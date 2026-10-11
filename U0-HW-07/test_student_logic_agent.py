"""Student-authored unit tests for LogicAgent knowledge, search, and planning."""

import unittest

from agents import LogicAgent
from proplogic import Literal
from rover import (
    Action,
    ActionKind,
    ActionOutcome,
    Event,
    Percept,
    Position,
    ScanObservation,
    Scanner,
)
from run_experiment import run_scenario
from scenarios import training_scenarios

ROWS, COLUMNS = 4, 5
START = Position(0, 0)
MODULE = Position(2, 2)
EXIT = Position(3, 4)


def make_percept(
    action_count: int = 0,
    observation: ScanObservation | None = None,
    position: Position = START,
    traction: bool = False,
    carrying: bool = False,
) -> Percept:
    outcome = None
    if observation is not None:
        outcome = ActionOutcome(True, Event.SCANNED, -2, observation)
    return Percept(
        rows=ROWS,
        columns=COLUMNS,
        position=position,
        module_position=MODULE,
        exit_position=EXIT,
        traction_enabled=traction,
        carrying_module=carrying,
        action_count=action_count,
        last_outcome=outcome,
    )


def structural(positive: bool, *tiles: Position) -> ScanObservation:
    return ScanObservation(Scanner.STRUCTURAL, tuple(tiles), positive)


def surface(positive: bool, *tiles: Position) -> ScanObservation:
    return ScanObservation(Scanner.SURFACE, tuple(tiles), positive)


def row_window(row: int, column: int) -> tuple[Position, ...]:
    return tuple(Position(row, column + k) for k in range(3))


def square(row: int, column: int) -> tuple[Position, ...]:
    return (
        Position(row, column),
        Position(row, column + 1),
        Position(row + 1, column),
        Position(row + 1, column + 1),
    )


class AgentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = LogicAgent(4300)
        self.count = 0
        # The first percept initializes the knowledge base.
        self.agent.act(make_percept())

    def observe(self, observation: ScanObservation) -> None:
        """Deliver one scan outcome to the agent through act()."""
        self.count += 1
        self.agent.act(make_percept(self.count, observation))

    def entails(self, formula: str) -> bool:
        return self.agent.knowledge.entails(formula)

    def isolate_slippery_2_1(self) -> None:
        """Positive 2x2 over (1,1)-(2,2); negative scans clear all but (2,1)."""
        self.observe(surface(True, *square(1, 1)))
        self.observe(surface(False, *square(0, 1)))  # clears (1,1) and (1,2)
        self.observe(surface(False, *square(2, 2)))  # clears (2,2)

    def all_tiles(self) -> set[Position]:
        return {Position(r, c) for r in range(ROWS) for c in range(COLUMNS)}


