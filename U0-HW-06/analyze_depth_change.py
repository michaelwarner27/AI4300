"""Replay the Stage A2 depth-2 matches and record depth-1 vs depth-2 decisions.

For every board on which the focal ``minimax_3`` agent is asked to move, this
script runs the *same* depth-limited minimax the harness uses -- one search at
depth 1 and one at depth 2 -- and records for each depth the chosen move, the
backed-up value, the margin over the runner-up, the work done, and the value
each depth assigns to the *other* depth's choice.

The result is the ``a2-depth-change.csv`` evidence for the report claim "in
this position depth changed the selected move / backed-up value". The replay is
faithful by construction: it uses ``JetanEnvironment``, ``RandomJetanAgent``,
and ``DepthLimitedMinimaxAgent`` themselves with the same seeds, depths, and
move limit as ``run_experiments.py a2``, so the recorded match result and plies
must match the corresponding rows in ``results/a2.csv`` once Stage A2 has been
run. The script prints that comparison when ``results/a2.csv`` exists.

Implementation note on the value extraction: ``DepthLimitedMinimaxAgent``
exposes only its best move and totals, not per-action backed-up values. The
root loop of ``choose_action`` is eight lines of code; replicating it here --
with the same strict ``>`` comparison that keeps the *first* maximal action --
lets this script report the values and margins without reimplementing the
minimax traversal, which remains entirely inside
:class:`~minimax_agent.DepthLimitedMinimaxAgent`. The replication is validated
on the first focal position of every match against ``choose_action`` itself,
and the search totals are read from the agent's private counters in exactly the
state ``choose_action`` leaves them.

The ``match_type`` column distinguishes the four canonical Stage A2
configurations (seeds 0, 1 x both colors) from optional fixed-seed extensions
that only add more decision points to the evidence sample.
"""

from __future__ import annotations

import csv
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from environment import JetanEnvironment, NullViewer
from jetan import JetanBoard, Move, Player, PieceType
from minimax_agent import DepthLimitedMinimaxAgent, SearchMetrics
from random_agent import RandomJetanAgent
from student_strategies import evaluate_position_3

SEEDS = (0, 1)
EXTRA_SEEDS = (2, 3)
COLORS = ("orange", "black")
MAX_PLIES = 100
FOCAL_DEPTH = 2

FIELDNAMES = (
    "match_type",
    "seed",
    "focal_color",
    "ply",
    "turn_number",
    "legal_moves",
    "d1_move",
    "d1_value",
    "d1_margin",
    "d1_generated",
    "d1_evaluated",
    "d1_captures",
    "d1_wins",
    "d2_move",
    "d2_value",
    "d2_margin",
    "d2_generated",
    "d2_evaluated",
    "d2_captures",
    "d2_wins",
    "changed",
    "d1_value_of_d2_move",
    "d2_value_of_d1_move",
)

SUMMARY_FIELDNAMES = (
    "match_type",
    "seed",
    "focal_color",
    "result",
    "reason",
    "plies",
    "focal_turns",
    "decisions_recorded",
    "moves_changed",
    "changed_fraction",
)


