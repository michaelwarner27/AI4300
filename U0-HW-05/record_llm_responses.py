"""Record live LLM responses and their parser verdicts for prompt-revision evidence.

The assignment requires at least one documented prompt revision, justified by
inspecting what the model actually returned and which validation rule rejected
it. The supplied frameworks count rejections (``fallback_calls``) but discard
the response text, so that evidence does not exist unless it is captured at the
time of the call. This script captures it.

It also measures the thing the report calls *sensor parity*: the signed error
between the model's score and the deterministic fallback's score on the same
position, from the same perspective. That number is what says whether the model
is approximating the rubric it was given or substituting a different objective,
and it is the evidence a prompt revision has to move.

Everything here is offline-testable. The probes accept any client, so the test
suite drives them with :class:`llm_client.ScriptedClient`; only ``main`` builds
a live client. No supplied framework file is modified.

Usage (live, after the endpoint is reachable)::

    python record_llm_responses.py evaluate --live --out results/pilot-eval-v1.csv
    python record_llm_responses.py move --live --out results/pilot-move-v1.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from jetan import JetanBoard, Player
from llm_client import ChatClient, OpenAICompatibleClient
from student_strategies import (
    EVALUATION_PROMPT_VERSION,
    MOVE_PROMPT_VERSION,
    build_evaluation_prompt,
    build_move_prompt,
    choose_fallback,
    evaluate_position_3,
    parse_evaluation_response,
    parse_move_response,
)

#: Position tokens are written as ``column letter + row``; kept here so the CSV
#: is readable without re-deriving the convention.
DEFAULT_MODEL = "Gemma-4-26B-A4B-it-oQ4e-mtp"
DEFAULT_ENDPOINT = "http://golem:8000/v1"

EVALUATE_FIELDS = (
    "modality",
    "index",
    "ply",
    "side_to_move",
    "perspective",
    "prompt_version",
    "system_chars",
    "user_chars",
    "reference_score",
    "model_response",
    "verdict",
    "category",
    "parsed_value",
    "signed_error",
    "abs_error",
    "latency_seconds",
)

MOVE_FIELDS = (
    "modality",
    "index",
    "ply",
    "side_to_move",
    "legal_action_count",
    "prompt_version",
    "system_chars",
    "user_chars",
    "model_response",
    "verdict",
    "category",
    "parsed_move",
    "fallback_move",
    "chose_fallback",
    "latency_seconds",
)


class ChatClientLike(Protocol):
    """The subset of :class:`llm_client.ChatClient` this script needs."""

    def chat(self, messages: Sequence[dict[str, str]]) -> str: ...


@dataclass
class Exchange:
    """One prompt, one response, and the parser's verdict on that response."""

    modality: str
    index: int
    prompt_version: str
    side_to_move: str
    system_chars: int
    user_chars: int
    model_response: str
    verdict: str
    category: str
    parsed_value: str
    latency_seconds: float
    extra: dict[str, object] = field(default_factory=dict)

    def as_row(self) -> dict[str, object]:
        row: dict[str, object] = {
            "modality": self.modality,
            "index": self.index,
            "prompt_version": self.prompt_version,
            "side_to_move": self.side_to_move,
            "system_chars": self.system_chars,
            "user_chars": self.user_chars,
            "model_response": self.model_response,
            "verdict": self.verdict,
            "category": self.category,
            "parsed_value": self.parsed_value,
            "latency_seconds": f"{self.latency_seconds:.3f}",
        }
        row.update(self.extra)
        return row


def verdict_of(response: str, parse, actions=None) -> tuple[str, str, object]:
    """Return ``(verdict, category, value)`` for one raw model response.

    ``verdict`` is ``"accepted"`` or ``"rejected"``. ``category`` is the
    ``REJECT_*`` prefix the parser raised, or ``"-"`` on acceptance. The
    contract is that a rejection *is* the signal: both supplied frameworks count
    a raise as a fallback call, so a rejection here is exactly what would have
    become a fallback in a real match.
    """
    try:
        value = parse(response, actions) if actions is not None else parse(response)
    except Exception as error:  # noqa: BLE001 - any failure is a rejection
        message = str(error)
        return "rejected", message.split(":", 1)[0], None
    return "accepted", "-", value


