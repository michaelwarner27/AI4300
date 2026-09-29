"""Implement student evaluation, prompting, parsing, and fallback functions."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from jetan import BOARD_SIZE, JetanBoard, Move, PieceType, Player

# ---------------------------------------------------------------------------
# Scale shared by all three deterministic evaluators
# ---------------------------------------------------------------------------
#
# Every deterministic evaluator below follows the same two-step construction:
#
#   1. Combine a handful of *antisymmetric feature terms* into one signed raw
#      score measured in material-equivalent units, where 1.7 is one Padwar.
#      A term is antisymmetric when it is computed as a difference
#      ``f(board, player) - f(board, player.opponent)``. That form makes the
#      score reverse sign under a perspective change for free, so the
#      required perspective reversal needs no special case in any evaluator.
#
#   2. Squash the raw score through ``_squash`` into the required
#      ``[-0.99, 0.99]`` range. ``math.tanh`` is odd, so the squashed score is
#      antisymmetric too, and it is finite for every finite raw input.

#: Material value of one piece.
#:
#: Padwar, Warrior, Panthan, Dwar, Thoat, and Flier are the values published in
#: the 2017 course C++ source. That source assigns **no** value to Chief or
#: Princess, so those two entries are derived here from the terminal rules in
#: :meth:`jetan.JetanBoard.result`:
#:
#: * capturing a Princess ends the match immediately for the capturer, and
#: * capturing a Chief ends the match only when the capturing piece is itself a
#:   Chief, making a Chief capture conditional while a Princess capture is not.
#:
#: The six supplied values form three tiers that roughly double: ``1.7-2.0``,
#: then ``4.9-5.6``, then ``9.5``. Chief is therefore set to twice the Flier,
#: ``19.0``, which continues that pattern. Princess is ``2.0`` above Chief --
#: exactly one Panthan, the cheapest tradeable unit in the table -- which
#: encodes the unconditional-versus-conditional distinction above. Together the
#: two royals are worth ``40.0``, about 63 percent of the ``63.2`` held by one
#: complete non-royal army, so a royal dominates tactics without erasing every
#: other consideration.
PIECE_VALUES: dict[PieceType, float] = {
    PieceType.PADWAR: 1.7,
    PieceType.WARRIOR: 1.9,
    PieceType.PANTHAN: 2.0,
    PieceType.DWAR: 4.9,
    PieceType.THOAT: 5.6,
    PieceType.FLIER: 9.5,
    PieceType.CHIEF: 19.0,
    PieceType.PRINCESS: 21.0,
}

#: Raw-score magnitude that squashes to roughly 0.75. Calibrated in the same
#: units as :data:`PIECE_VALUES` so that a realistic early-game material
#: difference of one Flier (9.5) lands at 0.44 and a two-royal advantage (40)
#: lands at 0.95, giving good resolution across the range where depth-1
#: decisions are actually made.
SCORE_SCALE = 20.0

#: Required bound on every returned score. ``_squash`` saturates at exactly
#: ``+/-SCORE_LIMIT`` for extreme input, so this is a closed-interval bound.
SCORE_LIMIT = 0.99

#: Evaluation 2 coefficient. Material units per legal-action advantage.
#: Bounded by design: a 30-move mobility swing is worth 6.0 units, which is
#: less than one Flier, so mobility can never outbid a material gain.
MOBILITY_UNIT = 0.2

#: Evaluation 2 coefficient. Material units per piece per full advance from the
#: piece's home rank to the opposing home rank. A completely advanced army is
#: worth ``20 * 0.15 = 3.0`` units, less than one Thoat.
ADVANCE_UNIT = 0.15

#: Evaluation 3 coefficient. Fraction of a threatened piece's value credited
#: for attacking it. Below 1.0 so that a position where both armies attack each
#: other equally is not scored as though the material advantage had vanished.
THREAT_WEIGHT = 0.5

#: Evaluation 3 coefficient. Material units for the opponent's Princess having
#: spent its single board-wide escape while the acting player's has not.
ESCAPE_UNIT = 1.0


def _squash(raw: float) -> float:
    """Map a signed material-equivalent score into ``[-0.99, 0.99]``.

    ``math.tanh`` is odd, so ``_squash(-x) == -_squash(x)`` and perspective
    reversal is preserved to within floating-point rounding. Every finite raw
    input gives a finite result. ``tanh`` reaches exactly 1.0 in IEEE double
    once ``|raw|`` exceeds roughly 500, so the function saturates at exactly
    ``+/-0.99``; that is the closed-interval bound the framework requires, and
    no real position approaches it, since a full army is worth about 101.
    """
    return SCORE_LIMIT * math.tanh(raw / SCORE_SCALE)


def _home_row(player: Player) -> int:
    """Return the ``y`` coordinate of ``player``'s own back rank."""
    return 0 if player is Player.ORANGE else BOARD_SIZE - 1


def _forward(player: Player) -> int:
    """Return the ``y`` direction in which ``player`` advances.

    Orange's Panthan steps are defined with increasing ``y`` and Black's with
    decreasing ``y`` in :mod:`jetan`, so this matches the rule engine exactly.
    """
    return 1 if player is Player.ORANGE else -1


def _advance_progress(y: int, player: Player) -> float:
    """Return how far a piece on row ``y`` has advanced, from 0.0 to 1.0.

    ``0.0`` on the piece's own home rank and ``1.0`` on the opposing home rank.
    """
    return (y - _home_row(player)) * _forward(player) / (BOARD_SIZE - 1)


# ---------------------------------------------------------------------------
# Antisymmetric feature terms
# ---------------------------------------------------------------------------


def _material_term(board: JetanBoard, player: Player) -> float:
    """Return the signed material advantage in material units.

    The sum of :data:`PIECE_VALUES` over ``player``'s surviving pieces minus
    the same sum over the opponent's. ``board.pieces`` walks cells in index
    order for both players, so the two per-player sums are bit-identical
    between the two perspective calls and the difference negates exactly.
    """
    mine = sum(PIECE_VALUES[piece.kind] for _, piece in board.pieces(player))
    theirs = sum(PIECE_VALUES[piece.kind] for _, piece in board.pieces(player.opponent))
    return mine - theirs


def _mobility_term(board: JetanBoard, player: Player) -> float:
    """Return the legal-action advantage scaled by :data:`MOBILITY_UNIT`.

    Counts legal moves for either player without disturbing the turn. A side
    with no legal move loses the match under the supplied rules, so a large
    positive count is genuinely defensive and not merely cosmetic.
    """
    return MOBILITY_UNIT * (
        board.legal_action_count(player) - board.legal_action_count(player.opponent)
    )