class _DepthProbe:
    """One depth-limited search, with per-action backed-up values exposed.

    ``DepthLimitedMinimaxAgent`` computes exactly this internally but keeps
    only the best action. To record the margin and the cross-depth evaluation
    of the opponent's choice, the root loop of ``choose_action`` is replicated
    here; the traversal itself still runs inside the agent's ``_value``.
    """

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.agent = DepthLimitedMinimaxAgent(
            f"probe-depth-{depth}", depth, evaluate_position_3
        )

    def analyse(self, board: JetanBoard) -> dict[str, Any] | None:
        actions = board.actions()
        if not actions:
            # The environment never calls the focal agent on a terminal board,
            # but guard anyway so the probe is total.
            return None
        perspective = board.player()
        # Mirror choose_action's counter setup so the search totals can be read
        # afterwards from the same private fields choose_action populates.
        self.agent._generated = len(actions)
        self.agent._evaluated = 0
        self.agent._maximum_depth = 0
        values: dict[Move, float] = {}
        for action in actions:
            values[action] = float(
                self.agent._value(
                    board.result(action),
                    self.depth - 1,
                    perspective,
                    current_depth=1,
                )
            )
        best, second = actions[0], None
        best_value, second_value = values[actions[0]], -math.inf
        for action in actions[1:]:
            value = values[action]
            # Strict ``>`` keeps the *first* maximal action, exactly as the agent
            # chooses in choose_action.
            if value > best_value:
                second, second_value = best, best_value
                best, best_value = action, value
            elif value > second_value:
                second, second_value = action, value
        metrics = SearchMetrics(
            self.agent._generated, self.agent._evaluated, self.agent._maximum_depth
        )
        captures = any(
            board.at(action.destination) is not None for action in (best,)
        )
        wins = (
            board.at(best.destination) is not None
            and board.at(best.destination).kind is PieceType.PRINCESS
        )
        return {
            "move": best,
            "value": best_value,
            "margin": best_value - second_value,
            "second_value": second_value,
            "generated": metrics.generated,
            "evaluated": metrics.evaluated,
            "maximum_depth": metrics.maximum_depth,
            "captures": captures,
            "wins": wins,
            "values": values,
        }

    def chosen_by_agent(self, board: JetanBoard) -> Move:
        """Authoritative choice from ``choose_action``, for validation only."""
        return self.agent.choose_action(board)