def sample_cutoff_positions(games: int, plies: int, seed: int) -> list[JetanBoard]:
    """Return realistic depth-1 cutoff positions drawn from random games.

    A cutoff position is the successor of a real game position, not a random
    board, so the sampled set has the piece counts, developed formations, and
    mobility a depth-1 search actually encounters.

    Positions are drawn round-robin across game depth rather than as a prefix.
    A prefix would be filled entirely by the successors of the opening position,
    which are identical for every seed and every run, so a pilot built on one
    would measure the opening and nothing else. Interleaving by ply guarantees
    the sample spans early, middle, and late positions, where the model is under
    very different pressure and where a prompt defect is most likely to show.
    """
    rng = random.Random(seed)
    buckets: list[list[JetanBoard]] = [[] for _ in range(plies)]
    for _game in range(games):
        board = JetanBoard.initial()
        for depth in range(plies):
            actions = board.actions()
            if not actions or board.is_terminal():
                break
            for action in actions[:20]:
                successor = board.result(action)
                if not successor.is_terminal():
                    buckets[depth].append(successor)
            board = board.result(rng.choice(actions))
    positions: list[JetanBoard] = []
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if index < len(bucket):
                positions.append(bucket[index])
    return positions


def probe_evaluator(
    client: ChatClientLike,
    boards: Sequence[JetanBoard],
    perspective: Player | None = None,
) -> list[Exchange]:
    """Score each board through the LLM cutoff evaluator and record the verdict.

    When ``perspective`` is ``None`` each board is probed from both sides, which
    is how a sign error becomes visible: a model that scores the side to move
    instead of the requested perspective produces errors of opposite sign on the
    two probes of the same board, and their sum is far from zero.
    """
    exchanges: list[Exchange] = []
    for index, board in enumerate(boards):
        sides = (perspective, perspective.opponent) if perspective else (
            Player.ORANGE,
            Player.BLACK,
        )
        for view in sides:
            messages = build_evaluation_prompt(board, view)
            start = time.perf_counter()
            try:
                response = client.chat(messages)
                transport = ""
            except Exception as error:  # noqa: BLE001
                response = f"<transport error: {type(error).__name__}>"
                transport = type(error).__name__
            latency = time.perf_counter() - start
            verdict, category, value = verdict_of(
                response, parse_evaluation_response
            )
            reference = evaluate_position_3(board, view)
            if isinstance(value, float):
                signed = value - reference
                parsed: object = f"{value:.6f}"
                signed_text = f"{signed:+.6f}"
                absolute = f"{abs(signed):.6f}"
            else:
                signed, parsed, absolute, signed_text = "", "", "", ""
            exchanges.append(
                Exchange(
                    modality="cutoff_evaluator",
                    index=index,
                    prompt_version=EVALUATION_PROMPT_VERSION,
                    side_to_move=board.player().name,
                    system_chars=len(messages[0]["content"]),
                    user_chars=len(messages[1]["content"]),
                    model_response=response,
                    verdict=verdict,
                    category=category or transport,
                    parsed_value=parsed,
                    latency_seconds=latency,
                    extra={
                        "ply": board.ply,
                        "side_to_move": board.player().name,
                        "perspective": view.name,
                        "reference_score": f"{reference:.6f}",
                        "signed_error": signed_text,
                        "abs_error": absolute,
                    },
                )
            )
    return exchanges


def probe_direct(
    client: ChatClientLike,
    boards: Sequence[JetanBoard],
    history: int = 0,
) -> list[Exchange]:
    """Request one move per board through the direct agent and record the verdict."""
    exchanges: list[Exchange] = []
    for index, board in enumerate(boards):
        actions = board.actions()
        if not actions:
            continue
        recent = tuple(board_history(board, history))
        messages = build_move_prompt(board, actions, recent)
        start = time.perf_counter()
        try:
            response = client.chat(messages)
            transport = ""
        except Exception as error:  # noqa: BLE001
            response = f"<transport error: {type(error).__name__}>"
            transport = type(error).__name__
        latency = time.perf_counter() - start
        verdict, category, value = verdict_of(
            response, parse_move_response, actions
        )
        fallback = choose_fallback(board, actions)
        exchanges.append(
            Exchange(
                modality="direct_move",
                index=index,
                prompt_version=MOVE_PROMPT_VERSION,
                side_to_move=board.player().name,
                system_chars=len(messages[0]["content"]),
                user_chars=len(messages[1]["content"]),
                model_response=response,
                verdict=verdict,
                category=category or transport,
                parsed_value="" if value is None else str(value),
                latency_seconds=latency,
                extra={
                    "ply": board.ply,
                    "legal_action_count": len(actions),
                    "parsed_move": "" if value is None else str(value),
                    "fallback_move": str(fallback),
                    "chose_fallback": "n/a" if value is None else (
                        "yes" if value == fallback else "no"
                    ),
                },
            )
        )
    return exchanges