def _advance_term(board: JetanBoard, player: Player) -> float:
    """Return the signed home-rank-to-home-rank advance advantage.

    Each piece contributes :data:`ADVANCE_UNIT` times its advance progress. The
    Princess is excluded: under the supplied rules it may not move onto an
    attacked square, so rewarding its advance would push it into danger, which
    is the opposite of what the term is for.
    """
    total = 0.0
    for (_, y), piece in board.pieces(player):
        if piece.kind is not PieceType.PRINCESS:
            total += _advance_progress(y, player)
    for (_, y), piece in board.pieces(player.opponent):
        if piece.kind is not PieceType.PRINCESS:
            total -= _advance_progress(y, player.opponent)
    return ADVANCE_UNIT * total


def _threat_term(board: JetanBoard, player: Player) -> float:
    """Return the signed attack advantage scaled by :data:`THREAT_WEIGHT`.

    ``my_threat`` credits the value of opponent pieces standing on squares the
    acting player's pieces attack; ``their_threat`` does the same in reverse.
    The term is their *difference*, which makes it antisymmetric while still
    distinguishing a free capture from a piece that is merely contested.

    Because the Princess is worth 20.0, this one term already carries most of
    the royal-safety signal: a Princess standing on an attacked square is
    counted in ``their_threat`` and is therefore penalised, and an enemy
    Princess that can be taken is counted in ``my_threat`` and rewarded.

    Cost note: :meth:`jetan.JetanBoard.attacked_locations` is memoized per
    board, and :meth:`jetan.JetanBoard.legal_action_count` already materializes
    both sides' attacked sets while enumerating moves. Once
    :func:`_mobility_term` has run, this term adds only two set lookups.
    """
    my_attacks = board.attacked_locations(player)
    their_attacks = board.attacked_locations(player.opponent)
    my_threat = sum(
        PIECE_VALUES[piece.kind]
        for location, piece in board.pieces(player.opponent)
        if location in my_attacks
    )
    their_threat = sum(
        PIECE_VALUES[piece.kind]
        for location, piece in board.pieces(player)
        if location in their_attacks
    )
    return THREAT_WEIGHT * (my_threat - their_threat)


def _escape_term(board: JetanBoard, player: Player) -> float:
    """Return the signed Princess-escape advantage.

    A Princess may once per match make a move of more than 3 squares in a line,
    and doing so spends its escape permanently (``jetan.py:271`` tests
    ``max(dx, dy) > 3`` on the move, not on the Princess being attacked). Once
    spent the escape is gone for the rest of the match and retreating cannot
    restore it, so this is a genuinely depleting resource that no other term
    observes. A positive score means the opponent's Princess has spent its
    escape and the acting player's has not.

    Because the escape is consumed by a long move, a Princess that has spent it
    is a Princess that has already committed to the attack and can no longer
    retreat to safety in one step. That is the tactical fact this term prices.

    ``used_escape`` is indexed by ``Player.ORANGE - 1`` and
    ``Player.BLACK - 1`` in :mod:`jetan`, which the ``IntEnum`` values 1 and 2
    satisfy directly.
    """
    spent_by_opponent = int(board.used_escape[player.opponent - 1])
    spent_by_player = int(board.used_escape[player - 1])
    return ESCAPE_UNIT * (spent_by_opponent - spent_by_player)


# ---------------------------------------------------------------------------
# The three deterministic evaluation functions
# ---------------------------------------------------------------------------


def evaluate_position_1(board: JetanBoard, perspective: Player) -> float:
    """Evaluation 1, the material baseline.

    ``0.99 * tanh(material_advantage / 20)``

    *Information used:* the surviving count of each piece type.
    *Intentionally ignored:* mobility, position, whether any piece is attacked,
    and Princess escape state.
    *Scaling and range:* the raw term is a material difference measured in the
    same units as :data:`PIECE_VALUES`, so it is unbounded in principle but in
    practice lies between ``-101.2`` and ``+101.2``, the value of one complete
    non-royal army. ``_squash`` maps that onto ``[-0.99, 0.99]`` with
    :data:`SCORE_SCALE` ``20.0``, which puts one Padwar at 0.084, one Flier at
    0.44, and a two-royal advantage at 0.95. The transform is smooth and has
    no flat region until ``|raw| > 500``, far outside any reachable position,
    so there is no resolution cliff inside the range that matters.
    *Why it should track utility:* winning is mostly a matter of surviving with
    more material than the opponent, and a material edge compounds across
    plies.
    *Cost:* one pass over 100 cells; the cheapest of the three.
    *Known weakness:* it is completely blind to danger. It will trade its
    Princess for a marginal gain and it will not notice that its own pieces are
    en prise, which is what Evaluation 2 and 3 are designed to fix.
    """
    return _squash(_material_term(board, perspective))


def evaluate_position_2(board: JetanBoard, perspective: Player) -> float:
    """Evaluation 2, adding mobility and advance to the material baseline.

    ``0.99 * tanh((material + 0.2 * move_advantage
                   + 0.15 * advance_advantage) / 20)``

    *Revision from Evaluation 1:* Evaluation 1 values only what is on the board,
    so it is indifferent between a piece that is jammed against its own back
    rank and a piece that controls the centre. This version adds a legal-action
    count term and a home-rank advance term, both in material-equivalent units,
    so the agent prefers positions it can keep playing in and that carry its
    pieces toward the opposing back rank where captures happen.
    *Scaling and range:* all three terms are expressed in material-equivalent
    units and share Evaluation 1's ``/20`` and ``0.99 * tanh(.)`` pipeline, so
    the output range is unchanged. The additions are deliberately bounded below
    the value of a single piece: a 30-move mobility swing is worth 6.0 units,
    less than one Flier, and a fully advanced army is worth 3.0 units, less than
    one Thoat. Neither addition can on its own outbid a material gain, so this
    version re-ranks material-equal positions without ever overruling a real
    material difference.

    *Why it should track utility:* mobility and advance are the standard
    activity and progress proxies from the game-playing literature; together
    they break ties between material-equal positions in the direction that
    creates more future options.
    *Cost:* one pass over 100 cells plus two legal-move enumerations, which
    :mod:`jetan` memoizes per board.
    *Known weakness:* it still has no notion of attack. It can win material
    races while marching into attack, and it cannot distinguish a capture that
    is available from one that is merely threatened. Evaluation 3 addresses
    exactly this.

    *Measured quirk worth recording:* the legal-action count is dominated by
    the Princess, which accounts for 27 of the 52 legal moves in the initial
    position, against 16 for a lone Flier and 8 for a lone Padwar on an open
    board. The term therefore doubles as a partial measure of how much safe
    space the Princess has, so a safety signal arrived earlier and less
    explicitly than intended. Evaluation 3 keeps this term and adds explicit
    royal safety on top, which leaves the Princess weighted twice; that overlap
    is recorded as a known weakness of Evaluation 3.
    """
    return _squash(
        _material_term(board, perspective)
        + _mobility_term(board, perspective)
        + _advance_term(board, perspective)
    )