class _FocalProbeAgent:
    """Play minimax_3 at depth ``FOCAL_DEPTH`` while recording depth 1 and 2.

    ``choose_action`` is invoked by :class:`JetanEnvironment` exactly when the
    report needs a decision: never on a terminal board, with the board's ``ply``
    and the move count available as context.
    """

    def __init__(self, name: str, record: list[dict[str, Any]], seed: int,
                 color: str, match_type: str) -> None:
        self.name = name
        self._record = record
        self._seed = seed
        self._color = color
        self._match_type = match_type
        self._turn = 0
        self._inner = DepthLimitedMinimaxAgent(
            name, FOCAL_DEPTH, evaluate_position_3
        )
        self._probe_d1 = _DepthProbe(1)
        self._probe_d2 = _DepthProbe(2)
        self._validated_d1 = False
        self._validated_d2 = False

    def choose_action(self, board: JetanBoard) -> Move:
        self._turn += 1
        depth1 = self._probe_d1.analyse(board)
        depth2 = self._probe_d2.analyse(board)
        if depth1 is None or depth2 is None:
            raise RuntimeError("focal agent called on a terminal board")
        # Validate the replicated root loop against the authoritative agent on
        # the first decision of each match; one extra search each, then trust.
        for tag, probe, validated in (
            ("d1", self._probe_d1, self._validated_d1),
            ("d2", self._probe_d2, self._validated_d2),
        ):
            if not validated:
                authoritative = probe.chosen_by_agent(board)
                mine = depth1["move"] if tag == "d1" else depth2["move"]
                if authoritative != mine:
                    raise AssertionError(
                        f"{tag} replication diverged from choose_action: "
                        f"probe={mine} agent={authoritative} seed={self._seed} "
                        f"color={self._color} ply={board.ply}"
                    )
                if tag == "d1":
                    self._validated_d1 = True
                else:
                    self._validated_d2 = True
        d1_move = depth1["move"]
        d2_move = depth2["move"]
        row = {
            "match_type": self._match_type,
            "seed": self._seed,
            "focal_color": self._color,
            "ply": board.ply,
            "turn_number": self._turn,
            "legal_moves": board.legal_action_count(board.player()),
            "d1_move": str(d1_move),
            "d1_value": depth1["value"],
            "d1_margin": depth1["margin"],
            "d1_generated": depth1["generated"],
            "d1_evaluated": depth1["evaluated"],
            "d1_captures": depth1["captures"],
            "d1_wins": depth1["wins"],
            "d2_move": str(d2_move),
            "d2_value": depth2["value"],
            "d2_margin": depth2["margin"],
            "d2_generated": depth2["generated"],
            "d2_evaluated": depth2["evaluated"],
            "d2_captures": depth2["captures"],
            "d2_wins": depth2["wins"],
            "changed": int(d1_move != d2_move),
            "d1_value_of_d2_move": depth1["values"][d2_move],
            "d2_value_of_d1_move": depth2["values"][d1_move],
        }
        self._record.append(row)
        return self._inner.choose_action(board)


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _play(
    seed: int, color: str, match_type: str, record: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> None:
    if color == "orange":
        focal = _FocalProbeAgent("Orange Minimax 3", record, seed, color, match_type)
        orange, black = focal, RandomJetanAgent(seed, "Black Random")
    else:
        focal = _FocalProbeAgent("Black Minimax 3", record, seed, color, match_type)
        orange, black = RandomJetanAgent(seed, "Orange Random"), focal
    environment = JetanEnvironment(
        orange,
        black,
        viewer=NullViewer(),
        max_plies=MAX_PLIES,
        max_think_seconds=3600.0,
    )
    result = environment.run()
    decisions = [r for r in record if r["seed"] == seed
                 and r["focal_color"] == color]
    changed = sum(r["changed"] for r in decisions)
    summaries.append(
        {
            "match_type": match_type,
            "seed": seed,
            "focal_color": color,
            "result": result.winner.name if result.winner is not None else "DRAW",
            "reason": result.reason,
            "plies": result.plies,
            "focal_turns": focal._turn,
            "decisions_recorded": len(decisions),
            "moves_changed": changed,
            "changed_fraction": round(changed / len(decisions), 4) if decisions else 0.0,
        }
    )
    outcome = "win" if result.winner is not None else "draw"
    print(
        f"[{match_type:8}] seed={seed} color={color:6} -> {outcome:5} "
        f"plies={result.plies:3} reason={result.reason:10} "
        f"focal_turns={focal._turn:3} changed={changed}/{len(decisions)}",
        flush=True,
    )


def _verify_against_a2(
    summaries: list[dict[str, Any]], a2_path: Path
) -> None:
    """Compare canonical replayed matches to the recorded Stage A2 rows."""
    if not a2_path.is_file():
        print("\nresults/a2.csv not present yet -- run Stage A2, then rerun this "
              "script to verify the replay reproduces the A2 matches.")
        return
    with a2_path.open(newline="", encoding="utf-8") as source:
        a2: dict[str, dict[str, Any]] = {}
        for r in csv.DictReader(source):
            color = str(r["focal_color"])
            a2[f"{color}-{r[f'{color}_seed']}"] = r
    print("\nReplay vs results/a2.csv (canonical matches only):")
    mismatches = 0
    compared = 0
    for summary in summaries:
        if summary["match_type"] != "canonical":
            continue
        key = f"{summary['focal_color']}-{summary['seed']}"
        recorded = a2.get(key)
        if recorded is None:
            print(f"  seed={summary['seed']} color={summary['focal_color']}: "
                  "NOT in a2.csv")
            mismatches += 1
            continue
        compared += 1
        recorded_result = str(recorded["result"]).rstrip()
        replayed_result = str(summary["result"]).rstrip()
        agree = (
            recorded_result == replayed_result
            and int(recorded["plies"]) == int(summary["plies"])
        )
        if agree:
            print(f"  seed={summary['seed']} color={summary['focal_color']:6} OK "
                  f"(result={recorded_result} plies={summary['plies']})")
        else:
            print(f"  seed={summary['seed']} color={summary['focal_color']:6} "
                  f"MISMATCH recorded(result={recorded_result} "
                  f"plies={recorded['plies']}) vs replay(result={replayed_result} "
                  f"plies={summary['plies']})")
            mismatches += 1
    if compared and not mismatches:
        print("  -> all canonical replays reproduce results/a2.csv exactly.")
    elif mismatches:
        print(f"  -> {mismatches} canonical mismatch(es); investigate before "
              "using the a2-depth-change evidence.")


def _summary(rows: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> None:
    """Print the report-facing summary of depth-1 vs depth-2 disagreement."""
    print("\n=== Depth-1 vs depth-2 decision comparison ===")
    print(
        f"focal decisions recorded: {len(rows)} "
        f"(changed in {sum(r['changed'] for r in rows)} = "
        f"{100*sum(r['changed'] for r in rows)/len(rows):.1f}%)"
    )
    canonical = [r for r in rows if r["match_type"] == "canonical"]
    print(
        f"canonical decisions: {len(canonical)} "
        f"(changed in {sum(r['changed'] for r in canonical)})"
    )
    if not rows:
        return
    margins_d1 = [r["d1_margin"] for r in rows]
    margins_d2 = [r["d2_margin"] for r in rows]
    print(
        f"mean margin: depth-1 {sum(margins_d1)/len(margins_d1):+.6f}   "
        f"depth-2 {sum(margins_d2)/len(margins_d2):+.6f}"
    )
    changed_rows = [r for r in rows if r["changed"]]
    if changed_rows:
        # The most reportable disagreement: depth 2 most strongly downgrades the
        # depth-1 choice (large positive d2_value - d2_value_of_d1_move).
        ranked = sorted(
            changed_rows, key=lambda r: -(r["d2_value"] - r["d2_value_of_d1_move"])
        )
        best = ranked[0]
        print(f"\nStrongest disagreement (seed={best['seed']} color={best['focal_color']} "
              f"ply={best['ply']}, {best['legal_moves']} legal moves):")
        print(f"  depth-1: {best['d1_move']} value={best['d1_value']:+.6f} "
              f"(margin {best['d1_margin']:+.6f})")
        print(f"  depth-2: {best['d2_move']} value={best['d2_value']:+.6f} "
              f"(margin {best['d2_margin']:+.6f})")
        print(f"  depth-2's score of the depth-1 move: {best['d2_value_of_d1_move']:+.6f} "
              f"(downgrade {best['d2_value_of_d1_move']-best['d2_value']:+.6f})")
        print(f"  depth-1's score of the depth-2 move: {best['d1_value_of_d2_move']:+.6f}")
    print("\n=== Per-match summary ===")
    for summary in summaries:
        print(
            f"  {summary['match_type']:8} seed={summary['seed']} "
            f"color={summary['focal_color']:6} result={summary['result']:5} "
            f"reason={summary['reason']:10} plies={summary['plies']:3} "
            f"turns={summary['focal_turns']:3} "
            f"changed={summary['moves_changed']}/{summary['decisions_recorded']}"
        )


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/a2-depth-change.csv"),
        help="raw decision-comparison CSV (default: results/a2-depth-change.csv)",
    )
    parser.add_argument(
        "--no-extra-seeds",
        action="store_true",
        help="record only the four canonical Stage A2 configurations",
    )
    parser.add_argument("--a2-csv", type=Path, default=Path("results/a2.csv"))
    args = parser.parse_args(argv)

    started = time.perf_counter()
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    extra_seeds = () if args.no_extra_seeds else EXTRA_SEEDS

    matches = [(seed, color, "canonical") for seed in SEEDS for color in COLORS]
    matches += [(seed, color, "extra") for seed in extra_seeds for color in COLORS]
    for seed, color, match_type in matches:
        print(f"--- replay seed={seed} color={color} match_type={match_type} ---",
              flush=True)
        _play(seed, color, match_type, rows, summaries)

    _write_csv(output, FIELDNAMES, rows)
    summary_path = output.with_name(f"{output.stem}-summary{output.suffix}")
    _write_csv(
        summary_path,
        SUMMARY_FIELDNAMES,
        [{field: summary[field] for field in SUMMARY_FIELDNAMES}
         for summary in summaries],
    )
    print(f"\nRaw comparison: {output}")
    print(f"Per-match summary: {summary_path}")
    _summary(rows, summaries)
    _verify_against_a2(summaries, args.a2_csv)
    print(f"\ncompleted in {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())