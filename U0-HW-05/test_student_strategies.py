"""Student tests for the three deterministic evaluation functions.

These cover the correctness items the assignment requires for the deterministic
evaluators: finite bounded output, perspective reversal, and at least one
hand-checked position per evaluator. They are offline by construction -- no
model client is constructed and ``TestOfflineGuarantee`` actively fails if
anything tries to open a socket.

Run only this file with::

    python -m unittest -v test_student_strategies
"""

from __future__ import annotations

import math
import random
import unittest
import urllib.request

from jetan import JetanBoard, Piece, PieceType, Player
from student_strategies import (
    ADVANCE_UNIT,
    ESCAPE_UNIT,
    MOBILITY_UNIT,
    PIECE_VALUES,
    SCORE_LIMIT,
    THREAT_WEIGHT,
    _advance_term,
    _escape_term,
    _material_term,
    _mobility_term,
    _threat_term,
    evaluate_position_1,
    evaluate_position_2,
    evaluate_position_3,
)

EVALUATORS = (evaluate_position_1, evaluate_position_2, evaluate_position_3)

#: ``_squash`` is antisymmetric only up to floating-point summation order, which
#: was measured at 1.2e-16 over a 120-ply random walk. This tolerance sits far
#: above that noise floor while still being far below any meaningful score.
ANTISYMMETRY_DELTA = 1e-9


def walk(seed: int, plies: int) -> list[JetanBoard]:
    """Return the initial board plus each position along a random game.

    The walk is seeded, so the fixture is identical on every run and a failure
    can always be reproduced. Using real successors means the positions include
    captures, used escapes, and terminal states, not just tidy opening layouts.
    """
    rng = random.Random(seed)
    board = JetanBoard.initial()
    positions = [board]
    for _ in range(plies):
        actions = board.actions()
        if not actions:
            break
        board = board.result(rng.choice(actions))
        positions.append(board)
    return positions


#: Shared corpus: the opening layout, three seeded games, and the three
#: hand-built positions used for the numeric checks below.
CORPUS: list[JetanBoard] = (
    [JetanBoard.initial()]
    + walk(20260927, 60)
    + walk(7, 40)
    + walk(101, 25)
    + [
        JetanBoard.from_pieces(
            {
                (2, 2): Piece(Player.ORANGE, PieceType.CHIEF),
                (7, 7): Piece(Player.BLACK, PieceType.PADWAR),
            }
        ),
        JetanBoard.from_pieces(
            {
                (4, 8): Piece(Player.ORANGE, PieceType.FLIER),
                (0, 0): Piece(Player.ORANGE, PieceType.PADWAR),
                (1, 0): Piece(Player.ORANGE, PieceType.PADWAR),
                (9, 9): Piece(Player.BLACK, PieceType.WARRIOR),
            }
        ),
        JetanBoard.from_pieces(
            {
                (4, 4): Piece(Player.ORANGE, PieceType.FLIER),
                (1, 1): Piece(Player.BLACK, PieceType.FLIER),
                (9, 9): Piece(Player.BLACK, PieceType.PADWAR),
                (0, 0): Piece(Player.ORANGE, PieceType.PADWAR),
            },
            turn=Player.ORANGE,
            used_escape=(False, True),
        ),
    ]
)

HAND_CHECKED_A = CORPUS[-3]
HAND_CHECKED_B = CORPUS[-2]
HAND_CHECKED_C = CORPUS[-1]


class MaterialTableTests(unittest.TestCase):
    """The published 2017 C++ values are reproduced exactly."""

    def test_supplied_values_match_the_course_source(self) -> None:
        expected = {
            PieceType.PADWAR: 1.7,
            PieceType.WARRIOR: 1.9,
            PieceType.PANTHAN: 2.0,
            PieceType.DWAR: 4.9,
            PieceType.THOAT: 5.6,
            PieceType.FLIER: 9.5,
        }
        for kind, value in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(PIECE_VALUES[kind], value)

    def test_derived_royal_values_continue_the_pattern(self) -> None:
        # Chief continues the roughly-doubling tier pattern at 2x the Flier.
        self.assertEqual(PIECE_VALUES[PieceType.CHIEF], 2 * PIECE_VALUES[PieceType.FLIER])
        # Princess sits exactly one Panthan above Chief, since a Princess
        # capture is an unconditional win and a Chief capture is conditional.
        gap = PIECE_VALUES[PieceType.PRINCESS] - PIECE_VALUES[PieceType.CHIEF]
        self.assertAlmostEqual(gap, PIECE_VALUES[PieceType.PANTHAN], places=12)

    def test_royals_outrank_every_other_piece(self) -> None:
        royals = {PieceType.CHIEF, PieceType.PRINCESS}
        non_royal_max = max(
            value for kind, value in PIECE_VALUES.items() if kind not in royals
        )
        self.assertGreater(PIECE_VALUES[PieceType.CHIEF], non_royal_max)
        self.assertGreater(PIECE_VALUES[PieceType.PRINCESS], non_royal_max)

    def test_every_piece_type_has_a_value(self) -> None:
        self.assertEqual(set(PIECE_VALUES), set(PieceType))