def evaluate_position_3(board: JetanBoard, perspective: Player) -> float:
    """Evaluation 3, adding attack geometry and royal safety.

    ``0.99 * tanh((material + 0.2 * move_advantage
                   + 0.15 * advance_advantage
                   + 0.5 * threat_advantage
                   + 1.0 * escape_advantage) / 20)``

    *Revision from Evaluation 2:* Evaluation 2 has no way to tell a capture that
    is available from a capture that is merely threatened, and it cannot see an
    attacked Princess. This version adds a threat term -- the difference
    between the value of the opponent's pieces the acting player attacks and
    the value of the acting player's pieces the opponent attacks -- plus a term
    for the opponent's spent Princess escape. The threat term prices a Princess
    at its full 20.0, so royal safety and winning captures emerge from one
    mechanism instead of two overlapping ones.

    *Why it should track utility:* it is the only one of the three that
    represents the two ways a position is actually lost in Jetan, an attacked
    Princess and an exhausted escape, so it is the version most able to
    distinguish a winning position from a nominally even one.
    *Scaling and range:* unchanged again; both new terms are in
    material-equivalent units and share the ``/20`` and ``0.99 * tanh(.)``
    pipeline, so the output is bounded by ``[-0.99, 0.99]``. The threat term is
    the only addition that can be large in either direction, because it sums
    piece values: two armies attacking each other symmetrically would put
    ``+/-101.2`` on opposite sides of the difference. In practice the Jetan step
    sets make that cancellation exact, so the term is usually near zero and
    occasionally decisive. The escape term is bounded by ``+/-1.0``, under one
    Padwar, which makes it a tie-breaker rather than a driver.
    *Cost:* as Evaluation 2, plus two memoized set lookups; the attacked sets
    are already computed by the mobility term.
    *Known weakness:* the threat term is myopic, and it fires less often than
    it first appears. Every non-Princess step set in :mod:`jetan` is closed
    under step negation, so a piece always attacks a square it could also be
    attacked from, and two pieces of equal reach that threaten each other
    cancel exactly in the difference. The term therefore does real work mainly
    when pieces of *different* reach interact -- a Flier bearing down on a
    Padwar that cannot answer -- or when a blocked intermediate square breaks
    the symmetry. It also double counts a piece that is both present and
    attacked, and because :func:`_mobility_term` already carries a partial
    Princess-safety signal, an attacked Princess is penalized twice. There is
    no concept of shelter or of supporting a threatened piece with a second
    attacker, so three-ply tactics remain invisible to it.
    """
    return _squash(
        _material_term(board, perspective)
        + _mobility_term(board, perspective)
        + _advance_term(board, perspective)
        + _threat_term(board, perspective)
        + _escape_term(board, perspective)
    )


# ---------------------------------------------------------------------------
# LLM strategy functions
# ---------------------------------------------------------------------------
#
# Rejection contract
# ------------------
# Both supplied LLM frameworks wrap the student prompt/call/parse step in a
# bare ``except Exception`` (``llm_evaluation.py:64`` and
# ``direct_llm_agent.py:68``) and route to the deterministic fallback when it
# fires. Raising is therefore the framework's rejection signal: there is no
# sentinel return value and no error-code protocol to negotiate, and a parser
# that returns a quiet default on bad input would be silently wrong rather than
# safely wrong. Every rejection below raises ``ValueError`` whose message
# begins with one of the ``REJECT_*`` category names below followed by ``": "``.
#
# Those category prefixes are load-bearing rather than cosmetic. The pilot
# recorder in ``record_llm_responses.py`` splits each rejection message on the
# first ``": "`` to attribute a live rejected response to a validation rule,
# which is exactly the evidence the report's rejection table is built from. If
# the prefixes are removed, that evidence degrades to a single opaque failure
# bucket.
#
# Model text is treated as untrusted data throughout
# -------------------------------------------------
# No function in this file ever calls ``eval``, ``exec``, ``compile``,
# ``getattr``, ``__import__``, or ``pickle`` on model output, and no model
# output is ever used to mutate a board. Model text reaches the game through
# exactly two doors, both of which are total functions from a closed set:
#
#   * :func:`parse_evaluation_response` returns a ``float`` that must satisfy
#     ``-0.99 <= score <= 0.99``; and
#   * :func:`parse_move_response` returns a ``Move`` that is looked up in a
#     ``{str(move): move}`` table built from the engine's own ``board.actions()``.
#
# The move parser is a *dictionary lookup*, not a coordinate parse. Model text
# is used only as a hash key against a table the rule engine produced, so an
# arbitrary string from the model can at worst fail to be a key. It is never
# decomposed into ``(x, y)`` integers, so no model-chosen coordinate can reach
# the board. ``jetan.JetanBoard.result`` independently re-validates the move
# against ``board.actions()`` on every application, and ``DirectLLMAgent``
# re-checks membership at ``direct_llm_agent.py:66`` and ``:71``, so the engine
# refuses an illegal move even if every layer above it were bypassed.

#: Rejection categories. Each prefixes a ``ValueError`` message, separated from
#: the human-readable detail by the first ``": "``.
REJECT_EMPTY = "empty_response"
REJECT_NOT_A_STRING = "not_a_string"
REJECT_MULTIPLE_LINES = "multiple_lines"
REJECT_INVALID_JSON = "invalid_json"
REJECT_DUPLICATE_KEY = "duplicate_key"
REJECT_NON_FINITE_LITERAL = "non_finite_literal"
REJECT_SURROUNDING_CONTENT = "surrounding_content"
REJECT_NOT_AN_OBJECT = "not_an_object"
REJECT_MISSING_KEY = "missing_key"
REJECT_ADDITIONAL_KEY = "additional_key"
REJECT_WRONG_TYPE = "wrong_type"
REJECT_NOT_FINITE = "not_finite"
REJECT_OUT_OF_RANGE = "out_of_range"
REJECT_ILLEGAL_MOVE = "illegal_move"

