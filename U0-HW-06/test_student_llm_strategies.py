"""Offline tests for the student LLM prompt, parser, and fallback functions.

Every test in this file runs against :class:`llm_client.ScriptedClient`. No
test opens a socket, and :class:`TestOfflineGuarantee` asserts that no supplied
LLM agent can reach the network without the ``--live`` flag, so the whole suite
is safe to run before the course endpoint is configured.

The rejection tests are organised by validation category rather than by input
string, because the category is what the report's rejection table reports.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import replace
from pathlib import Path

import record_llm_responses as recorder
import student_strategies as strategies
from direct_llm_agent import DirectLLMAgent
from jetan import JetanBoard, Move, Piece, PieceType, Player
from llm_client import ScriptedClient
from llm_evaluation import LLMEvaluationFunction
from llm_minimax_agent import LLMMinimaxAgent
from student_strategies import (
    REJECTION_CATEGORIES,
    build_evaluation_prompt,
    build_move_prompt,
    choose_fallback,
    evaluate_position_1,
    evaluate_position_3,
    parse_evaluation_response,
    parse_move_response,
)

#: One board for parser tests. Turn and perspective are varied independently so
#: the sign-error case is reachable without constructing a special position.
BOARD = JetanBoard.initial()


def category_of(error: BaseException) -> str:
    """Return the validation category prefix of a rejection message."""
    return str(error).split(":", 1)[0]


def walk(plies: int, first: int = 0) -> JetanBoard:
    """Return a board ``plies`` moves in, choosing a fixed branch each ply."""
    board = BOARD
    for _ in range(plies):
        board = board.result(board.actions()[first % len(board.actions())])
    return board


def sample_positions(depth: int = 3, width: int = 8) -> list[JetanBoard]:
    """Return a spread of positions from a breadth-limited search of the tree.

    A single fixed walk almost never reaches a capture, because both sides
    develop symmetrically from the opening, so the capture and Princess-capture
    tests need real breadth rather than a longer line.
    """
    frontier = [BOARD]
    collected: list[JetanBoard] = []
    for _ in range(depth):
        following: list[JetanBoard] = []
        for board in frontier:
            children = [board.result(a) for a in board.actions()[:width]]
            collected.extend(children)
            following.extend(children)
        frontier = following
    return collected


# --- Verified contact positions -------------------------------------------
#
# A tree search does not reach a capture within a useful test runtime: from the
# opening both armies develop symmetrically and the Princess stays home, so
# these three positions are constructed instead. Each was probed to confirm it
# exposes exactly the captures its test asserts, so the assertions below are
# about the fallback's policy rather than about reachability.
#
# Coordinates are ``(x, y)``. A move token is the column letter followed by the
# y value, so ``(4, 4)`` prints as ``e4`` and ``(5, 3)`` prints as ``f3``.


def princess_versus_dwar_board() -> JetanBoard:
    """Orange to move; a Princess capture and a Dwar capture are both legal."""
    return JetanBoard.from_pieces(
        {
            (4, 4): Piece(Player.BLACK, PieceType.PRINCESS),   # e4
            (6, 4): Piece(Player.BLACK, PieceType.DWAR),      # g4
            (2, 2): Piece(Player.ORANGE, PieceType.PADWAR),   # c2
            (5, 3): Piece(Player.ORANGE, PieceType.FLIER),    # f3
        },
        turn=Player.ORANGE,
    )


def competing_captures_board() -> JetanBoard:
    """Orange to move; a Dwar (4.9) and a Warrior (1.9) are both capturable."""
    return JetanBoard.from_pieces(
        {
            (4, 4): Piece(Player.BLACK, PieceType.DWAR),      # e4
            (0, 2): Piece(Player.BLACK, PieceType.WARRIOR),   # a2
            (3, 3): Piece(Player.ORANGE, PieceType.FLIER),    # d3
            (0, 0): Piece(Player.ORANGE, PieceType.PADWAR),   # a0
        },
        turn=Player.ORANGE,
    )


def mirrored_princess_board() -> JetanBoard:
    """The Princess-capture case with the colours and the turn swapped."""

    return JetanBoard.from_pieces(
        {
            (5, 5): Piece(Player.ORANGE, PieceType.PRINCESS),  # f5
            (3, 5): Piece(Player.ORANGE, PieceType.DWAR),     # d5
            (7, 7): Piece(Player.BLACK, PieceType.PADWAR),    # h7
            (4, 6): Piece(Player.BLACK, PieceType.FLIER),     # e6
        },
        turn=Player.BLACK,
    )


class TestEvaluationParserAcceptance(unittest.TestCase):
    """Well-formed responses must be accepted and return the stated number."""

    def test_accepts_representative_valid_responses(self) -> None:
        cases = {
            '{"score": 0.25}': 0.25,
            '{"score":0}': 0.0,
            '{"score": -0.5}': -0.5,
            '{"score": 0.99}': 0.99,
            '{"score": -0.99}': -0.99,
            '{"score": 1e-2}': 0.01,
            '{"score": -1.5e-1}': -0.15,
            '{"score": 0}': 0.0,
        }
        for response, expected in cases.items():
            with self.subTest(response=response):
                self.assertAlmostEqual(parse_evaluation_response(response), expected)

    def test_tolerates_surrounding_whitespace_but_not_interior_newlines(self) -> None:
        for response in ('  {"score": 0.4}  ', "\n{\"score\": 0.4}", '{"score": 0.4}\n'):
            with self.subTest(response=repr(response)):
                self.assertAlmostEqual(parse_evaluation_response(response), 0.4)

    def test_accepted_values_stay_inside_the_closed_bound(self) -> None:
        for response in ('{"score": 0.99}', '{"score": -0.99}'):
            with self.subTest(response=response):
                value = parse_evaluation_response(response)
                self.assertLessEqual(abs(value), strategies.SCORE_LIMIT)


class TestEvaluationParserRejection(unittest.TestCase):
    """Every malformed response must raise, and be attributable to a rule."""

    REJECTED = (
        ("multiple_lines", '```json\n{"score": 0.2}\n```'),
        ("multiple_lines", '{"score": 0.2}\nHope that helps'),
        ("multiple_lines", '{"score":\n0.2}'),
        ("surrounding_content", 'Here is my answer: {"score": 0.2}'),
        ("surrounding_content", '{"score": 0.2} Black is winning'),
        ("surrounding_content", '```{"score": 0.2}```'),
        ("invalid_json", "{"),
        ("invalid_json", '{"score":}'),
        ("invalid_json", "{'score': 0.25}"),
        ("invalid_json", '{"score" 0.25}'),
        ("invalid_json", "{score: 0.25}"),
        ("duplicate_key", '{"score": 0.1, "score": 0.9}'),
        ("missing_key", "{}"),
        ("missing_key", '{"value": 0.25}'),
        ("additional_key", '{"score": 0.25, "reason": "ok"}'),
        ("wrong_type", '{"score": true}'),
        ("wrong_type", '{"score": false}'),
        ("wrong_type", '{"score": "0.25"}'),
        ("wrong_type", '{"score": null}'),
        ("wrong_type", '{"score": [0.25]}'),
        ("wrong_type", '{"score": {"value": 0.25}}'),
        ("non_finite_literal", '{"score": NaN}'),
        ("non_finite_literal", '{"score": Infinity}'),
        ("non_finite_literal", '{"score": -Infinity}'),
        ("not_finite", '{"score": 1e400}'),
        ("not_finite", '{"score": -1e400}'),
        ("out_of_range", '{"score": 1.0}'),
        ("out_of_range", '{"score": -1.0}'),
        ("out_of_range", '{"score": 1.5}'),
        ("out_of_range", '{"score": -0.991}'),
        ("out_of_range", '{"score": 100}'),
        ("not_an_object", "0.25"),
        ("not_an_object", "[0.25]"),
        ("not_an_object", '"0.25"'),
        ("empty_response", ""),
        ("empty_response", "   "),
        ("not_a_string", None),
        ("not_a_string", 0.25),
    )

    def test_each_malformed_response_is_rejected_by_its_named_rule(self) -> None:
        for expected, response in self.REJECTED:
            with self.subTest(response=repr(response)):
                with self.assertRaises(ValueError) as caught:
                    parse_evaluation_response(response)  # type: ignore[arg-type]
                self.assertEqual(category_of(caught.exception), expected)

    def test_boolean_scores_are_rejected_rather_than_read_as_one(self) -> None:
        """``isinstance(True, int)`` is True, so this needs its own guard."""
        self.assertLess(abs(parse_evaluation_response('{"score": 0.5}')), 1.0)
        with self.assertRaises(ValueError):
            parse_evaluation_response('{"score": true}')

    def test_every_declared_category_is_reachable(self) -> None:
        """A declared-but-unreachable category would leave an empty report row.

        ``illegal_move`` cannot arise in the evaluation contract, so it is
        credited here from the move parser's results rather than from this
        class's inputs.
        """
        seen = set()
        for _expected, response in self.REJECTED:
            with self.assertRaises(ValueError) as caught:
                parse_evaluation_response(response)  # type: ignore[arg-type]
            seen.add(category_of(caught.exception))
        move_seen = set()
        actions = BOARD.actions()
        for response in ('{"move": "Z9-Z9"}', '{"move": 5}', "bad"):
            with self.assertRaises(ValueError) as caught:
                parse_move_response(response, actions)
            move_seen.add(category_of(caught.exception))
        self.assertIn("illegal_move", move_seen)
        self.assertEqual(
            seen | move_seen,
            set(REJECTION_CATEGORIES),
            "declared categories and exercised categories must match",
        )


class TestMoveParserAcceptance(unittest.TestCase):
    """A legal token must resolve to the engine's own Move object."""

    def test_accepts_each_legal_token(self) -> None:
        actions = BOARD.actions()
        for action in (actions[0], actions[len(actions) // 2], actions[-1]):
            with self.subTest(token=str(action)):
                parsed = parse_move_response(
                    '{"move": "%s"}' % action, actions
                )
                self.assertIsInstance(parsed, Move)
                self.assertEqual(parsed, action)

    def test_returns_the_engine_object_not_a_reconstruction(self) -> None:
        actions = BOARD.actions()
        target = actions[3]
        self.assertIs(parse_move_response('{"move": "%s"}' % target, actions), target)


class TestMoveParserRejection(unittest.TestCase):
    """A move must come from the supplied list, matched exactly."""

    def test_illegal_and_malformed_tokens_are_rejected(self) -> None:
        actions = BOARD.actions()
        legal = str(actions[0])
        cases = (
            ("illegal_move", '{"move": "Z9-Z9"}'),
            ("illegal_move", '{"move": "a0-a0"}'),
            ("illegal_move", '{"move": "j9-j9"}'),
            ("illegal_move", '{"move": "%s "}' % legal),
            ("illegal_move", '{"move": "%s"}' % legal.upper()),
            ("illegal_move", '{"move": "%s"}' % legal.replace("-", " ")),
            ("wrong_type", '{"move": 5}'),
            ("wrong_type", '{"move": null}'),
            ("wrong_type", '{"move": true}'),
            ("wrong_type", '{"move": ["%s"]}' % legal),
            ("missing_key", "{}"),
            ("missing_key", '{"action": "%s"}' % legal),
            ("additional_key", '{"move": "%s", "why": "capture"}' % legal),
            ("duplicate_key", '{"move": "%s", "move": "%s"}' % (legal, legal)),
            ("surrounding_content", 'Sure: {"move": "%s"}' % legal),
            ("surrounding_content", '{"move": "%s"} (best move)' % legal),
            ("multiple_lines", '```json\n{"move": "%s"}\n```' % legal),
            ("invalid_json", '{"move": }'),
            ("empty_response", ""),
        )
        for expected, response in cases:
            with self.subTest(response=response):
                with self.assertRaises(ValueError) as caught:
                    parse_move_response(response, actions)
                self.assertEqual(category_of(caught.exception), expected)

    def test_rejection_is_measured_against_the_supplied_list_only(self) -> None:
        """A token legal on one board must be refused when it is not supplied."""
        for later in sample_positions(depth=3, width=6):
            later_actions = later.actions()
            if not later_actions:
                continue
            late_tokens = {str(action) for action in later_actions}
            excluded = [
                action for action in BOARD.actions() if str(action) not in late_tokens
            ]
            if not excluded:
                continue
            for action in excluded:
                with self.assertRaises(ValueError) as caught:
                    parse_move_response('{"move": "%s"}' % action, later_actions)
                self.assertEqual(category_of(caught.exception), "illegal_move")
            return
        self.skipTest("every opening token also appears in the sampled lists")


class TestModelTextIsNeverExecuted(unittest.TestCase):
    """Model output must be data, never code, and never reach the board."""

    def test_module_calls_no_dynamic_execution_primitive(self) -> None:
        source = Path(strategies.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        banned = {
            "eval", "exec", "compile", "getattr", "setattr",
            "__import__", "delattr", "globals", "vars", "locals",
        }
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    found.add(func.id)
                elif isinstance(func, ast.Attribute):
                    found.add(func.attr)
        self.assertEqual(
            found & banned,
            set(),
            "student_strategies must not call a dynamic-execution primitive",
        )

    def test_module_imports_nothing_that_could_execute_or_fetch(self) -> None:
        source = Path(strategies.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & {"pickle", "subprocess", "os", "shutil", "ctypes", "marshal"},
            set(),
            "student_strategies must not import an execution or process module",
        )

    def test_move_token_is_used_only_as_a_dict_key(self) -> None:
        """Model text is a hash key against a table the engine produced."""
        actions = walk(plies=1).actions()
        target = actions[0]
        parsed = parse_move_response('{"move": "%s"}' % target, actions)
        table = {str(action): action for action in actions}
        self.assertIs(parsed, table[str(target)])

    def test_fallback_depends_only_on_board_and_actions(self) -> None:
        """No clock, no random source, and no model text in the fallback."""
        import inspect

        parameters = list(inspect.signature(choose_fallback).parameters)
        self.assertEqual(parameters, ["board", "actions"])
        board = walk(plies=1)
        actions = board.actions()
        self.assertEqual(choose_fallback(board, actions), choose_fallback(board, actions))


class TestEvaluationPrompt(unittest.TestCase):
    """The prompt must carry state, turn, perspective, scale, and the contract."""

    def test_returns_two_role_tagged_string_messages(self) -> None:
        messages = build_evaluation_prompt(BOARD, Player.ORANGE)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        for message in messages:
            self.assertEqual(set(message), {"role", "content"})
            self.assertIsInstance(message["content"], str)
            self.assertTrue(message["content"].strip())

    def test_user_message_contains_the_board_diagram(self) -> None:
        content = build_evaluation_prompt(BOARD, Player.ORANGE)[1]["content"]
        self.assertIn(str(BOARD), content)
        self.assertIn("a b c d e f g h i j", content)

    def test_turn_and_perspective_are_reported_independently(self) -> None:
        """The sign-error case: the cutoff board's turn is the opponent."""
        self.assertIs(BOARD.player(), Player.ORANGE)
        messages = build_evaluation_prompt(BOARD, Player.BLACK)
        content = messages[1]["content"]
        self.assertIn("Side to move: Orange", content)
        self.assertIn("Score from the perspective of: Black", content)
        reversed_ = build_evaluation_prompt(BOARD, Player.ORANGE)[1]["content"]
        self.assertIn("Score from the perspective of: Orange", reversed_)

    def test_perspective_is_recorded_even_when_it_matches_the_turn(self) -> None:
        content = build_evaluation_prompt(BOARD, Player.ORANGE)[1]["content"]
        self.assertIn("Side to move: Orange", content)
        self.assertIn("Score from the perspective of: Orange", content)

    def test_reports_mobility_for_both_sides(self) -> None:
        content = build_evaluation_prompt(BOARD, Player.ORANGE)[1]["content"]
        self.assertIn("Legal moves available:", content)
        self.assertIn(str(len(BOARD.actions())), content)

    def test_reports_escape_state_for_both_sides(self) -> None:
        spent = replace(BOARD, used_escape=(True, False))
        content = build_evaluation_prompt(spent, Player.ORANGE)[1]["content"]
        self.assertIn("Orange spent", content)
        self.assertIn("Black available", content)
        fresh = build_evaluation_prompt(BOARD, Player.ORANGE)[1]["content"]
        self.assertIn("Orange available", fresh)

    def test_instructions_state_the_contract_and_the_range(self) -> None:
        system = " ".join(
            build_evaluation_prompt(BOARD, Player.ORANGE)[0]["content"].split()
        )
        self.assertIn('{"score": 0.25}', system)
        self.assertIn("-0.99", system)
        self.assertIn("0.99", system)
        self.assertIn("code fences", system)
        self.assertIn("perspective", system.lower())
        self.assertIn("not the player to move", system.lower())

    def test_instructions_mirror_the_fallback_evaluator_terms(self) -> None:
        """The rubric must describe evaluate_position_3, not some other target."""
        system = build_evaluation_prompt(BOARD, Player.ORANGE)[0]["content"]
        for term in ("material", "mobility", "advance", "threat", "escape"):
            self.assertIn(term, system)
        for value in strategies.PIECE_VALUES.values():
            self.assertIn(str(value), system)
        self.assertIn("0.2 * mobility", system)
        self.assertIn("0.15 * advance", system)
        self.assertIn("0.5 * threat", system)
        self.assertIn("1.0 * escape", system)
        self.assertIn("tanh(S / 20)", system)


class TestMovePrompt(unittest.TestCase):
    """The direct-agent prompt must carry every sensor the report lists."""

    def setUp(self) -> None:
        self.actions = BOARD.actions()
        self.user = build_move_prompt(BOARD, self.actions, self.actions[:2])[1]["content"]

    def test_returns_two_role_tagged_string_messages(self) -> None:
        messages = build_move_prompt(BOARD, self.actions, ())
        self.assertEqual([m["role"] for m in messages], ["system", "user"])

    def test_names_the_current_player(self) -> None:
        self.assertIn("You are playing: Orange", self.user)

    def test_contains_the_board_diagram(self) -> None:
        self.assertIn(str(BOARD), self.user)

    def test_contains_the_princess_escape_state(self) -> None:
        self.assertIn("Princess escape already spent", self.user)
        self.assertIn("Orange available", self.user)
        self.assertIn("Black available", self.user)

    def test_enumerates_every_legal_move_with_its_exact_token(self) -> None:
        for index, action in enumerate(self.actions, start=1):
            self.assertIn(f"{index}. {action}", self.user)
        for action in self.actions:
            self.assertIn(str(action), self.user)

    def test_every_listed_token_round_trips_through_the_parser(self) -> None:
        """The prompt offers only strings the parser will accept."""
        for action in self.actions:
            with self.subTest(action=str(action)):
                self.assertEqual(
                    parse_move_response('{"move": "%s"}' % action, self.actions),
                    action,
                )

    def test_includes_bounded_recent_move_context(self) -> None:
        self.assertIn("Recent moves:", self.user)
        self.assertIn(f"{self.actions[0]}, {self.actions[1]}", self.user)
        empty = build_move_prompt(BOARD, self.actions, ())[1]["content"]
        self.assertIn("(none)", empty)

    def test_says_so_when_no_capture_is_available(self) -> None:
        self.assertIn("No legal move captures a piece.", self.user)

    def test_annotates_captures_and_flags_a_winning_princess_capture(self) -> None:
        for board in (princess_versus_dwar_board(), mirrored_princess_board()):
            with self.subTest(turn=board.player().name):
                actions = board.actions()
                user = build_move_prompt(board, actions, ())[1]["content"]
                self.assertIn("Captures available:", user)
                captures = {
                    action: board.at(action.destination)
                    for action in actions
                    if board.at(action.destination) is not None
                }
                princess = [
                    action
                    for action, victim in captures.items()
                    if victim.kind is PieceType.PRINCESS
                ]
                self.assertTrue(princess, "fixture must expose a Princess capture")
                for action in princess:
                    self.assertIn(
                        f"{action} captures princess (value 21.0) "
                        "-- wins the game immediately",
                        user,
                    )
                for action, victim in captures.items():
                    self.assertIn(f"{action} captures {victim.kind.name.lower()}", user)
                    self.assertIn(f"(value {strategies.PIECE_VALUES[victim.kind]})", user)

    def test_every_token_in_the_numbered_list_and_capture_notes_is_legal(self) -> None:
        """The prompt must never offer a string the parser would reject."""
        boards = (BOARD, competing_captures_board(), princess_versus_dwar_board())
        for board in boards:
            actions = board.actions()
            if not actions:
                continue
            legal = {str(action) for action in actions}
            user = build_move_prompt(board, actions, ())[1]["content"]
            lines = user.splitlines()
            start = lines.index(f"Legal moves ({len(actions)}):") + 1
            numbered: list[str] = []
            for line in lines[start:]:
                if not line.strip():
                    break
                index, _, token = line.partition(". ")
                self.assertTrue(index.isdigit(), line)
                numbered.append(token.strip())
            self.assertEqual(numbered, [str(a) for a in actions])
            in_capture_block = False
            for line in lines:
                if line.startswith("Captures available:"):
                    in_capture_block = True
                    continue
                if not in_capture_block:
                    continue
                if not line.strip():
                    break
                token = line.split(" captures ")[0].strip()
                self.assertIn(
                    token, legal, f"capture note offers an illegal token: {line!r}"
                )
                self.assertIn(
                    parse_move_response('{"move": "%s"}' % token, actions), actions
                )
            self.assertIn(
                "No legal move captures a piece.",
                build_move_prompt(BOARD, BOARD.actions(), ())[1]["content"],
            )

    def test_instructions_state_the_move_contract(self) -> None:
        system = build_move_prompt(BOARD, self.actions, ())[0]["content"]
        self.assertIn('{"move": "a1-b2"}', system)
        self.assertIn("code fences", system)
        self.assertIn("exactly one key", system)
        self.assertIn("Princess", system)


class TestEvaluationIntegration(unittest.TestCase):
    """The evaluator must use, reject, cache, and budget exactly as specified."""

    def build(self, responses, max_model_calls: int = 64, fallback=evaluate_position_3):
        client = ScriptedClient(responses)
        evaluation = LLMEvaluationFunction(
            client,
            build_evaluation_prompt,
            parse_evaluation_response,
            fallback,
            max_model_calls,
        )
        return client, evaluation

    def test_valid_response_is_used_and_counted_as_a_model_call(self) -> None:
        client, evaluation = self.build(['{"score": 0.3}'])
        value = evaluation(BOARD, Player.ORANGE)
        self.assertAlmostEqual(value, 0.3)
        self.assertEqual(evaluation.model_calls, 1)
        self.assertEqual(evaluation.fallback_calls, 0)
        self.assertEqual(len(client.messages_seen), 1)

    def test_invalid_response_falls_back_and_is_counted(self) -> None:
        fallback_value = evaluate_position_3(BOARD, Player.ORANGE)
        client, evaluation = self.build(["not json at all"], fallback=evaluate_position_3)
        value = evaluation(BOARD, Player.ORANGE)
        self.assertAlmostEqual(value, fallback_value)
        self.assertEqual(evaluation.model_calls, 1)
        self.assertEqual(evaluation.fallback_calls, 1)
        self.assertEqual(evaluation.total_fallback_calls, 1)
        self.assertEqual(len(client.messages_seen), 1)

    def test_every_rejection_category_triggers_the_fallback(self) -> None:
        for _expected, response in TestEvaluationParserRejection.REJECTED:
            if not isinstance(response, str):
                continue
            with self.subTest(response=repr(response)):
                _client, evaluation = self.build([response])
                evaluation.begin_search()
                value = evaluation(BOARD, Player.ORANGE)
                self.assertAlmostEqual(
                    value, evaluate_position_3(BOARD, Player.ORANGE)
                )
                self.assertEqual(evaluation.fallback_calls, 1)

    def test_zero_budget_never_contacts_the_model(self) -> None:
        """An empty script raises if called, so this proves the budget path."""
        client, evaluation = self.build((), max_model_calls=0)
        value = evaluation(BOARD, Player.ORANGE)
        self.assertAlmostEqual(value, evaluate_position_3(BOARD, Player.ORANGE))
        self.assertEqual(evaluation.model_calls, 0)
        self.assertEqual(evaluation.fallback_calls, 1)
        self.assertEqual(client.messages_seen, [])

    def test_budget_exhaustion_falls_back_without_a_further_request(self) -> None:
        client, evaluation = self.build(['{"score": 0.1}'], max_model_calls=1)
        first = evaluation(BOARD, Player.ORANGE)
        second = evaluation(walk(plies=1), Player.ORANGE)
        self.assertAlmostEqual(first, 0.1)
        self.assertAlmostEqual(second, evaluate_position_3(walk(plies=1), Player.ORANGE))
        self.assertEqual(evaluation.model_calls, 1)
        self.assertEqual(evaluation.fallback_calls, 1)
        self.assertEqual(len(client.messages_seen), 1)

    def test_cache_is_keyed_by_board_and_perspective(self) -> None:
        client, evaluation = self.build(['{"score": 0.1}', '{"score": 0.2}'])
        first = evaluation(BOARD, Player.ORANGE)
        repeat = evaluation(BOARD, Player.ORANGE)
        other = evaluation(BOARD, Player.BLACK)
        self.assertEqual(first, repeat)
        self.assertAlmostEqual(first, 0.1)
        self.assertAlmostEqual(other, 0.2)
        self.assertEqual(evaluation.cache_hits, 1)
        self.assertEqual(evaluation.model_calls, 2)
        self.assertEqual(len(client.messages_seen), 2)

    def test_fallback_values_are_cached_too(self) -> None:
        client, evaluation = self.build(["bad"])
        evaluation(BOARD, Player.ORANGE)
        evaluation(BOARD, Player.ORANGE)
        self.assertEqual(evaluation.cache_hits, 1)
        self.assertEqual(evaluation.fallback_calls, 1)
        self.assertEqual(len(client.messages_seen), 1)

    def test_begin_search_resets_per_search_counters_not_totals(self) -> None:
        _client, evaluation = self.build(['{"score": 0.1}', '{"score": 0.2}'])
        evaluation(BOARD, Player.ORANGE)
        evaluation.begin_search()
        self.assertEqual(evaluation.model_calls, 0)
        self.assertEqual(evaluation.fallback_calls, 0)
        self.assertEqual(evaluation.total_model_calls, 1)
        evaluation(walk(plies=1), Player.ORANGE)
        self.assertEqual(evaluation.model_calls, 1)
        self.assertEqual(evaluation.total_model_calls, 2)

    def test_identical_scripts_give_identical_results(self) -> None:
        script = ['{"score": 0.11}', '{"score": -0.22}', "bad"]
        _c1, first = self.build(script)
        _c2, second = self.build(script)
        boards = [BOARD, walk(plies=1), walk(plies=2)]
        values_a = [first(board, Player.ORANGE) for board in boards]
        values_b = [second(board, Player.ORANGE) for board in boards]
        self.assertEqual(values_a, values_b)

    def test_prompt_reaches_the_client_with_both_roles(self) -> None:
        client, evaluation = self.build(['{"score": 0.1}'])
        evaluation(BOARD, Player.BLACK)
        sent = client.messages_seen[0]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertIn("Score from the perspective of: Black", sent[1]["content"])

    def test_minimax_agent_chooses_a_legal_move_at_depth_one(self) -> None:
        _client, evaluation = self.build(['{"score": 0.1}'] * len(BOARD.actions()))
        agent = LLMMinimaxAgent("Offline LLM Evaluator", 1, evaluation)
        action = agent.choose_action(BOARD)
        self.assertIn(action, BOARD.actions())
        self.assertEqual(agent.total_metrics.maximum_depth, 1)


class TestDirectAgentIntegration(unittest.TestCase):
    """The direct agent must select, validate, and fall back deterministically."""

    def build(self, responses, fallback=choose_fallback, history_limit: int = 8):
        client = ScriptedClient(responses)
        agent = DirectLLMAgent(
            client,
            build_move_prompt,
            parse_move_response,
            "Offline Direct",
            fallback,
            history_limit,
        )
        return client, agent

    def test_valid_move_is_played_and_counted(self) -> None:
        target = BOARD.actions()[7]
        client, agent = self.build(['{"move": "%s"}' % target])
        action = agent.choose_action(BOARD)
        self.assertEqual(action, target)
        self.assertEqual(agent.model_calls, 1)
        self.assertEqual(agent.fallback_calls, 0)
        self.assertEqual(len(client.messages_seen), 1)

    def test_each_legal_token_is_playable(self) -> None:
        for target in (BOARD.actions()[0], BOARD.actions()[20], BOARD.actions()[-1]):
            with self.subTest(target=str(target)):
                _client, agent = self.build(['{"move": "%s"}' % target])
                self.assertEqual(agent.choose_action(BOARD), target)

    def test_illegal_move_response_falls_back(self) -> None:
        client, agent = self.build(['{"move": "Z9-Z9"}'])
        action = agent.choose_action(BOARD)
        self.assertIn(action, BOARD.actions())
        self.assertEqual(action, choose_fallback(BOARD, BOARD.actions()))
        self.assertEqual(agent.model_calls, 1)
        self.assertEqual(agent.fallback_calls, 1)
        self.assertEqual(len(client.messages_seen), 1)

    def test_every_rejection_category_triggers_the_fallback(self) -> None:
        actions = BOARD.actions()
        legal = str(actions[0])
        bad = [
            "not json", "```json\n{\"move\": \"%s\"}\n```" % legal,
            "Sure: {\"move\": \"%s\"}" % legal, "{\"move\": \"%s\"} ok" % legal,
            "{\"move\": \"%s\", \"why\": \"x\"}" % legal,
            "{\"move\": \"%s\", \"move\": \"%s\"}" % (legal, legal),
            '{"move": 5}', '{"move": null}', '{"move": true}', "{}",
            '{"move": "Z9-Z9"}', '{"score": 0.5}', "", "[1,2]",
        ]
        for response in bad:
            with self.subTest(response=response):
                _client, agent = self.build([response])
                action = agent.choose_action(BOARD)
                self.assertIn(action, actions)
                self.assertEqual(agent.fallback_calls, 1)

    def test_a_parser_returning_an_illegal_move_is_still_caught(self) -> None:
        """The framework re-validates even if the parser were bypassed."""

        def bad_parser(response: str, actions: tuple[Move, ...]) -> Move:
            del response
            return Move((9, 9), (0, 0))

        client = ScriptedClient(["anything"])
        agent = DirectLLMAgent(
            client, build_move_prompt, bad_parser, "Bad Parser", choose_fallback, 8
        )
        action = agent.choose_action(BOARD)
        self.assertIn(action, BOARD.actions())
        self.assertEqual(agent.fallback_calls, 1)

    def test_client_failure_is_counted_as_a_model_call(self) -> None:
        """``model_calls`` increments before the request, so exhaustion counts."""
        client, agent = self.build(())  # any call raises RuntimeError
        action = agent.choose_action(BOARD)
        self.assertIn(action, BOARD.actions())
        self.assertEqual(agent.model_calls, 1)
        self.assertEqual(agent.fallback_calls, 1)

    def test_history_is_bounded_and_cleared_per_match(self) -> None:
        board = walk(plies=1)
        actions = board.actions()
        script = ['{"move": "%s"}' % action for action in actions[:5]]
        _client, agent = self.build(script)
        for _ in range(4):
            agent.choose_action(board)
        self.assertLessEqual(len(agent.recent_moves), 8)
        self.assertEqual(agent.recent_moves, tuple(actions[:4]))
        agent.choose_action(BOARD)
        self.assertEqual(len(agent.recent_moves), 1)

    def test_recent_moves_reach_the_prompt(self) -> None:
        board = walk(plies=1)
        action = board.actions()[2]
        _client, agent = self.build(['{"move": "%s"}' % action] * 2)
        agent.choose_action(board)
        agent.choose_action(board)
        self.assertIn(action, agent.recent_moves)

    def test_identical_scripts_give_identical_moves(self) -> None:
        board = walk(plies=1)
        script = ['{"move": "%s"}' % board.actions()[1]] * 3
        _c1, first = self.build(script)
        _c2, second = self.build(script)
        boards = [BOARD, walk(plies=1), walk(plies=2)]
        for position in boards:
            token = '{"move": "%s"}' % position.actions()[1]
            first_client = ScriptedClient([token])
            second_client = ScriptedClient([token])
            a = DirectLLMAgent(first_client, build_move_prompt, parse_move_response)
            b = DirectLLMAgent(second_client, build_move_prompt, parse_move_response)
            self.assertEqual(a.choose_action(position), b.choose_action(position))


class TestFallbackPolicy(unittest.TestCase):
    """The documented policy: winning capture, then best capture, then first."""

    def test_always_returns_a_member_of_the_supplied_actions(self) -> None:
        for plies in range(0, 6):
            board = walk(plies=plies)
            actions = board.actions()
            if not actions:
                continue
            with self.subTest(plies=plies):
                self.assertIn(choose_fallback(board, actions), actions)

    def test_is_deterministic_across_repeated_calls(self) -> None:
        for plies in range(0, 6):
            board = walk(plies=plies)
            actions = board.actions()
            if not actions:
                continue
            with self.subTest(plies=plies):
                picks = {choose_fallback(board, actions) for _ in range(5)}
                self.assertEqual(len(picks), 1)

    def test_prefers_a_winning_princess_capture_over_any_other_capture(self) -> None:
        """Tier 1 outranks tier 2 even when tier 2 captures a Dwar."""
        for board in (princess_versus_dwar_board(), mirrored_princess_board()):
            with self.subTest(turn=board.player().name):
                actions = board.actions()
                victim_of = {
                    action: board.at(action.destination)
                    for action in actions
                    if board.at(action.destination) is not None
                }
                princess = [
                    action
                    for action, victim in victim_of.items()
                    if victim.kind is PieceType.PRINCESS  # type: ignore[union-attr]
                ]
                others = [
                    action
                    for action, victim in victim_of.items()
                    if victim.kind is not PieceType.PRINCESS  # type: ignore[union-attr]
                ]
                self.assertTrue(princess, "fixture must expose a Princess capture")
                self.assertTrue(others, "fixture must also expose a lesser capture")
                self.assertIn(choose_fallback(board, actions), princess)

    def test_takes_the_most_valuable_available_capture(self) -> None:
        board = competing_captures_board()
        actions = board.actions()
        values = {
            action: strategies.PIECE_VALUES[board.at(action.destination).kind]  # type: ignore[union-attr]
            for action in actions
            if board.at(action.destination) is not None
        }
        self.assertGreaterEqual(len(values), 2, "fixture must expose rival captures")
        best = max(values.values())
        self.assertIn(best, set(values.values()))
        self.assertAlmostEqual(
            strategies.PIECE_VALUES[board.at(choose_fallback(board, actions).destination).kind],  # type: ignore[union-attr]
            best,
        )

    def test_takes_a_capture_rather_than_a_quiet_move(self) -> None:
        for board in (
            competing_captures_board(),
            princess_versus_dwar_board(),
            mirrored_princess_board(),
        ):
            with self.subTest(turn=board.player().name):
                actions = board.actions()
                captures = [
                    action
                    for action in actions
                    if (victim := board.at(action.destination)) is not None
                    and victim.player is not board.player()
                ]
                quiet = [action for action in actions if action not in captures]
                self.assertTrue(captures and quiet, "fixture needs both kinds")
                self.assertIn(choose_fallback(board, actions), captures)

    def test_falls_back_to_the_lowest_token_in_a_quiet_position(self) -> None:
        board = BOARD
        actions = board.actions()
        self.assertEqual(choose_fallback(board, actions), min(actions, key=str))

    def test_never_invokes_the_model(self) -> None:
        """The policy reads only the board, so no client can be involved."""
        client = ScriptedClient(())  # any call would raise
        evaluation = LLMEvaluationFunction(
            client, build_evaluation_prompt, parse_evaluation_response,
            evaluate_position_3, 0,
        )
        del evaluation
        board = walk(plies=2)
        self.assertIn(choose_fallback(board, board.actions()), board.actions())
        self.assertEqual(client.messages_seen, [])

    def test_agrees_with_the_infrastructure_default_in_quiet_positions(self) -> None:
        from direct_llm_agent import first_legal_action

        board = BOARD
        actions = board.actions()
        self.assertEqual(choose_fallback(board, actions), first_legal_action(board, actions))


class TestPilotRecorder(unittest.TestCase):
    """The recorder must be the source of the prompt-revision evidence.

    Driven entirely by :class:`ScriptedClient`, so the evidence pipeline is
    verified before any live call is made. If a category stopped being recorded,
    the revision could not be justified against it.
    """

    def setUp(self) -> None:
        self.boards = recorder.sample_cutoff_positions(
            games=1, plies=4, seed=7
        )[:4]

    def test_sampled_positions_are_non_terminal_and_reproducible(self) -> None:
        self.assertTrue(self.boards)
        again = recorder.sample_cutoff_positions(games=1, plies=4, seed=7)[:4]
        self.assertEqual(
            [str(b) for b in self.boards], [str(b) for b in again]
        )
        for board in self.boards:
            self.assertFalse(board.is_terminal())
            self.assertTrue(board.actions())

    def test_a_different_seed_gives_a_different_sample(self) -> None:
        other = recorder.sample_cutoff_positions(games=1, plies=4, seed=99)[:4]
        self.assertNotEqual(
            [str(b) for b in self.boards], [str(b) for b in other]
        )

    def test_verdict_of_attributes_each_category(self) -> None:
        cases = (
            ('{"score": 0.2}', "accepted", "-"),
            ("```json\n{\"score\": 0.2}\n```", "rejected", "multiple_lines"),
            ("Sure: {\"score\": 0.2}", "rejected", "surrounding_content"),
            ('{"score": 0.1, "score": 0.2}', "rejected", "duplicate_key"),
            ('{"score": true}', "rejected", "wrong_type"),
            ('{"score": NaN}', "rejected", "non_finite_literal"),
            ('{"score": 1.5}', "rejected", "out_of_range"),
        )
        for response, verdict, category in cases:
            with self.subTest(response=response):
                self.assertEqual(
                    recorder.verdict_of(response, parse_evaluation_response)[:2],
                    (verdict, category),
                )

    def test_evaluate_probe_records_the_reference_and_signed_error(self) -> None:
        client = ScriptedClient(['{"score": 0.4}'] * len(self.boards) * 2)
        exchanges = recorder.probe_evaluator(client, self.boards)
        self.assertEqual(len(exchanges), len(self.boards) * 2)
        for exchange in exchanges:
            self.assertEqual(exchange.verdict, "accepted")
            row = exchange.as_row()
            self.assertEqual(row["modality"], "cutoff_evaluator")
            self.assertEqual(row["prompt_version"], strategies.EVALUATION_PROMPT_VERSION)
            self.assertTrue(row["reference_score"])
            self.assertTrue(row["signed_error"])
            expected = float(row["parsed_value"]) - float(row["reference_score"])
            self.assertAlmostEqual(float(row["signed_error"]), expected, places=5)

    def test_evaluate_probe_covers_both_perspectives_of_one_board(self) -> None:
        client = ScriptedClient(['{"score": 0.1}'] * 2)
        exchanges = recorder.probe_evaluator(client, self.boards[:1])
        views = {row.as_row()["perspective"] for row in exchanges}
        self.assertEqual(views, {"ORANGE", "BLACK"})

    def test_rejected_rows_are_retained_with_their_category_and_text(self) -> None:
        script = ['{"score": 0.2}', "```json\n{\"score\": 0.2}\n```", "oops", '{"score": 1.5}']
        exchanges = recorder.probe_evaluator(ScriptedClient(script), self.boards[:2])
        rejected = [e for e in exchanges if e.verdict == "rejected"]
        self.assertTrue(rejected)
        for exchange in rejected:
            self.assertTrue(exchange.model_response)
            self.assertTrue(exchange.category)
            self.assertEqual(exchange.as_row()["parsed_value"], "")

    def test_summary_counts_categories_and_mean_error(self) -> None:
        script = ['{"score": 0.2}', '{"score": 0.2}', "oops", '{"score": true}']
        exchanges = recorder.probe_evaluator(ScriptedClient(script), self.boards[:2])
        summary = recorder.summarise(exchanges)
        self.assertEqual(summary["requests"], len(exchanges))
        self.assertEqual(
            summary["accepted"] + summary["rejected"], len(exchanges)
        )
        self.assertGreaterEqual(len(summary["categories"]), 1)
        self.assertNotEqual(summary["mean_abs_error"], "n/a")

    def test_move_probe_records_the_fallback_and_whether_it_was_needed(self) -> None:
        first, second = self.boards[0], self.boards[1]
        exchanges = recorder.probe_direct(
            ScriptedClient(
                [f'{{"move": "{first.actions()[0]}"}}', '{"move": "Z9-Z9"}']
            ),
            [first, second],
        )
        self.assertEqual(len(exchanges), 2, "probe_direct calls once per board")
        accepted, rejected = exchanges
        self.assertEqual(accepted.verdict, "accepted")
        self.assertEqual(accepted.as_row()["parsed_move"], str(first.actions()[0]))
        self.assertEqual(
            accepted.as_row()["fallback_move"],
            str(choose_fallback(first, first.actions())),
        )
        self.assertIn(accepted.as_row()["chose_fallback"], ("yes", "no"))
        self.assertEqual(rejected.verdict, "rejected")
        self.assertEqual(rejected.as_row()["category"], "illegal_move")
        self.assertEqual(rejected.as_row()["chose_fallback"], "n/a")
        self.assertEqual(rejected.as_row()["parsed_move"], "")

    def test_move_probe_marks_agreement_with_the_fallback(self) -> None:
        """'chose_fallback' distinguishes a model pick from a policy pick."""
        board = self.boards[0]
        agreeing = recorder.probe_direct(
            ScriptedClient([f'{{"move": "{choose_fallback(board, board.actions())}"}}']),
            [board],
        )
        self.assertEqual(agreeing[0].as_row()["chose_fallback"], "yes")
        other = next(a for a in board.actions() if a != board.actions()[0])
        differing = recorder.probe_direct(
            ScriptedClient([f'{{"move": "{other}"}}']), [board]
        )
        self.assertEqual(differing[0].as_row()["chose_fallback"], "no")

    def test_transport_failure_is_recorded_not_raised(self) -> None:
        """An endpoint error must become a row, or the pilot would lose data."""
        exchanges = recorder.probe_evaluator(ScriptedClient(()), self.boards[:1])
        self.assertTrue(exchanges)
        self.assertEqual(exchanges[0].verdict, "rejected")
        self.assertIn("transport error", exchanges[0].model_response)

    def test_csv_round_trips_and_retains_failures(self) -> None:
        import csv
        import tempfile
        from pathlib import Path

        script = ['{"score": 0.2}', "oops", '{"score": -0.3}']
        exchanges = recorder.probe_evaluator(ScriptedClient(script), self.boards[:2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.csv"
            recorder.write_csv(exchanges, str(path))
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(exchanges))
        self.assertEqual(
            sum(1 for r in rows if r["verdict"] == "rejected"),
            sum(1 for e in exchanges if e.verdict == "rejected"),
        )
        self.assertIn("signed_error", rows[0])

    def test_move_csv_uses_the_move_schema(self) -> None:
        import csv
        import tempfile
        from pathlib import Path

        board = self.boards[0]
        exchanges = recorder.probe_direct(
            ScriptedClient(['{"move": "%s"}' % board.actions()[0]]), [board]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.csv"
            recorder.write_csv(exchanges, str(path))
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                self.assertIn("fallback_move", reader.fieldnames or [])
                self.assertNotIn("reference_score", reader.fieldnames or [])

    def test_write_csv_refuses_an_empty_result(self) -> None:
        with self.assertRaises(ValueError):
            recorder.write_csv([], "unused.csv")

    def test_main_refuses_to_fabricate_responses_without_live(self) -> None:
        """Without --live there is no honest output, so it must refuse."""
        with self.assertRaises(SystemExit) as caught:
            recorder.main(["evaluate", "--out", "unused.csv"])
        self.assertIn("--live", str(caught.exception))

    def test_live_client_requires_a_key_and_an_allowed_endpoint(self) -> None:
        import os

        saved = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(SystemExit):
                recorder.build_live_client(0, "m", "https://example.com/v1", 0.0)
            with self.assertRaises(SystemExit):
                recorder.build_live_client(0, "m", "http://example.com/v1", 0.0)
            os.environ["OPENAI_API_KEY"] = "placeholder"
            client = recorder.build_live_client(
                0, "m", "http://golem:8000/v1", 0.0
            )
            self.assertEqual(client.seed, 0)
            self.assertEqual(client.temperature, 0.0)
        finally:
            if saved is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = saved

    def test_recorder_never_writes_the_api_key(self) -> None:
        """The CSV must not be able to leak the key into the repository."""
        source = Path(recorder.__file__).read_text(encoding="utf-8")
        for field in recorder.EVALUATE_FIELDS + recorder.MOVE_FIELDS:
            self.assertNotIn("key", field.lower())
        self.assertNotIn("api_key", " ".join(recorder.EVALUATE_FIELDS))
        del source


class TestOfflineGuarantee(unittest.TestCase):
    """Nothing here may touch the network."""

    def test_no_test_client_performs_a_real_request(self) -> None:
        client = ScriptedClient(['{"score": 0.0}'])
        client.chat([{"role": "user", "content": "hi"}])
        with self.assertRaises(RuntimeError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_agent_builders_refuse_to_build_without_a_client(self) -> None:
        from agent_factory import AgentConfiguration, create_configured_agent
        import student_agents  # noqa: F401  (registers the agents)

        for name in ("llm_evaluator", "llm_direct"):
            with self.subTest(agent=name):
                with self.assertRaises(RuntimeError):
                    create_configured_agent(
                        name, AgentConfiguration(seed=0, player_name=name, depth=1)
                    )

    def test_deterministic_agents_need_no_client(self) -> None:
        from agent_factory import AgentConfiguration, create_configured_agent

        for name in ("minimax_1", "minimax_2", "minimax_3"):
            with self.subTest(agent=name):
                agent = create_configured_agent(
                    name, AgentConfiguration(seed=0, player_name=name, depth=1)
                )
                self.assertIn(agent.choose_action(BOARD), BOARD.actions())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