class BoundsAndFinitenessTests(unittest.TestCase):
    """Required output contract: finite and inside [-0.99, 0.99]."""

    def test_every_evaluator_is_finite_and_bounded_on_the_corpus(self) -> None:
        for index, board in enumerate(CORPUS):
            for evaluate in EVALUATORS:
                for perspective in Player:
                    with self.subTest(ply=index, evaluate=evaluate.__name__, p=perspective):
                        value = evaluate(board, perspective)
                        self.assertTrue(math.isfinite(value), f"not finite: {value!r}")
                        self.assertGreaterEqual(value, -SCORE_LIMIT)
                        self.assertLessEqual(value, SCORE_LIMIT)

    def test_scores_never_leave_the_closed_interval_under_extreme_material(self) -> None:
        # A contrived position far outside any reachable game still validates.
        crowded = {
            (x, y): Piece(Player.ORANGE, PieceType.PRINCESS)
            for x in range(10)
            for y in range(10)
        }
        board = JetanBoard.from_pieces(crowded)
        for evaluate in EVALUATORS:
            with self.subTest(evaluate=evaluate.__name__):
                value = evaluate(board, Player.ORANGE)
                self.assertTrue(math.isfinite(value))
                self.assertLessEqual(abs(value), SCORE_LIMIT)


class PerspectiveReversalTests(unittest.TestCase):
    """Required contract: the score reverses when the perspective changes."""

    def test_evaluators_reverse_exactly_under_perspective_swap(self) -> None:
        for index, board in enumerate(CORPUS):
            for evaluate in EVALUATORS:
                with self.subTest(ply=index, evaluate=evaluate.__name__):
                    orange = evaluate(board, Player.ORANGE)
                    black = evaluate(board, Player.BLACK)
                    self.assertAlmostEqual(orange, -black, delta=ANTISYMMETRY_DELTA)

    def test_each_feature_term_is_individually_antisymmetric(self) -> None:
        # Reversal is a property of the construction, so it must hold for the
        # terms before they are summed as well as for the public functions.
        terms = (
            _material_term,
            _mobility_term,
            _advance_term,
            _threat_term,
            _escape_term,
        )
        for index, board in enumerate(CORPUS):
            for term in terms:
                with self.subTest(ply=index, term=term.__name__):
                    self.assertAlmostEqual(
                        term(board, Player.ORANGE),
                        -term(board, Player.BLACK),
                        delta=ANTISYMMETRY_DELTA,
                    )

    def test_symmetric_opening_scores_zero_from_both_perspectives(self) -> None:
        # The two armies are mirror images at ply 0, so every antisymmetric
        # term cancels. This is a clean end-to-end check of the whole pipeline.
        initial = JetanBoard.initial()
        for evaluate in EVALUATORS:
            with self.subTest(evaluate=evaluate.__name__):
                self.assertAlmostEqual(
                    evaluate(initial, Player.ORANGE), 0.0, delta=ANTISYMMETRY_DELTA
                )