#: Every category name, for tests and for the pilot recorder's summary table.
REJECTION_CATEGORIES: tuple[str, ...] = (
    REJECT_EMPTY,
    REJECT_NOT_A_STRING,
    REJECT_MULTIPLE_LINES,
    REJECT_INVALID_JSON,
    REJECT_DUPLICATE_KEY,
    REJECT_NON_FINITE_LITERAL,
    REJECT_SURROUNDING_CONTENT,
    REJECT_NOT_AN_OBJECT,
    REJECT_MISSING_KEY,
    REJECT_ADDITIONAL_KEY,
    REJECT_WRONG_TYPE,
    REJECT_NOT_FINITE,
    REJECT_OUT_OF_RANGE,
    REJECT_ILLEGAL_MOVE,
)


class _ContractViolation(ValueError):
    """A response already rejected by a JSON hook, carrying its own category.

    Subclasses :class:`ValueError` so the supplied frameworks still catch it.
    It exists only to let :func:`_decode_single_object` distinguish a category
    raised deliberately by a hook from an incidental ``json`` decode error, so
    that ``duplicate_key`` is not reported as ``invalid_json``.
    """


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Return a dict from JSON object pairs, refusing any repeated key.

    ``json.loads`` and ``raw_decode`` both silently keep the *last* value for a
    duplicated key, so ``{"score": 0.1, "score": 0.9}`` would otherwise parse
    cleanly as ``0.9``. A model that emits a corrected value after an earlier
    one would silently have its correction discarded, or the reverse. The
    assignment requires duplicate keys to be rejected, and this hook is the only
    place that can do it without re-implementing the decoder.
    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _ContractViolation(
                f"{REJECT_DUPLICATE_KEY}: {key!r} appears more than once"
            )
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    """Refuse the ``NaN``, ``Infinity``, and ``-Infinity`` literals.

    These are *not* JSON, but Python's ``json`` module accepts them by default
    in both directions: it will emit them from ``float('nan')`` and it will read
    them from input. Left alone, ``{"score": NaN}`` would decode successfully and
    the only thing stopping it from reaching the agent would be a later
    finiteness check, which is a weaker place to catch it.
    """
    raise _ContractViolation(f"{REJECT_NON_FINITE_LITERAL}: {token}")


_STRICT_DECODER = json.JSONDecoder(
    object_pairs_hook=_reject_duplicate_keys,
    parse_constant=_reject_constant,
)


def _decode_single_object(response: str, key: str) -> object:
    """Return the single value of the required ``key`` in a one-key JSON object.

    This is the shared front half of both response contracts. It enforces, in
    order, every rule that both modalities share:

    1. the response must be a ``str``;
    2. after stripping outer whitespace it must be non-empty;
    3. it must occupy a single line -- an interior newline is treated as proof
       of a Markdown fence or of surrounding commentary;
    4. it must decode as JSON, with duplicate keys and non-finite literals
       refused by the hooks above;
    5. the decoded object must consume the response *entirely*, with no
       characters left over;
    6. it must be an object, not an array, number, or bare literal;
    7. it must contain ``key`` and nothing else.

    Outer whitespace is stripped but interior newlines are refused. That
    asymmetry is deliberate: a trailing newline is near-universal in model
    output and rejecting it would manufacture fallback calls for responses that
    are in fact well formed, whereas an interior newline really does indicate a
    fence or commentary rather than the bare object the contract asks for.

    Step 5 is what makes "consume the entire response" testable. ``raw_decode``
    reports how much input it used, so requiring ``end == len(text)`` rejects
    ``{"score": 0.2} I think Black is winning`` and any fence tail, rather than
    quietly accepting the prefix. The mirror case, commentary *before* the
    object, fails inside ``raw_decode`` instead, so it is identified by the
    position of the first brace: a brace at index 0 is a malformed attempt and
    is reported as ``invalid_json``, while a brace at a later index means the
    model produced a well-formed object and wrapped it, and is reported as
    ``surrounding_content``. That distinction is the difference between "the
    model cannot format JSON" and "the model adds a preamble", which are
    different defects with different fixes, and the pilot recorder reports them
    separately.
    """
    if not isinstance(response, str):
        raise ValueError(f"{REJECT_NOT_A_STRING}: got {type(response).__name__}")
    text = response.strip()
    if not text:
        raise ValueError(f"{REJECT_EMPTY}: the response was blank")
    if "\n" in text or "\r" in text:
        raise ValueError(
            f"{REJECT_MULTIPLE_LINES}: the response is not a single line"
        )
    try:
        value, end = _STRICT_DECODER.raw_decode(text)
    except _ContractViolation:
        raise
    except ValueError as error:
        # A brace anywhere but first position means the object is wrapped in a
        # fence or in commentary, which is a different defect from a malformed
        # object and worth reporting as such: the model produced the right
        # object and wrapped it, rather than producing nonsense. A brace in
        # first position is a genuine attempt, so its error is reported as-is.
        wrapped = text.find("{")
        if wrapped > 0:
            raise ValueError(
                f"{REJECT_SURROUNDING_CONTENT}: the JSON object begins at "
                f"character {wrapped} rather than at the start of the response"
            ) from error
        raise ValueError(f"{REJECT_INVALID_JSON}: {error}") from error
    if end != len(text):
        raise ValueError(
            f"{REJECT_SURROUNDING_CONTENT}: {text[end:]!r} follows the JSON object"
        )
    if not isinstance(value, dict):
        raise ValueError(f"{REJECT_NOT_AN_OBJECT}: got {type(value).__name__}")
    if key not in value:
        raise ValueError(f"{REJECT_MISSING_KEY}: {key!r} is required")
    if len(value) > 1:
        extra = sorted(name for name in value if name != key)
        raise ValueError(f"{REJECT_ADDITIONAL_KEY}: unexpected {extra}")
    return value[key]


#: Version tag for the cutoff-evaluator prompt. Bumped only when the instruction
#: text below changes, and the superseded text is retained in the repository so
#: the report can quote both. The initial value is the version flown at the
#: first live pilot.
EVALUATION_PROMPT_VERSION = "v2"

