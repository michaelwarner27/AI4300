"""Agent protocol and provided Rescue Rover baselines."""

from __future__ import annotations

import heapq
import random
from collections import deque
from typing import Protocol

from proplogic import KnowledgeBase, Clause, Literal, atom
from rover import Action, Percept, Position, Scanner


class RoverAgent(Protocol):
    def reset(self) -> None:
        """Clear episode-specific state."""

    def act(self, percept: Percept) -> Action:
        """Choose one action from the current percept and observation history."""


class StopAgent:
    """Minimal interface example that immediately ends the mission."""

    def reset(self) -> None:
        pass

    def act(self, percept: Percept) -> Action:
        del percept
        return Action.stop()


class RandomAgent:
    """Seeded baseline that does not reason about scanner evidence."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def reset(self) -> None:
        pass

    def act(self, percept: Percept) -> Action:
        actions = self._candidate_actions(percept)
        return self._rng.choice(actions)

    def _candidate_actions(self, percept: Percept) -> list[Action]:
        actions = [Action.retrieve(), Action.stop()]
        actions.append(
            Action.disable_traction()
            if percept.traction_enabled
            else Action.enable_traction()
        )

        row = percept.position.row
        column = percept.position.column
        for candidate in (
            Position(row - 1, column),
            Position(row + 1, column),
            Position(row, column - 1),
            Position(row, column + 1),
        ):
            if 0 <= candidate.row < percept.rows and 0 <= candidate.column < percept.columns:
                actions.append(Action.move(candidate))

        for scan_row in range(percept.rows):
            for scan_column in range(percept.columns - 2):
                actions.append(
                    Action.structural_scan(
                        *(Position(scan_row, scan_column + offset) for offset in range(3))
                    )
                )
        for scan_row in range(percept.rows - 2):
            for scan_column in range(percept.columns):
                actions.append(
                    Action.structural_scan(
                        *(Position(scan_row + offset, scan_column) for offset in range(3))
                    )
                )
        for scan_row in range(percept.rows - 1):
            for scan_column in range(percept.columns - 1):
                actions.append(
                    Action.surface_scan(
                        Position(scan_row, scan_column),
                        Position(scan_row, scan_column + 1),
                        Position(scan_row + 1, scan_column),
                        Position(scan_row + 1, scan_column + 1),
                    )
                )
        return actions


class LogicAgent:
    """Required knowledge-based rover agent implemented by students."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.reset() 

    def reset(self) -> None:
        self.knowledge = KnowledgeBase()
        self.initialized = False
        self.last_processed_action = -1
        self.scanned_regions = set()  # Track which regions have been scanned
        self.planned_actions = []  # Store planned action sequence
        self.action_index = 0  # Current position in planned sequence
        self.route = None  # Tile path from A* (start -> module -> exit)
        self.plan_finalized = False  # True once surface scanning is finished
        self._no_slip_cache = {}  # Per-observation cache of KB queries

    def act(self, percept: Percept) -> Action:
        """Update propositional knowledge and choose one justified action."""
        # Initialize knowledge base on first percept
        if not self.initialized:
            self._initialize_knowledge(percept)
            self.initialized = True
        
        # Process new scan observations (avoid processing the same percept twice)
        if percept.last_outcome and percept.last_outcome.scan and percept.action_count > self.last_processed_action:
            self.last_processed_action = percept.action_count
            obs = percept.last_outcome.scan
            
            # Track scanned regions
            region_key = tuple(sorted((tile.row, tile.column) for tile in obs.region))
            self.scanned_regions.add(region_key)
            
            if obs.scanner == Scanner.STRUCTURAL:
                if obs.positive:
                    # At least one fragile tile exists in the region
                    self.knowledge.add_clause(*(Literal(str(f"fragile_{tile.row}_{tile.column}"), True) for tile in obs.region))
                else:
                    # All tiles are NOT fragile (negated facts)
                    for tile in obs.region:
                        self.knowledge.add_fact(Literal(str(f"fragile_{tile.row}_{tile.column}"), False))
            
            elif obs.scanner == Scanner.SURFACE:
                if obs.positive:
                    # At least one slippery tile exists in the region
                    self.knowledge.add_clause(*(Literal(str(f"slippery_{tile.row}_{tile.column}"), True) for tile in obs.region))
                else:
                    # All tiles are NOT slippery (negated facts)
                    for tile in obs.region:
                        self.knowledge.add_fact(Literal(str(f"slippery_{tile.row}_{tile.column}"), False))
            
            self._no_slip_cache.clear()

            # Verify knowledge base consistency after update
            if not self.knowledge.is_consistent():
                raise RuntimeError(f"Knowledge base inconsistency detected after processing scan observation: {obs}")
        
        # Phase 1: structural scans over the whole map
        structural = self._next_structural_scan(percept)
        if structural:
            return structural

        # Phase 2: plan the route over entailed non-fragile tiles
        if self.route is None:
            if not self.knowledge.is_consistent():
                raise RuntimeError("Knowledge base is inconsistent before planning")
            safe_tiles = self._build_traversable_graph(percept)
            self.route = self._plan_mission(
                percept.position, percept.module_position, percept.exit_position, safe_tiles
            ) or []

        # Phase 3: surface scan only the route's tiles, then fix the actions
        if not self.plan_finalized:
            surface = self._next_surface_scan(percept)
            if surface:
                return surface
            if not self.knowledge.is_consistent():
                raise RuntimeError("Knowledge base is inconsistent before execution")
            if self.route:
                self.planned_actions = self._path_to_actions(
                    self.route, percept.module_position, percept.exit_position
                )
            self.action_index = 0
            self.plan_finalized = True

        # Execute planned actions
        if self.action_index < len(self.planned_actions):
            action = self.planned_actions[self.action_index]
            self.action_index += 1
            return action
        
        # No plan or plan exhausted - stop
        return Action.stop()
    
    def _initialize_knowledge(self, percept: Percept) -> None:
        """Add terrain rules and known facts to the knowledge base."""
        # For each tile, add constraints:
        # 1. At least one category must be true
        # 2. No two categories can be true simultaneously
        for row in range(percept.rows):
            for column in range(percept.columns):
                # At least one category is true
                self.knowledge.add_clause(
                    Literal(f"stable_{row}_{column}", True),
                    Literal(f"slippery_{row}_{column}", True),
                    Literal(f"fragile_{row}_{column}", True),
                )
                
                # Mutually exclusive pairs (using add_clause for disjunctions)
                # ~stable OR ~slippery
                self.knowledge.add_clause(
                    Literal(f"stable_{row}_{column}", False),
                    Literal(f"slippery_{row}_{column}", False),
                )
                # ~stable OR ~fragile
                self.knowledge.add_clause(
                    Literal(f"stable_{row}_{column}", False),
                    Literal(f"fragile_{row}_{column}", False),
                )
                # ~slippery OR ~fragile
                self.knowledge.add_clause(
                    Literal(f"slippery_{row}_{column}", False),
                    Literal(f"fragile_{row}_{column}", False),
                )
        
        # The starting tile is stable
        start_row, start_col = percept.position.row, percept.position.column
        self.knowledge.add_fact(Literal(f"stable_{start_row}_{start_col}", True))
    
    def _next_structural_scan(self, percept: Percept) -> Action | None:
        """Return the next unscanned structural region, or None when done."""
        windows = []
        for r in range(percept.rows):
            for c in range(percept.columns - 2):
                windows.append(tuple(Position(r, c + k) for k in range(3)))
        for r in range(percept.rows - 2):
            for c in range(percept.columns):
                windows.append(tuple(Position(r + k, c) for k in range(3)))
        for region in windows:
            if self._region_key(region) not in self.scanned_regions:
                return Action.structural_scan(*region)
        return None

    def _next_surface_scan(self, percept: Percept) -> Action | None:
        """Pick the 2x2 scan covering the most route tiles with unknown slipperiness.

        Returns None when every route tile is resolved or the remaining action
        budget is needed to execute the current plan.
        """
        unknown = self._unknown_route_tiles()
        if not unknown:
            return None

        # Reserve = actual moves + retrieve + stop + the worst-case number of
        # traction toggles over every possible outcome for the unknown tiles.
        reserve = (len(self.route) - 1) + 2 + self._worst_case_toggles()
        if 100 - percept.action_count - 1 < reserve:
            return None

        best_region, best_count = None, 0
        for r in range(percept.rows - 1):
            for c in range(percept.columns - 1):
                region = (
                    Position(r, c), Position(r, c + 1),
                    Position(r + 1, c), Position(r + 1, c + 1),
                )
                if self._region_key(region) in self.scanned_regions:
                    continue
                count = sum(1 for tile in region if tile in unknown)
                if count > best_count:
                    best_region, best_count = region, count
        if best_region is None:
            return None
        return Action.surface_scan(*best_region)

    def _worst_case_toggles(self) -> int:
        """Most traction toggles the route could need as unknown tiles resolve.

        Traction starts off and toggles whenever the need changes between
        consecutive entered tiles. Unknown tiles may resolve either way, so a
        small DP finds the assignment that maximizes the number of changes.
        """
        # best[v] = max toggles so far with traction currently in state v
        best = {False: 0, True: None}
        for tile in self.route[1:]:
            if self._known_not_slippery(tile):
                options = (False,)
            elif self._tile_is_slippery(tile):
                options = (True,)
            else:
                options = (False, True)
            nxt = {False: None, True: None}
            for new_state in options:
                for old_state, count in best.items():
                    if count is None:
                        continue
                    total = count + (old_state != new_state)
                    if nxt[new_state] is None or total > nxt[new_state]:
                        nxt[new_state] = total
            best = nxt
        return max(c for c in best.values() if c is not None)

    @staticmethod
    def _region_key(region) -> tuple:
        return tuple(sorted((tile.row, tile.column) for tile in region))

    def _unknown_route_tiles(self) -> set[Position]:
        """Route tiles that will be entered and whose slipperiness is undecided."""
        return {
            tile for tile in self.route[1:]
            if not self._known_not_slippery(tile) and not self._tile_is_slippery(tile)
        }

    def _build_traversable_graph(self, percept: Percept) -> set[Position]:
        """Build set of non-fragile tiles that can be traversed."""
        safe_tiles = set()
        for row in range(percept.rows):
            for column in range(percept.columns):
                # A tile is safe if we can entail that it's NOT fragile
                if self.knowledge.entails(f"NOT fragile_{row}_{column}"):
                    safe_tiles.add(Position(row, column))
        return safe_tiles
    
    def _tile_is_slippery(self, pos: Position) -> bool:
        """Check if a tile is known to be slippery."""
        key = ("slippery", pos)
        if key not in self._no_slip_cache:
            self._no_slip_cache[key] = self.knowledge.entails(f"slippery_{pos.row}_{pos.column}")
        return self._no_slip_cache[key]

    def _known_not_slippery(self, pos: Position) -> bool:
        """Check if the KB entails that a tile is not slippery."""
        key = ("not_slippery", pos)
        if key not in self._no_slip_cache:
            self._no_slip_cache[key] = self.knowledge.entails(f"NOT slippery_{pos.row}_{pos.column}")
        return self._no_slip_cache[key]

    def _needs_traction(self, pos: Position) -> bool:
        """Use traction unless the tile is entailed not slippery (safe default)."""
        return not self._known_not_slippery(pos)
    
    def _manhattan_distance(self, pos: Position, goal: Position) -> int:
        """Calculate Manhattan distance heuristic."""
        return abs(pos.row - goal.row) + abs(pos.column - goal.column)
    
    def _find_path(self, start: Position, goal: Position, safe_tiles: set[Position]) -> list[Position] | None:
        """Find minimum-cost path from start to goal using A* search.
        
        Movement costs:
        - Basic move: 3 points
        - Entering stable with traction: 3 additional
        - Entering slippery with traction: 0 additional
        
        Returns list of positions from start to goal (inclusive), or None if no path exists.
        """
        if start not in safe_tiles or goal not in safe_tiles:
            return None
        
        # Priority queue: (f_score, counter, position, path)
        counter = 0
        open_set = [(0, counter, start, [start])]
        closed_set = set()
        g_scores = {start: 0}  # Cost from start to each node
        
        while open_set:
            f_score, _, current, path = heapq.heappop(open_set)
            
            if current in closed_set:
                continue
            
            closed_set.add(current)
            
            # Goal reached
            if current == goal:
                return path
            
            # Explore neighbors (up, down, left, right)
            neighbors = [
                Position(current.row - 1, current.column),
                Position(current.row + 1, current.column),
                Position(current.row, current.column - 1),
                Position(current.row, current.column + 1),
            ]
            
            for neighbor in neighbors:
                if neighbor not in safe_tiles or neighbor in closed_set:
                    continue
                
                # Calculate cost to enter this neighbor
                # Basic move cost is 3
                move_cost = 3
                
                # g(neighbor) = g(current) + cost to move to neighbor
                tentative_g = g_scores[current] + move_cost
                
                if neighbor not in g_scores or tentative_g < g_scores[neighbor]:
                    g_scores[neighbor] = tentative_g
                    
                    # h(neighbor) = Manhattan distance to goal
                    h_score = self._manhattan_distance(neighbor, goal)
                    f_score = tentative_g + h_score
                    
                    counter += 1
                    new_path = path + [neighbor]
                    heapq.heappush(open_set, (f_score, counter, neighbor, new_path))
        
        # No path found
        return None
    
    def _plan_mission(self, start: Position, module_pos: Position, exit_pos: Position, safe_tiles: set[Position]) -> list[Position] | None:
        """Plan two-leg route: start -> module -> exit.
        
        Returns combined path, or None if either leg is not possible.
        """
        # Find path to module
        leg1 = self._find_path(start, module_pos, safe_tiles)
        if not leg1:
            return None
        
        # Find path from module to exit
        leg2 = self._find_path(module_pos, exit_pos, safe_tiles)
        if not leg2:
            return None
        
        # Combine paths (remove duplicate module position)
        return leg1[:-1] + leg2
    
    def _path_to_actions(self, path: list[Position], module_pos: Position, exit_pos: Position) -> list[Action]:
        """Convert a position path to a sequence of rover actions.
        
        Handles:
        - Moving to each position
        - Enabling/disabling traction for slippery tiles
        - Retrieving module at module position
        - Stopping at exit with module
        """
        actions = []
        carrying_module = False
        traction_enabled = False
        
        for i, next_pos in enumerate(path[1:]):  # Skip start position
            current_pos = path[i]
            
            # Traction is required unless the tile is entailed not slippery
            is_slippery = self._needs_traction(next_pos)
            
            # Handle traction toggling
            if is_slippery and not traction_enabled:
                # Need traction to cross slippery safely
                actions.append(Action.enable_traction())
                traction_enabled = True
            elif not is_slippery and traction_enabled:
                # Disable traction on stable ground for efficiency
                actions.append(Action.disable_traction())
                traction_enabled = False
            
            # Move to next position
            actions.append(Action.move(next_pos))
            
            # Retrieve module if we've reached it
            if next_pos == module_pos and not carrying_module:
                actions.append(Action.retrieve())
                carrying_module = True
        
        # Stop at exit (should be last position in path and should be carrying module)
        if path[-1] == exit_pos and carrying_module:
            actions.append(Action.stop())
        
        return actions