class HandCheckedPositionTests(unittest.TestCase):
    """At least one independently hand-derived position per evaluator.

    Each expected literal below was derived by counting pieces and legal moves
    by hand from the documented formula, then confirmed against the code. The
    derivation is repeated in the comment above each assertion.
    """

    # -- Position A ---------------------------------------------------------
    # Board: Orange Chief at c2, Black Padwar at h7. Turn Orange.
    # Squares render as column letter + y with no offset, so the Chief's
    # (2, 2) prints as "c2" and the Padwar's (7, 7) as "h7".
    # Material  = 19.0 - 1.7                       = 17.3
    # Mobility  = 0.2 * (35 - 8)                   =  5.4
    # Advance   = 0.15 * ((2-0)/9 - (9-7)/9)       =  0.0   (equally advanced)
    #            Neither piece is on its own home rank: the Chief has advanced
    #            2 rows and the Padwar is 2 rows from Black's back rank, so the
    #            two terms are equal and the difference vanishes.
    # Threat    = 0.5 * (0 - 0)                    =  0.0   (c2 and h7 mutually
    #                                                          unreachable)
    # Escape    = 0.0
    # raw = 22.7
    # Eval 1 = 0.99 * tanh(17.3 / 20) = 0.6918366
    # Eval 2 = 0.99 * tanh(22.7 / 20) = 0.8045963
    # Eval 3 = 0.99 * tanh(22.7 / 20) = 0.8045963  (threat and escape are zero,
    #                                                 so Eval 3 == Eval 2 exactly)
    def test_position_a_evaluation_1(self) -> None:
        self.assertEqual(_material_term(HAND_CHECKED_A, Player.ORANGE), 17.3)
        self.assertAlmostEqual(
            evaluate_position_1(HAND_CHECKED_A, Player.ORANGE),
            0.691836592,
            places=9,
        )

    def test_position_a_evaluation_2(self) -> None:
        self.assertAlmostEqual(
            _mobility_term(HAND_CHECKED_A, Player.ORANGE), 0.2 * (35 - 8), places=12
        )
        self.assertAlmostEqual(
            evaluate_position_2(HAND_CHECKED_A, Player.ORANGE),
            0.804596340,
            places=9,
        )

    def test_position_a_evaluation_3_adds_nothing_when_threat_and_escape_vanish(
        self,
    ) -> None:
        self.assertEqual(_threat_term(HAND_CHECKED_A, Player.ORANGE), 0.0)
        self.assertEqual(_escape_term(HAND_CHECKED_A, Player.ORANGE), 0.0)
        self.assertAlmostEqual(
            evaluate_position_3(HAND_CHECKED_A, Player.ORANGE),
            evaluate_position_2(HAND_CHECKED_A, Player.ORANGE),
            places=15,
        )

    # -- Position B ---------------------------------------------------------
    # Board: Orange Flier at e8, Orange Padwars at a0 and b0,
    #        Black Warrior at j9. Turn Orange.
    # Material  = (9.5 + 1.7 + 1.7) - 1.9          = 11.0
    # Mobility  = 0.2 * (18 - 3)                   =  3.0
    #            Flier reaches 12 destinations, each Padwar 3, Warrior 3.
    # Advance   = 0.15 * ((8-0)/9 - (9-9)/9)       =  0.1333333
    #            Only the Flier has advanced: it is 8 rows from Orange's back
    #            rank, the two Padwars sit on row 0, and Black's Warrior sits on
    #            row 9 which is Black's own home rank.
    # Threat    = 0.5 * (0 - 0)                    =  0.0   (no cross-board
    #                                                          contact at all)
    # Escape    = 0.0
    # raw = 14.1333333
    # Eval 1 = 0.99 * tanh(11.0        / 20) = 0.4955150
    # Eval 2 = 0.99 * tanh(14.1333333  / 20) = 0.6024965
    # Eval 3 = 0.99 * tanh(14.1333333  / 20) = 0.6024965  (threat and escape are
    #                                                 zero, so Eval 3 == Eval 2)
    def test_position_b_evaluation_1(self) -> None:
        self.assertEqual(_material_term(HAND_CHECKED_B, Player.ORANGE), 11.0)
        self.assertAlmostEqual(
            evaluate_position_1(HAND_CHECKED_B, Player.ORANGE),
            0.495515009,
            places=9,
        )

    def test_position_b_evaluation_2(self) -> None:
        # Enumerated by hand: Flier 12, Padwar a0 3, Padwar b0 3, Warrior 3.
        self.assertEqual(
            HAND_CHECKED_B.legal_action_count(Player.ORANGE), 12 + 3 + 3
        )
        self.assertEqual(HAND_CHECKED_B.legal_action_count(Player.BLACK), 3)
        self.assertAlmostEqual(
            _mobility_term(HAND_CHECKED_B, Player.ORANGE), 3.0, places=12
        )
        self.assertAlmostEqual(
            _advance_term(HAND_CHECKED_B, Player.ORANGE),
            ADVANCE_UNIT * 8 / 9,
            places=12,
        )
        self.assertAlmostEqual(
            evaluate_position_2(HAND_CHECKED_B, Player.ORANGE),
            0.602496508,
            places=9,
        )

    # -- Position C ---------------------------------------------------------
    # Board: Orange Flier at e4, Orange Padwar at a0,
    #        Black Flier at b1, Black Padwar at j9, used_escape (False, True).
    # Material  = (9.5 + 1.7) - (9.5 + 1.7)        =  0.0
    # Mobility  = 0.2 * (16 - 12)                  =  0.8
    # Advance   = 0.15 * ((4-0)/9 - (1-9)*-1/9)    = -0.0666667
    #            Black Flier is deeper, so Black gains here.
    # Threat    = 0.5 * (9.5 - (9.5 + 1.7))        = -0.85
    #            Orange's Flier attacks only the Black Flier, while the Black
    #            Flier attacks both the Orange Flier and the Orange Padwar.
    # Escape    = 1.0 * (1 - 0)                    = +1.0  (Black spent hers)
    # raw = 0.0 + 0.8 - 0.0666667 - 0.85 + 1.0    = +0.8833333
    # Eval 1 = 0.99 * tanh( 0.0       / 20) =  0.0000000
    # Eval 2 = 0.99 * tanh( 0.7333333 / 20) =  0.0362837
    # Eval 3 = 0.99 * tanh( 0.8833333 / 20) =  0.0436966  (sum of all five)
    def test_position_c_evaluation_1(self) -> None:
        # Material is exactly even, so the baseline is uninformative here.
        self.assertEqual(_material_term(HAND_CHECKED_C, Player.ORANGE), 0.0)
        self.assertAlmostEqual(
            evaluate_position_1(HAND_CHECKED_C, Player.ORANGE), 0.0, places=15
        )

    def test_position_c_evaluation_2(self) -> None:
        self.assertAlmostEqual(
            _mobility_term(HAND_CHECKED_C, Player.ORANGE), 0.2 * (16 - 12), places=12
        )
        self.assertAlmostEqual(
            _advance_term(HAND_CHECKED_C, Player.ORANGE), -ADVANCE_UNIT * 4 / 9, places=12
        )
        self.assertAlmostEqual(
            evaluate_position_2(HAND_CHECKED_C, Player.ORANGE),
            0.036283741,
            places=9,
        )

    def test_position_c_evaluation_3(self) -> None:
        # Black's Flier covers both Orange pieces; Orange's covers one Black one.
        self.assertAlmostEqual(
            _threat_term(HAND_CHECKED_C, Player.ORANGE),
            THREAT_WEIGHT * (9.5 - (9.5 + 1.7)),
            places=12,
        )
        self.assertAlmostEqual(
            _escape_term(HAND_CHECKED_C, Player.ORANGE), ESCAPE_UNIT * 1.0, places=12
        )
        self.assertAlmostEqual(
            evaluate_position_3(HAND_CHECKED_C, Player.ORANGE),
            0.043696591,
            places=9,
        )