#: System message for the LLM cutoff evaluator. Two versions are frozen:
#: ``_EVALUATION_INSTRUCTIONS_V1`` (the original) and the current
#: ``_EVALUATION_INSTRUCTIONS_V2`` (used by :func:`build_evaluation_prompt`).
#:
#: The rubric deliberately mirrors :func:`evaluate_position_3` term for term and
#: coefficient for coefficient. The assignment's fallback mechanism then has a
#: useful property: when the model succeeds and when it fails, the position is
#: judged by the *same* objective, so any Stage B or Stage C utility difference
#: is attributable to model error rather than to the objective having silently
#: changed underneath the measurement. The alternative -- letting the model
#: form its own judgement -- makes a bad prompt indistinguishable from a bad
#: model, which is the one distinction the experiment exists to make.
#:
#: *The v2 revision and its evidence.* The live pilot (2026-09-28, 80 probes of
#: v1, ``results/pilot-eval-v1.csv``) rejected 28 of 80 responses: 25 with
#: ``REJECT_ invalid_json`` and 3 with ``REJECT_ multiple_lines``. Inspecting
#: the raw model text showed a single dominant failure mode: the model opens
#: ``{"score": 0.1355...`` and then emits an unbounded, repeating decimal
#: (``10101010...``) instead of the closing brace, or closes the object and
#: then appends a newline with self-correcting commentary. The parser is fixed
#: (whole response consumed, one line only), so the only lever available was
#: the prompt's response contract. v2 therefore constrains the expected output
#: more tightly: the score is a short decimal with at most two digits after the
#: point, and the closing brace is declared to be the final character of the
#: reply, with self-correction commentary explicitly forbidden. Both reset
#: points map to rejection rules. The threat-accuracy hypothesis tested by the
#: pilot (the model would fail at the threat term rather than the format) was
#: not the observed blocker and is reported as such.
_EVALUATION_INSTRUCTIONS_V1 = """\
You are the evaluation function of a Jetan playing agent. You are shown one \
position and you return a single number that scores it.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently; the Princess can never escape again after it.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO COMPUTE THE SCORE
Let "you" be the player named as the perspective below, and "them" be the
other player. Form each of these five terms as (yours minus theirs):

1. material
   The sum of the values of that side's surviving pieces.
2. mobility
   The number of legal moves available to that side.
3. advance
   The sum, over that side's pieces EXCLUDING the Princess, of how far each
   piece has advanced from its own home row toward the opposing home row.
   Orange's home row is 0, Black's home row is 9, and a piece that has reached
   the opposing home row has advanced 1.0.
4. threat
   The total value of THEIR pieces standing on squares YOUR pieces attack,
   minus the total value of YOUR pieces standing on squares THEIR pieces
   attack.
5. escape
   1 if the opposing Princess has already spent its escape, otherwise 0;
   minus 1 if your Princess has already spent its escape, otherwise 0.

Combine them into one raw score S and convert it:

  S = material + 0.2 * mobility + 0.15 * advance + 0.5 * threat + 1.0 * escape
  score = 0.99 * tanh(S / 20)

A positive score means the perspective player is better off. A negative score
means the other player is better off. The result is always between -0.99 and
0.99 inclusive.

PERSPECTIVE
The perspective is fixed by the request and is very often NOT the player to
move. Score the position for the perspective player regardless of who is to
move. Do not reverse the sign because the side to move differs.

RESPONSE CONTRACT
Reply with exactly one raw JSON object and nothing else:

{"score": 0.25}

The object must have exactly one key, "score", whose value is a JSON number
between -0.99 and 0.99. No Markdown code fences. No text before or after. No
second line. No extra keys. Any other output is rejected and the position is
scored by a deterministic fallback instead, so a malformed reply is worse than
a simple one.
"""


#: Current (v2) evaluation instructions. Identical to v1 except for the
#: ``RESPONSE CONTRACT`` block, which was tightened after the live pilot:
#: two-digit decimals stop the observed digit-runaway, and the explicit
#: closing-brace/final-character sentence targets the trailing-commentary
#: rejection. See :data:`EVALUATION_PROMPT_VERSION`.
_EVALUATION_INSTRUCTIONS_V2 = """\
You are the evaluation function of a Jetan playing agent. You are shown one \
position and you return a single number that scores it.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently; the Princess can never escape again after it.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO COMPUTE THE SCORE
Let "you" be the player named as the perspective below, and "them" be the
other player. Form each of these five terms as (yours minus theirs):

1. material
   The sum of the values of that side's surviving pieces.
2. mobility
   The number of legal moves available to that side.
3. advance
   The sum, over that side's pieces EXCLUDING the Princess, of how far each
   piece has advanced from its own home row toward the opposing home row.
   Orange's home row is 0, Black's home row is 9, and a piece that has reached
   the opposing home row has advanced 1.0.
4. threat
   The total value of THEIR pieces standing on squares YOUR pieces attack,
   minus the total value of YOUR pieces standing on squares THEIR pieces
   attack.
5. escape
   1 if the opposing Princess has already spent its escape, otherwise 0;
   minus 1 if your Princess has already spent its escape, otherwise 0.

Combine them into one raw score S and convert it:

  S = material + 0.2 * mobility + 0.15 * advance + 0.5 * threat + 1.0 * escape
  score = 0.99 * tanh(S / 20)

A positive score means the perspective player is better off. A negative score
means the other player is better off. The result is always between -0.99 and
0.99 inclusive.

PERSPECTIVE
The perspective is fixed by the request and is very often NOT the player to
move. Score the position for the perspective player regardless of who is to
move. Do not reverse the sign because the side to move differs.

RESPONSE CONTRACT
Reply with exactly one raw JSON object on a single line and nothing else:

{"score": 0.25}

The object must have exactly one key, "score", whose value is a JSON number
between -0.99 and 0.99. Write the number with at most two digits after the
decimal point, for example 0.25 or -0.99, never a long or repeating decimal.
The closing brace must be the final character of your reply; never add a
comment, a second thought, or anything else after it, even to correct
yourself. No Markdown code fences. No text before or after. No second line. No
extra keys. Any other output is rejected and the position is scored by a
deterministic fallback instead, so a malformed reply is worse than a simple
one.
"""


def _escape_text(board: JetanBoard, player: Player) -> str:
    """Return ``"spent"`` or ``"available"`` for ``player``'s Princess escape."""
    return "spent" if board.used_escape[player - 1] else "available"


