"""Student alpha-beta scaffold and shared Assignment 6 search metrics."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from alpha_beta_ordering import OrderingFunction
from evaluations import EvaluationFunction
from jetan import JetanBoard, Move, Player


@dataclass(frozen=True)
class SearchMetrics:
    """Work performed during one search, with the root at depth zero."""

    visited_states: int = 0
    expanded_nodes: int = 0
    generated_actions: int = 0
    evaluated_states: int = 0
    pruned_actions: int = 0
    maximum_depth: int = 0
    thinking_time: float = 0.0

    @property
    def generated(self) -> int:
        return self.generated_actions

    @property
    def evaluated(self) -> int:
        return self.evaluated_states

    def __add__(self, other: SearchMetrics) -> SearchMetrics:
        return SearchMetrics(
            self.visited_states + other.visited_states,
            self.expanded_nodes + other.expanded_nodes,
            self.generated_actions + other.generated_actions,
            self.evaluated_states + other.evaluated_states,
            self.pruned_actions + other.pruned_actions,
            max(self.maximum_depth, other.maximum_depth),
            self.thinking_time + other.thinking_time,
        )


class AlphaBetaAgent:
    """Depth-limited alpha-beta with canonical root traversal."""

    def __init__(
        self,
        name: str,
        depth: int,
        evaluation_function: EvaluationFunction,
        ordering: OrderingFunction,
    ) -> None:
        if depth < 1:
            raise ValueError("depth must be at least one ply")
        self.name = name
        self.depth = depth
        self.evaluation_function = evaluation_function
        self.ordering = ordering
        self.last_metrics = SearchMetrics()
        self.total_metrics = SearchMetrics()
        self.last_value = -math.inf
        self._reset_counts()

    def _reset_counts(self) -> None:
        self._visited = 0
        self._expanded = 0
        self._generated = 0
        self._evaluated = 0
        self._pruned = 0
        self._maximum_depth = 0

    def _visit(self, depth: int) -> None:
        self._visited += 1
        self._maximum_depth = max(self._maximum_depth, depth)

    def _evaluate_leaf(
        self, board: JetanBoard, depth: int, perspective: Player
    ) -> float | None:
        if board.is_terminal():
            self._evaluated += 1
            return float(board.utility(perspective))
        if depth == self.depth:
            self._evaluated += 1
            value = float(self.evaluation_function(board, perspective))
            if not math.isfinite(value):
                raise ValueError("evaluation function must return a finite value")
            return value
        return None

    def choose_action(self, board: JetanBoard) -> Move:
        started = time.perf_counter()
        self._reset_counts()
        self._visit(0)
        if board.is_terminal():
            raise RuntimeError("alpha-beta received a terminal board")

        actions = board.actions()
        self._expanded += 1
        self._generated += len(actions)
        perspective = board.player()
        best_action = actions[0]
        best_value = -math.inf
        alpha = -math.inf
        for action in actions:  # Keep canonical root order.
            value = self._value(
                board.result(action), 1, perspective, alpha, math.inf
            )
            if value > best_value:  # Keep the first canonical move on a tie.
                best_action, best_value = action, value
            alpha = max(alpha, best_value)

        self.last_value = best_value
        self.last_metrics = SearchMetrics(
            self._visited,
            self._expanded,
            self._generated,
            self._evaluated,
            self._pruned,
            self._maximum_depth,
            time.perf_counter() - started,
        )
        self.total_metrics += self.last_metrics
        return best_action

    def _value(
        self,
        board: JetanBoard,
        depth: int,
        perspective: Player,
        alpha: float,
        beta: float,
    ) -> float:
        """Return the backed-up value and update exact search metrics.

        TODO: Count this visit. Check terminal status before the depth cutoff.
        On expansion, call ``board.actions()`` exactly once, count the complete
        canonical tuple, and pass that tuple to ``self.ordering``. Recur as MAX
        when ``board.player() is perspective`` and MIN otherwise. Update alpha
        or beta and count every generated sibling skipped by a cutoff in
        ``self._pruned``. Keep ``perspective`` fixed throughout the recursion.
        """
        self._visit(depth)
        utility = self._evaluate_leaf(board, depth, perspective)
        if utility != None:
            return utility
        moves = board.actions()
        self._expanded += 1
        self._generated += len(moves)
        moves = self.ordering(board, moves, perspective)
        if board.player() == perspective:
            #Max
            value = -math.inf
            for i in range(len(moves)):
                child = self._value(board.result(moves[i]), depth + 1, perspective, alpha, beta)
                value = max(value, child)
                alpha = max(alpha, value)
                if alpha >= beta:
                    self._pruned += len(moves[i+1:])
                    break
        else:
            #Min
            value = math.inf
            for i in range(len(moves)):
                child = self._value(board.result(moves[i]), depth + 1, perspective, alpha, beta)
                value = min(value, child)
                beta = min(beta, value)
                if alpha >= beta:
                    self._pruned += len(moves[i+1:])
                    break

        del board, depth, perspective, alpha, beta
        return value
        # raise NotImplementedError("implement alpha-beta recursion")