def board_history(board: JetanBoard, count: int) -> list[str]:
    """Return up to ``count`` placeholder recent-move tokens for a probe.

    A sampled cutoff position has no recoverable move history, so the probe
    passes synthetic tokens. That is sufficient for measuring the *response
    contract* -- the question the pilot answers first -- and it keeps the
    history line non-empty so its effect on the prompt is exercised.
    """
    return [f"a{ply}-b{ply}" for ply in range(count)]


def summarise(exchanges: Sequence[Exchange]) -> dict[str, object]:
    """Return aggregate counts, mean latency, and mean absolute score error."""
    accepted = [e for e in exchanges if e.verdict == "accepted"]
    categories: dict[str, int] = {}
    for exchange in exchanges:
        if exchange.verdict == "rejected":
            key = exchange.category or "uncategorised"
            categories[key] = categories.get(key, 0) + 1
    errors = [
        abs(float(e.extra["signed_error"]))
        for e in exchanges
        if e.extra.get("signed_error") not in ("", None)
    ]
    latencies = [e.latency_seconds for e in exchanges]
    return {
        "requests": len(exchanges),
        "accepted": len(accepted),
        "rejected": len(exchanges) - len(accepted),
        "acceptance_rate": (
            f"{len(accepted) / len(exchanges):.1%}" if exchanges else "n/a"
        ),
        "mean_latency_seconds": (
            f"{sum(latencies) / len(latencies):.3f}" if latencies else "n/a"
        ),
        "max_latency_seconds": f"{max(latencies):.3f}" if latencies else "n/a",
        "mean_abs_error": f"{sum(errors) / len(errors):.4f}" if errors else "n/a",
        "categories": dict(sorted(categories.items(), key=lambda kv: -kv[1])),
    }


def write_csv(exchanges: Sequence[Exchange], path: str) -> None:
    """Write exchanges to ``path``, retaining every row including failures.

    Rejected rows are kept deliberately: the report's rejection table is built
    from them, so dropping them would destroy the evidence the prompt revision
    has to be justified against.
    """
    if not exchanges:
        raise ValueError("refusing to write a header-only CSV with no exchanges")
    fields = (
        EVALUATE_FIELDS
        if exchanges[0].modality == "cutoff_evaluator"
        else MOVE_FIELDS
    )
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for exchange in exchanges:
            writer.writerow(exchange.as_row())


def build_live_client(seed: int, model: str, endpoint: str, temperature: float):
    """Return a client for the course endpoint, or raise if it is not configured.

    The API key is read from the environment and never written to the CSV, the
    summary, or any other artefact.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY must be set for a live probe")
    if not endpoint.startswith("https://") and not _is_local_endpoint(endpoint):
        raise SystemExit(
            "--endpoint must use HTTPS, or name the course golem host"
        )
    return OpenAICompatibleClient(
        model=model,
        api_key=api_key,
        base_url=endpoint,
        temperature=temperature,
        seed=seed,
    )


def _is_local_endpoint(endpoint: str) -> bool:
    host = endpoint.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return host in {"golem", "localhost", "127.0.0.1", "::1"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("evaluate", "move"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--plies", type=int, default=12)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--history", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--endpoint", default=os.environ.get("OPENAI_BASE_URL", DEFAULT_ENDPOINT)
    )
    parser.add_argument(
        "--temperature", type=float, default=float(os.environ.get("OPENAI_TEMPERATURE", "0"))
    )
    args = parser.parse_args(argv)

    boards = sample_cutoff_positions(args.games, args.plies, args.seed)[: args.limit]
    if not boards:
        raise SystemExit("no cutoff positions were sampled")

    if args.live:
        client: ChatClientLike = build_live_client(
            args.seed, args.model, args.endpoint, args.temperature
        )
    else:
        raise SystemExit(
            "refusing to fabricate responses: pass --live to contact the endpoint, "
            "or drive probe_evaluator/probe_direct with ScriptedClient from a test"
        )

    if args.mode == "evaluate":
        exchanges = probe_evaluator(client, boards)
    else:
        exchanges = probe_direct(client, boards, history=args.history)

    write_csv(exchanges, args.out)
    summary = summarise(exchanges)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote {len(exchanges)} rows to {args.out}")
    if summary["rejected"]:
        print("\nRepresentative rejected responses:")
        shown = 0
        for exchange in exchanges:
            if exchange.verdict != "rejected" or shown >= 5:
                continue
            print(f"  [{exchange.category}] {exchange.model_response[:160]!r}")
            shown += 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