def build_evaluation_prompt(
    board: JetanBoard, perspective: Player
) -> Sequence[dict[str, str]]:
    """Build the two-message prompt for the LLM cutoff evaluator.

    Returns a ``system`` message carrying the rules and the rubric, and a
    ``user`` message carrying the position. The split is not cosmetic: it is
    what makes the assignment's required prompt revision cheap and auditable,
    because a revision can change the rubric in
    :data:`_EVALUATION_INSTRUCTIONS_V2` without disturbing the position format
    below, and the two can then be diffed and quoted separately. V1 remains
    frozen as :data:`_EVALUATION_INSTRUCTIONS_V1` for the revision evidence.

    *The perspective trap.* The framework calls this with ``perspective`` fixed
    to the root player of the search, while ``board.player()`` is whoever is to
    move in the cutoff position. At depth 1 those differ for every action by
    the opponent, so the prompt states the two independently and in separate
    labelled fields, and the instructions warn against flipping the sign. A
    prompt that reported only the side to move would produce a sign error on
    roughly half of all evaluations, and a sign error is the one failure mode a
    fallback cannot repair: the value is well formed, so it is accepted.

    *Sensor parity with the deterministic agent.* The question the assignment
    asks is whether the model receives the same effective information the
    deterministic agent gets programmatically. Comparing the two:

    ==========================  ============================  =============
    Quantity                    Deterministic agent            This prompt
    ==========================  ============================  =============
    Piece inventory             ``board.pieces``              board diagram
    Legal-move counts           ``legal_action_count``        stated directly
    Advance                     computed from coordinates     readable from
                                                              the diagram
    Princess escape flags       ``board.used_escape``         stated directly
    Attacked squares            ``attacked_locations``        **not supplied**
    ==========================  ============================  =============

    Three of the four cheap signals are precomputed and stated, so the model
    does not spend its attention counting moves or re-reading the escape flags.
    The fifth -- attacked squares -- is deliberately withheld in both prompt
    versions. It is the honest weak spot: ``attacked_locations`` is a set the
    program derives by applying eight step patterns to every piece, and no
    amount of board text makes that arithmetic shallow. The model has the same
    underlying data as the deterministic agent here but must do far more work
    to extract it, so a wrong threat term biases the score in both directions
    at once. This was the pre-registered hypothesis for the pilot. The pilot
    (``results/pilot-eval-v1.csv``) did not falsify it, but it showed that the
    *rejection* blocker was format, not threat accuracy: 25 of 28 rejected
    responses ran away into an unbounded repeating decimal and 3 appended
    self-correction commentary after a valid object. The documented revision
    therefore tightened the response contract (see
    :data:`EVALUATION_PROMPT_VERSION`) rather than adding sensors, and the
    sensor set is unchanged between versions.

    *Range.* The rubric states ``-0.99 <= score <= 0.99`` and explains that
    ``+/-1.0`` is reserved for a finished game, because the framework returns
    exact ``+/-1.0`` for terminal states and a model that returns ``1.0`` would
    be rejected by :func:`parse_evaluation_response` and lose a position it had
    actually scored correctly.
    """
    side_to_move = board.player()
    lines = [
        f"Side to move: {side_to_move.name.capitalize()}",
        f"Score from the perspective of: {perspective.name.capitalize()}",
        "",
        "Legal moves available: "
        f"{side_to_move.name.capitalize()} {board.legal_action_count(side_to_move)}, "
        f"{side_to_move.opponent.name.capitalize()} "
        f"{board.legal_action_count(side_to_move.opponent)}",
        "Princess escape already spent: "
        f"Orange {_escape_text(board, Player.ORANGE)}, "
        f"Black {_escape_text(board, Player.BLACK)}",
        "",
        "Board:",
        str(board),
    ]
    return (
        {"role": "system", "content": _EVALUATION_INSTRUCTIONS_V2},
        {"role": "user", "content": "\n".join(lines)},
    )


def parse_evaluation_response(response: str) -> float:
    """Parse ``{"score": NUMBER}`` into a float, or raise to trigger the fallback.

    Accepts exactly one raw JSON object on one line with exactly one key,
    ``"score"``, whose value is a JSON number in ``[-0.99, 0.99]``.

    *Why raising is the contract.* :class:`llm_evaluation.LLMEvaluationFunction`
    wraps the prompt, the model call, and this parser in one ``except
    Exception`` and substitutes the fallback evaluator on any failure. Raising
    is therefore the documented rejection path, and it has a property a sentinel
    return would not: the rejection is counted. ``fallback_calls`` in the Stage
    B and Stage C tables is the number of times this function raised, so every
    rule below is a rule that makes the model's reliability measurable rather
    than merely enforced.

    *Layered validation.* :func:`_decode_single_object` first enforces the rules
    both modalities share -- single line, valid JSON, no duplicate keys, the
    whole response consumed, an object rather than a bare literal, exactly one
    key. This function then adds the rules specific to a number:

    * **Booleans are rejected before the numeric test.** In Python
      ``isinstance(True, int)`` is ``True``, so an ordinary
      ``isinstance(value, (int, float))`` check would accept ``{"score": true}``
      and return ``1.0`` -- a number, in range, and completely wrong. The
      ``bool`` test is listed first for exactly that reason.
    * **Non-finite values are rejected after conversion, not before.** The
      literal forms ``NaN`` and ``Infinity`` are caught earlier by
      :func:`_reject_constant`, but ``1e400`` is ordinary JSON syntax that
      ``float()`` turns into ``inf`` with no error at all, so the finiteness
      test is still required.
    * **Range is closed at both ends.** ``0.99`` and ``-0.99`` are accepted;
      ``1.0``, ``-1.0``, and ``0.991`` are not. ``+/-1.0`` is what the framework
      returns for a *finished* game, so accepting it from a cutoff evaluation
      would let a confident model claim a decided position and suppress any
      further search.
    """
    raw = _decode_single_object(response, "score")
    if isinstance(raw, bool):
        raise ValueError(f"{REJECT_WRONG_TYPE}: a Boolean is not a score")
    if not isinstance(raw, (int, float)):
        raise ValueError(f"{REJECT_WRONG_TYPE}: {type(raw).__name__} is not a score")
    score = float(raw)
    if not math.isfinite(score):
        raise ValueError(f"{REJECT_NOT_FINITE}: {score!r}")
    if not -SCORE_LIMIT <= score <= SCORE_LIMIT:
        raise ValueError(
            f"{REJECT_OUT_OF_RANGE}: {score!r} is outside "
            f"[-{SCORE_LIMIT}, {SCORE_LIMIT}]"
        )
    return score