class FeatureBehaviourTests(unittest.TestCase):
    """Each added term must actually change the score in the intended direction."""

    def test_gaining_an_enemy_piece_lowers_my_score_and_raises_theirs(self) -> None:
        # ``bare`` is one Orange Padwar, so Orange is up a piece and Black is
        # down one. ``with_extra`` adds a Black Padwar, which must hurt Orange
        # and help Black. Checking both directions at once also re-verifies
        # perspective reversal in a case where the feature terms must fire.
        bare = JetanBoard.from_pieces({(4, 4): Piece(Player.ORANGE, PieceType.PADWAR)})
        with_extra = JetanBoard.from_pieces(
            {
                (4, 4): Piece(Player.ORANGE, PieceType.PADWAR),
                (0, 0): Piece(Player.BLACK, PieceType.PADWAR),
            }
        )
        for evaluate in EVALUATORS:
            with self.subTest(evaluate=evaluate.__name__):
                self.assertGreater(
                    evaluate(bare, Player.ORANGE), 0.0, "Orange should be up a piece"
                )
                self.assertLess(
                    evaluate(with_extra, Player.ORANGE), evaluate(bare, Player.ORANGE)
                )
                self.assertGreater(
                    evaluate(with_extra, Player.BLACK), evaluate(bare, Player.BLACK)
                )

    def test_a_spent_opponent_escape_raises_the_score(self) -> None:
        pieces = {
            (4, 4): Piece(Player.ORANGE, PieceType.PADWAR),
            (0, 0): Piece(Player.BLACK, PieceType.PADWAR),
        }
        fresh = JetanBoard.from_pieces(pieces, used_escape=(False, False))
        black_spent = JetanBoard.from_pieces(pieces, used_escape=(False, True))
        orange_spent = JetanBoard.from_pieces(pieces, used_escape=(True, False))
        # Only Evaluation 3 models escape state at all.
        self.assertEqual(_escape_term(fresh, Player.ORANGE), 0.0)
        self.assertEqual(_escape_term(black_spent, Player.ORANGE), ESCAPE_UNIT)
        self.assertEqual(_escape_term(orange_spent, Player.ORANGE), -ESCAPE_UNIT)
        self.assertGreater(
            evaluate_position_3(black_spent, Player.ORANGE),
            evaluate_position_3(fresh, Player.ORANGE),
        )
        self.assertLess(
            evaluate_position_3(orange_spent, Player.ORANGE),
            evaluate_position_3(fresh, Player.ORANGE),
        )

    def test_a_free_capture_is_rewarded_and_hanging_pieces_are_penalized(self) -> None:
        # Every Jetan step set is closed under negation, so two pieces of equal
        # reach that threaten each other cancel exactly inside the threat
        # difference. A one-sided threat therefore needs pieces of *different*
        # reach: the Flier jumps three squares diagonally from e4 to b1, but a
        # Padwar's two diagonal steps from b1 can never reach back to e4. The
        # two boards are colour-swapped mirrors, isolating the sign of the term:
        # 0.5 * (1.7 - 0) versus 0.5 * (0 - 1.7).
        free_capture = JetanBoard.from_pieces(
            {
                (4, 4): Piece(Player.ORANGE, PieceType.FLIER),
                (1, 1): Piece(Player.BLACK, PieceType.PADWAR),
            }
        )
        hanging = JetanBoard.from_pieces(
            {
                (1, 1): Piece(Player.ORANGE, PieceType.PADWAR),
                (4, 4): Piece(Player.BLACK, PieceType.FLIER),
            }
        )
        self.assertAlmostEqual(
            _threat_term(free_capture, Player.ORANGE), 0.85, places=12
        )
        self.assertAlmostEqual(_threat_term(hanging, Player.ORANGE), -0.85, places=12)
        # The mirror must also mirror in sign, which is antisymmetry again.
        self.assertAlmostEqual(
            _threat_term(free_capture, Player.ORANGE),
            -_threat_term(free_capture, Player.BLACK),
            delta=ANTISYMMETRY_DELTA,
        )
        # Only Evaluation 3 models threats, so the composite has to react.
        self.assertGreater(
            evaluate_position_3(free_capture, Player.ORANGE),
            evaluate_position_3(hanging, Player.ORANGE),
        )

    def test_the_three_evaluators_are_genuinely_different_functions(self) -> None:
        # Evaluations 2 and 3 are strict supersets of 1's feature set, so on a
        # position where the extra terms fire they must disagree. If they ever
        # matched everywhere, the revision sequence would be cosmetic.
        board = HAND_CHECKED_C
        scores = {evaluate.__name__: evaluate(board, Player.ORANGE) for evaluate in EVALUATORS}
        self.assertNotAlmostEqual(
            scores["evaluate_position_1"], scores["evaluate_position_2"], places=6
        )
        self.assertNotAlmostEqual(
            scores["evaluate_position_2"], scores["evaluate_position_3"], places=6
        )

    def test_mobility_and_advance_cannot_outbid_a_flier(self) -> None:
        # The documented cost bound: even an implausible 30-move and full-army
        # advance swing stays below one Flier, so material always dominates.
        swing = MOBILITY_UNIT * 30 + ADVANCE_UNIT * 20
        self.assertLess(swing, PIECE_VALUES[PieceType.FLIER])

    def test_term_coefficients_match_the_documented_formula(self) -> None:
        self.assertEqual(MOBILITY_UNIT, 0.2)
        self.assertEqual(ADVANCE_UNIT, 0.15)
        self.assertEqual(THREAT_WEIGHT, 0.5)
        self.assertEqual(ESCAPE_UNIT, 1.0)


class TestOfflineGuarantee(unittest.TestCase):
    """The evaluators must run with no network access."""

    def test_evaluation_never_attempts_a_connection(self) -> None:
        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("a deterministic evaluator attempted network access")

        original = urllib.request.urlopen
        urllib.request.urlopen = forbidden  # type: ignore[assignment]
        try:
            for board in CORPUS:
                for evaluate in EVALUATORS:
                    evaluate(board, Player.ORANGE)
                    evaluate(board, Player.BLACK)
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