class StructuralScanTranslationTests(AgentTestCase):
    def test_negative_structural_scan_clears_every_tile(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        for tile in row_window(1, 0):
            self.assertTrue(self.entails(f"NOT fragile_{tile.row}_{tile.column}"))

    def test_positive_structural_scan_adds_a_disjunction_only(self) -> None:
        self.observe(structural(True, *row_window(1, 0)))
        self.assertTrue(self.entails("fragile_1_0 OR fragile_1_1 OR fragile_1_2"))
        for tile in row_window(1, 0):
            self.assertFalse(self.entails(f"fragile_{tile.row}_{tile.column}"))
            self.assertFalse(self.entails(f"NOT fragile_{tile.row}_{tile.column}"))


class SurfaceScanTranslationTests(AgentTestCase):
    def test_negative_surface_scan_clears_every_tile(self) -> None:
        self.observe(surface(False, *square(1, 1)))
        for tile in square(1, 1):
            self.assertTrue(self.entails(f"NOT slippery_{tile.row}_{tile.column}"))

    def test_positive_surface_scan_adds_a_disjunction_only(self) -> None:
        self.observe(surface(True, *square(1, 1)))
        self.assertTrue(
            self.entails("slippery_1_1 OR slippery_1_2 OR slippery_2_1 OR slippery_2_2")
        )
        for tile in square(1, 1):
            self.assertFalse(self.entails(f"slippery_{tile.row}_{tile.column}"))
            self.assertFalse(self.entails(f"NOT slippery_{tile.row}_{tile.column}"))


class InferenceTests(AgentTestCase):
    def test_overlapping_scans_isolate_the_fragile_tile(self) -> None:
        # (1,0)-(1,2) has a fragile tile; (1,1)-(1,3) does not, so it must be (1,0)
        self.observe(structural(True, *row_window(1, 0)))
        self.assertFalse(self.entails("fragile_1_0"))
        self.observe(structural(False, *row_window(1, 1)))
        self.assertTrue(self.entails("fragile_1_0"))

    def test_overlapping_surface_scans_isolate_the_slippery_tile(self) -> None:
        self.observe(surface(True, *square(1, 1)))
        self.assertFalse(self.entails("slippery_2_1"))
        self.observe(surface(False, *square(0, 1)))
        self.observe(surface(False, *square(2, 2)))
        self.assertTrue(self.entails("slippery_2_1"))

    def test_slippery_evidence_implies_not_fragile_and_not_stable(self) -> None:
        self.isolate_slippery_2_1()
        self.assertTrue(self.entails("NOT fragile_2_1"))
        self.assertTrue(self.entails("NOT stable_2_1"))

    def test_unknown_tile_stays_unknown(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        for kind in ("fragile", "slippery", "stable"):
            self.assertFalse(self.entails(f"{kind}_3_3"))
            self.assertFalse(self.entails(f"NOT {kind}_3_3"))

    def test_start_tile_is_stable(self) -> None:
        self.assertTrue(self.entails("stable_0_0"))
        self.assertTrue(self.entails("NOT fragile_0_0"))
        self.assertTrue(self.entails("NOT slippery_0_0"))

    def test_exactly_one_terrain_category_per_tile(self) -> None:
        self.observe(structural(False, *row_window(2, 0)))
        self.observe(surface(False, *square(2, 0)))
        # Not fragile and not slippery leaves only stable.
        self.assertTrue(self.entails("stable_2_0"))


class ConsistencyTests(AgentTestCase):
    def test_knowledge_is_consistent_after_initialization(self) -> None:
        self.assertTrue(self.agent.knowledge.is_consistent())

    def test_knowledge_is_consistent_after_representative_updates(self) -> None:
        updates = (
            structural(False, *row_window(0, 0)),
            structural(True, *row_window(1, 0)),
            structural(False, *row_window(1, 1)),
            surface(True, *square(0, 0)),
            surface(False, *square(2, 2)),
        )
        for observation in updates:
            self.observe(observation)
            self.assertTrue(self.agent.knowledge.is_consistent())

    def test_contradictory_observation_raises(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        with self.assertRaises(RuntimeError):
            self.observe(structural(True, *row_window(1, 0)))


class RepeatedPerceptAndResetTests(AgentTestCase):
    def test_replayed_percept_is_processed_once(self) -> None:
        percept = make_percept(5, structural(False, *row_window(1, 0)))
        self.agent.act(percept)
        clauses_after_first = self.agent.knowledge.clauses
        self.agent.act(percept)
        self.assertEqual(self.agent.knowledge.clauses, clauses_after_first)
        self.assertEqual(self.agent.last_processed_action, 5)

    def test_reset_clears_knowledge_and_plan_state(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        self.agent.reset()

        self.assertEqual(self.agent.knowledge.clauses, ())
        self.assertFalse(self.agent.initialized)
        self.assertEqual(self.agent.last_processed_action, -1)
        self.assertEqual(self.agent.scanned_regions, set())
        self.assertEqual(self.agent.planned_actions, [])
        self.assertIsNone(self.agent.route)

    def test_reset_agent_forgets_prior_observations(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        self.agent.reset()
        self.agent.act(make_percept())
        self.assertFalse(self.entails("NOT fragile_1_0"))


class TerrainQueryTests(AgentTestCase):
    def test_traversable_graph_requires_entailed_non_fragile(self) -> None:
        self.observe(structural(False, *row_window(1, 0)))
        self.observe(structural(True, *row_window(2, 0)))
        safe = self.agent._build_traversable_graph(make_percept())

        self.assertIn(START, safe)  # known stable start
        for tile in row_window(1, 0):
            self.assertIn(tile, safe)
        for tile in row_window(2, 0):  # possibly fragile
            self.assertNotIn(tile, safe)
        self.assertNotIn(Position(3, 3), safe)  # unknown

    def test_tile_is_slippery_requires_entailment(self) -> None:
        self.isolate_slippery_2_1()
        self.assertTrue(self.agent._tile_is_slippery(Position(2, 1)))
        self.assertFalse(self.agent._tile_is_slippery(Position(1, 1)))  # cleared
        self.assertFalse(self.agent._tile_is_slippery(Position(3, 3)))  # unknown

    def test_known_not_slippery_requires_entailment(self) -> None:
        self.observe(surface(False, *square(1, 1)))
        self.assertTrue(self.agent._known_not_slippery(Position(1, 1)))
        self.assertFalse(self.agent._known_not_slippery(Position(3, 3)))  # unknown

    def test_traction_is_needed_unless_known_not_slippery(self) -> None:
        self.observe(surface(False, *square(1, 1)))
        self.observe(surface(True, *square(2, 3)))
        self.assertFalse(self.agent._needs_traction(Position(1, 1)))  # cleared
        self.assertTrue(self.agent._needs_traction(Position(2, 3)))  # maybe slippery
        self.assertTrue(self.agent._needs_traction(Position(3, 0)))  # unknown

    def test_query_cache_is_refreshed_after_new_evidence(self) -> None:
        self.assertTrue(self.agent._needs_traction(Position(1, 1)))
        self.observe(surface(False, *square(1, 1)))
        self.assertFalse(self.agent._needs_traction(Position(1, 1)))


class GraphSearchTests(AgentTestCase):
    def test_finds_shortest_path_on_open_map(self) -> None:
        path = self.agent._find_path(START, MODULE, self.all_tiles())
        self.assertEqual(path[0], START)
        self.assertEqual(path[-1], MODULE)
        self.assertEqual(len(path) - 1, 4)  # Manhattan distance
        for a, b in zip(path, path[1:]):
            self.assertEqual(abs(a.row - b.row) + abs(a.column - b.column), 1)

    def test_path_avoids_excluded_tiles(self) -> None:
        blocked = {Position(1, 0), Position(0, 1)}
        safe = self.all_tiles() - blocked
        # Start is walled in: no route exists.
        self.assertIsNone(self.agent._find_path(START, MODULE, safe))

    def test_path_detours_around_a_fragile_tile(self) -> None:
        safe = self.all_tiles() - {Position(1, 0), Position(1, 1)}
        path = self.agent._find_path(START, Position(2, 0), safe)
        self.assertIsNotNone(path)
        self.assertNotIn(Position(1, 0), path)
        self.assertNotIn(Position(1, 1), path)
        self.assertEqual(path[-1], Position(2, 0))

    def test_unreachable_goal_returns_none(self) -> None:
        safe = self.all_tiles() - {MODULE}
        self.assertIsNone(self.agent._find_path(START, MODULE, safe))

    def test_start_equals_goal(self) -> None:
        self.assertEqual(self.agent._find_path(START, START, self.all_tiles()), [START])

    def test_plan_mission_joins_both_legs_through_module(self) -> None:
        route = self.agent._plan_mission(START, MODULE, EXIT, self.all_tiles())
        self.assertEqual(route[0], START)
        self.assertEqual(route[-1], EXIT)
        self.assertEqual(route.count(MODULE), 1)
        self.assertEqual(len(route) - 1, 4 + 3)

    def test_plan_mission_fails_if_either_leg_is_blocked(self) -> None:
        self.assertIsNone(
            self.agent._plan_mission(START, MODULE, EXIT, self.all_tiles() - {EXIT})
        )
        self.assertIsNone(
            self.agent._plan_mission(START, MODULE, EXIT, self.all_tiles() - {MODULE})
        )


class RouteToActionTests(AgentTestCase):
    ROUTE = [
        Position(0, 0),
        Position(1, 0),
        Position(2, 0),
        Position(2, 1),
        Position(2, 2),
        Position(3, 2),
        Position(3, 3),
        Position(3, 4),
    ]

    def clear_all_slippery(self) -> None:
        for tile in self.ROUTE[1:]:
            self.observe(surface(False, *square(min(tile.row, ROWS - 2), min(tile.column, COLUMNS - 2))))

    def kinds(self, actions) -> list[ActionKind]:
        return [action.kind for action in actions]

    def test_known_stable_route_needs_no_traction(self) -> None:
        self.clear_all_slippery()
        actions = self.agent._path_to_actions(self.ROUTE, MODULE, EXIT)
        kinds = self.kinds(actions)
        self.assertNotIn(ActionKind.ENABLE_TRACTION, kinds)
        self.assertNotIn(ActionKind.DISABLE_TRACTION, kinds)
        self.assertEqual(kinds.count(ActionKind.MOVE), len(self.ROUTE) - 1)

    def test_moves_follow_the_route_in_order(self) -> None:
        self.clear_all_slippery()
        actions = self.agent._path_to_actions(self.ROUTE, MODULE, EXIT)
        targets = [a.target for a in actions if a.kind is ActionKind.MOVE]
        self.assertEqual(targets, self.ROUTE[1:])

    def test_unknown_tiles_get_traction_before_the_move(self) -> None:
        actions = self.agent._path_to_actions(self.ROUTE, MODULE, EXIT)
        self.assertIs(actions[0].kind, ActionKind.ENABLE_TRACTION)
        self.assertIs(actions[1].kind, ActionKind.MOVE)

    def test_traction_is_enabled_and_disabled_around_a_slippery_tile(self) -> None:
        # (2,1) is slippery; every other tile on the route is cleared.
        self.isolate_slippery_2_1()
        for tile in (Position(1, 0), Position(2, 0), Position(3, 2), Position(3, 3), Position(3, 4)):
            self.agent.knowledge.add_fact(Literal(f"slippery_{tile.row}_{tile.column}", False))
        self.agent._no_slip_cache.clear()
        self.assertTrue(self.agent.knowledge.is_consistent())

        self.assertTrue(self.agent._needs_traction(Position(2, 1)))
        for tile in (Position(1, 0), Position(2, 0), Position(2, 2), Position(3, 2)):
            self.assertFalse(self.agent._needs_traction(tile), tile)

        actions = self.agent._path_to_actions(self.ROUTE, MODULE, EXIT)
        kinds = self.kinds(actions)
        enable = kinds.index(ActionKind.ENABLE_TRACTION)
        disable = kinds.index(ActionKind.DISABLE_TRACTION)
        self.assertEqual(actions[enable + 1].target, Position(2, 1))
        self.assertEqual(actions[disable + 1].target, Position(2, 2))
        self.assertEqual(kinds.count(ActionKind.ENABLE_TRACTION), 1)
        self.assertEqual(kinds.count(ActionKind.DISABLE_TRACTION), 1)

    def test_traction_state_is_consistent_through_the_plan(self) -> None:
        actions = self.agent._path_to_actions(self.ROUTE, MODULE, EXIT)
        traction = False
        for action in actions:
            if action.kind is ActionKind.ENABLE_TRACTION:
                self.assertFalse(traction)  # no redundant toggle
                traction = True
            elif action.kind is ActionKind.DISABLE_TRACTION:
                self.assertTrue(traction)
                traction = False


class RetrievalAndStopTests(AgentTestCase):
    def actions_for(self, route) -> list[ActionKind]:
        return [a.kind for a in self.agent._path_to_actions(route, MODULE, EXIT)]

    def full_route(self):
        return self.agent._plan_mission(START, MODULE, EXIT, self.all_tiles())

    def test_retrieve_happens_exactly_once_at_the_module(self) -> None:
        actions = self.agent._path_to_actions(self.full_route(), MODULE, EXIT)
        kinds = [a.kind for a in actions]
        self.assertEqual(kinds.count(ActionKind.RETRIEVE), 1)
        retrieve = kinds.index(ActionKind.RETRIEVE)
        previous_move = max(i for i, k in enumerate(kinds[:retrieve]) if k is ActionKind.MOVE)
        self.assertEqual(actions[previous_move].target, MODULE)

    def test_stop_is_last_and_only_after_retrieval(self) -> None:
        kinds = self.actions_for(self.full_route())
        self.assertIs(kinds[-1], ActionKind.STOP)
        self.assertEqual(kinds.count(ActionKind.STOP), 1)
        self.assertLess(kinds.index(ActionKind.RETRIEVE), kinds.index(ActionKind.STOP))

    def test_stop_ends_on_the_exit_tile(self) -> None:
        route = self.full_route()
        actions = self.agent._path_to_actions(route, MODULE, EXIT)
        last_move = [a for a in actions if a.kind is ActionKind.MOVE][-1]
        self.assertEqual(last_move.target, EXIT)

    def test_no_stop_when_route_ends_before_the_exit(self) -> None:
        route = self.agent._find_path(START, MODULE, self.all_tiles())
        kinds = self.actions_for(route)
        self.assertIn(ActionKind.RETRIEVE, kinds)
        self.assertNotIn(ActionKind.STOP, kinds)

    def test_no_stop_or_retrieve_when_module_is_skipped(self) -> None:
        route = self.agent._find_path(START, EXIT, self.all_tiles() - {MODULE})
        kinds = self.actions_for(route)
        self.assertNotIn(ActionKind.RETRIEVE, kinds)
        self.assertNotIn(ActionKind.STOP, kinds)


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_gives_same_path_and_score(self) -> None:
        for scenario in training_scenarios():
            with self.subTest(scenario=scenario.identifier):
                first = run_scenario(lambda s: LogicAgent(s), scenario, seed=4300)
                second = run_scenario(lambda s: LogicAgent(s), scenario, seed=4300)
                path = lambda result: [
                    row.action.target
                    for row in result.trace
                    if row.action.kind is ActionKind.MOVE
                ]
                self.assertEqual(path(first), path(second))
                self.assertEqual(first.score, second.score)

    def test_one_agent_reused_across_episodes_matches_a_fresh_agent(self) -> None:
        scenario = training_scenarios()[0]
        shared = LogicAgent(4300)
        run_scenario(lambda s: shared, scenario, seed=4300)
        reused = run_scenario(lambda s: shared, scenario, seed=4300)
        fresh = run_scenario(lambda s: LogicAgent(s), scenario, seed=4300)
        self.assertEqual(reused.score, fresh.score)


if __name__ == "__main__":
    unittest.main()