#: Version tag for the direct-move prompt. Bumped only when the instruction
#: text below changes; the superseded text is retained in the repository.
MOVE_PROMPT_VERSION = "v2"

#: System message for the direct LLM move agent. Two versions are frozen:
#: ``_MOVE_INSTRUCTIONS_V1`` (the original) and the current
#: ``_MOVE_INSTRUCTIONS_V2`` (used by :func:`build_move_prompt`).
#:
#: The central design decision is that the legal move list is enumerated in the
#: prompt and the response must echo one token from it. The model is therefore
#: never asked to construct a move, only to *select* one, and
#: :func:`parse_move_response` matches the reply by dictionary lookup against a
#: table built from the engine's own ``board.actions()``. Illegal output is
#: impossible by construction rather than merely unlikely.
#:
#: *The v2 revision and its evidence.* The live move pilot (2026-09-28, 40
#: probes of v1, ``results/pilot-move-v1.csv``) accepted all 40 responses --
#: zero rejections. The revision is therefore a documented hardening step, not
#: a repair of an observed failure: the *evaluation* pilot on the same model
#: and the same shared parser showed the model's two failure habits (an
#: unbounded repeating decimal, and self-correcting commentary appended after
#: a valid JSON object), so v2 declares the closing brace the final character
#: of the reply and explicitly forbids a second thought after the object, pre-
#: emptively addressing what would otherwise become ``REJECT_ multiple_lines``
#: or ``REJECT_ surrounding_content`` in a move. V1 remains frozen for the
#: revision evidence.
_MOVE_INSTRUCTIONS_V1 = """\
You are choosing one move for a Jetan agent. You are shown a position and the \
complete list of legal moves, and you must choose one of them.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.
- A move is written as SOURCE-DESTINATION, for example d0-e3.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO CHOOSE
In rough order of priority:
1. If any move captures the opposing Princess, take it. It wins immediately.
2. Otherwise take a capture, preferring the most valuable piece captured.
3. Otherwise develop and centre your pieces, and avoid moves that leave your
   own pieces attacked with no support.

RESPONSE CONTRACT
Reply with exactly one raw JSON object and nothing else:

{"move": "a1-b2"}

The object must have exactly one key, "move", whose value is a JSON string
holding one of the legal move tokens listed below, copied exactly. No Markdown
code fences. No text before or after. No second line. No extra keys. Do not
invent a move that is not in the list, and do not add a capture suffix or
explanation to the token. Any other output is rejected and a deterministic
fallback chooses the move instead.
"""


#: Current (v2) move instructions. Identical to v1 except the ``RESPONSE
#: CONTRACT`` block declares that the closing brace must be the final character
#: of the reply and forbids any self-correcting commentary after the object,
#: pre-empting the trailing-text failure the evaluation pilot exposed on this
#: same model. See :data:`MOVE_PROMPT_VERSION`.
_MOVE_INSTRUCTIONS_V2 = """\
You are choosing one move for a Jetan agent. You are shown a position and the \
complete list of legal moves, and you must choose one of them.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.
- A move is written as SOURCE-DESTINATION, for example d0-e3.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO CHOOSE
In rough order of priority:
1. If any move captures the opposing Princess, take it. It wins immediately.
2. Otherwise take a capture, preferring the most valuable piece captured.
3. Otherwise develop and centre your pieces, and avoid moves that leave your
   own pieces attacked with no support.

RESPONSE CONTRACT
Reply with exactly one raw JSON object on a single line and nothing else:

{"move": "a1-b2"}

The object must have exactly one key, "move", whose value is a JSON string
holding one of the legal move tokens listed below, copied exactly and in full.
The closing brace must be the final character of your reply; never add a
comment, a second thought, or anything else after it, even to correct
yourself. No Markdown code fences. No text before or after. No second line. No
extra keys. Do not invent a move that is not in the list, and do not add a
capture suffix or explanation to the token. Any other output is rejected and a
deterministic fallback chooses the move instead.
"""


def _capture_notes(board: JetanBoard, actions: tuple[Move, ...]) -> str:
    """Return a short description of every legal move that captures a piece.

    The captures are listed separately from the numbered move list so the list
    itself stays machine-readable: annotating a token inline with its victim
    would give the model a string to copy that is not a legal token, and the
    cheapest way to guarantee a rejection is to offer a tempting wrong answer.
    """
    mover = board.player()
    notes = []
    for action in actions:
        victim = board.at(action.destination)
        if victim is None or victim.player is mover:
            continue
        suffix = (
            " -- wins the game immediately"
            if victim.kind is PieceType.PRINCESS
            else ""
        )
        notes.append(
            f"{action} captures {victim.kind.name.lower()}"
            f" (value {PIECE_VALUES[victim.kind]}){suffix}"
        )
    if not notes:
        return "No legal move captures a piece."
    return "Captures available:\n" + "\n".join(notes)


def build_move_prompt(
    board: JetanBoard,
    actions: tuple[Move, ...],
    recent_moves: tuple[Move, ...],
) -> Sequence[dict[str, str]]:
    """Build the two-message prompt for the direct LLM move agent.

    Returns a ``system`` message carrying the rules and the response contract,
    and a ``user`` message carrying the position, the authoritative legal move
    list, the capture summary, the Princess escape state, and the bounded
    recent-move window. Every item the assignment enumerates is present:

    * the **current player**, named explicitly in its own labelled field;
    * the **board**, as the same diagram the cutoff evaluator uses;
    * the **Princess escape state** for both sides;
    * the **authoritative legal move list**, numbered and using the exact token
      spelling the parser will look up; and
    * the **bounded recent-move context**, ``recent_moves`` as supplied by
      :class:`direct_llm_agent.DirectLLMAgent`.

    *Why the move list is authoritative.* The list is enumerated rather than
    described, and the parser matches the reply by lookup against the engine's
    own ``actions`` tuple. The model is selecting from a closed set instead of
    constructing a coordinate pair, so the "accept only a token from the
    supplied legal-action list" rule is enforced by the data structure rather
    than by a range check on a parsed coordinate. This is also why the capture
    summary is a separate block: annotating tokens inline would offer the model
    a string that looks right and is not a legal token.

    *On the recent-move bound.* ``recent_moves`` is supplied already truncated
    to the agent's ``history_limit`` (set to 8 in ``student_agents.py``), and it
    is cleared at the start of every match. It is offered as plain text in
    chronological order with no commentary, on the reasoning that a model
    reasons better about the position it is in than about the path that led
    there, and the Jetan opening is short enough that the window covers the
    part of the game where move order is most informative. This is a deliberate
    choice rather than a neutral one, and it is a candidate for the documented
    prompt revision if the pilot shows the model reasoning about its own last
    move instead of the position.

    *Sensor parity.* The prompt supplies strictly more than the deterministic
    agent receives, not less: it adds the numbered legal move list and the
    capture summary, which a depth-1 minimax derives internally and never
    states. The parity question is therefore not whether the information matches
    but whether the two modalities are doing comparable work, and they are not
    -- the direct agent receives a curated view plus one model call per move,
    while the cutoff evaluator makes a model call per candidate move. Stage B's
    two rows are not equal-effort conditions and the report must not present
    them as one.
    """
    mover = board.player()
    listing = "\n".join(
        f"{index}. {action}" for index, action in enumerate(actions, start=1)
    )
    history = (
        ", ".join(str(move) for move in recent_moves) if recent_moves else "(none)"
    )
    user = "\n".join(
        [
            f"You are playing: {mover.name.capitalize()}",
            "",
            "Board:",
            str(board),
            "",
            f"Princess escape already spent: Orange {_escape_text(board, Player.ORANGE)}, "
            f"Black {_escape_text(board, Player.BLACK)}",
            "",
            f"Legal moves ({len(actions)}):",
            listing,
            "",
            _capture_notes(board, actions),
            "",
            f"Recent moves: {history}",
        ]
    )
    return (
        {"role": "system", "content": _MOVE_INSTRUCTIONS_V2},
        {"role": "user", "content": user},
    )


def parse_move_response(response: str, actions: tuple[Move, ...]) -> Move:
    """Parse ``{"move": "a1-b2"}`` into a legal :class:`~jetan.Move`, or raise.

    Accepts exactly one raw JSON object on one line with exactly one key,
    ``"move"``, whose value is a string equal to ``str(move)`` for some member of
    ``actions``.

    *Legality by lookup, not by parsing.* The reply is used as a hash key
    against ``{str(action): action for action in actions}`` and the engine's own
    ``Move`` object is returned. The model text is never decomposed into
    coordinates, so there is no path by which a model-chosen square reaches the
    board. :meth:`jetan.JetanBoard.result` re-validates against
    ``board.actions()`` on every application and
    :meth:`direct_llm_agent.DirectLLMAgent.choose_action` re-checks membership
    afterwards, so this function is the second of three independent checks, not
    the only one.

    *Matching is exact and case-sensitive.* ``"D0-E3"``, ``"d0-e3 "``, and
    ``"d0 e3"`` are all rejected. Exactness is the point: a lenient normaliser
    would be a place where almost-correct model output turns into a silently
    different move, which is harder to attribute later than an outright
    rejection.

    *Why the parser and not the prompt guarantees legality.* The prompt offers a
    closed list, but a model can always produce a token that is not on it. A
    parser that reconstructed a move from a string would have to validate
    syntax, range, occupancy, and turn; a lookup has exactly one failure mode
    and no success path outside the engine's own set.
    """
    token = _decode_single_object(response, "move")
    if not isinstance(token, str):
        raise ValueError(
            f"{REJECT_WRONG_TYPE}: {type(token).__name__} is not a move token"
        )
    legal = {str(action): action for action in actions}
    if token not in legal:
        raise ValueError(
            f"{REJECT_ILLEGAL_MOVE}: {token!r} is not in the legal action list"
        )
    return legal[token]


def choose_fallback(board: JetanBoard, actions: tuple[Move, ...]) -> Move:
    """Return a deterministic legal move when the model's reply is unusable.

    *Policy.* Among ``actions``, prefer in order:

    1. a move that captures the opposing Princess, which ends the match at once;
    2. failing that, a move that captures a piece, taking the most valuable
       victim and breaking ties on the move token; and
    3. failing that, the lexicographically first move token.

    *Justification.*

    **It cannot produce an illegal move.** The result is always a member of
    ``actions``, which is the engine's own tuple, and it is chosen by ``min``
    over that tuple rather than constructed. This is the property that matters
    most: a fallback that could return an illegal move would hand the game to
    ``JetanBoard.result``, which raises on an illegal move and ends the match as
    an agent failure rather than as a rejected response.

    **It is deterministic.** No random source, no clock, no model text, and no
    dependence on anything outside its two arguments. Stage C varies a model
    seed and asks whether the deterministic opponent is unaffected by it; a
    fallback that drew on a random source would make the direct agent's move
    sequence seed-dependent for reasons that have nothing to do with the model,
    and the two effects could not be separated afterwards.

    **It is cheap.** ``O(len(actions))`` set lookups, with no search. This is a
    deliberate rejection of the obvious alternative of falling back to
    :func:`evaluate_position_3`-driven minimax. That fallback would be stronger,
    but it would consume the per-player think budget, and in the worst case -- a
    model that always fails -- the "direct LLM" row of Stage B would be a
    re-run of the deterministic row wearing a different name. A fallback must
    be a floor, not a substitute agent.

    **It is tiered, so the comparison stays honest.** A fallback that always
    returned ``actions[0]`` -- the supplied infrastructure default at
    ``direct_llm_agent.py:16`` -- would make every rejection look catastrophic
    and would understate the direct agent, because in most positions a capture
    is available and taking it is simply correct. Falling back to the first
    token would therefore bias Stage B and Stage C against the modality being
    measured. The three tiers cost the same asymptotically and remove that bias,
    while remaining far below the search cost a minimax fallback would impose.

    **The tiers are a floor, not a strategy.** Rule 3 is a tie-break, not a
    preference: the fallback has no positional judgement, so in a quiet position
    it is close to arbitrary. The consequence for interpretation is that a high
    fallback rate in quiet positions should be read as a *model* failure, not
    as evidence that the fallback played badly.
    """
    mover = board.player()

    def rank(action: Move) -> tuple[int, float, str]:
        victim = board.at(action.destination)
        if victim is None or victim.player is mover:
            return (2, 0.0, str(action))
        if victim.kind is PieceType.PRINCESS:
            return (0, 0.0, str(action))
        return (1, -PIECE_VALUES[victim.kind], str(action))

    return min(actions, key=rank) 
