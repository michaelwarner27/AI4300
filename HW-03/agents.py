"""Provided interfaces and student-completed agents for U0-HW-03."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from problem import RecoveryProblem
from recovery import Action, RecoveryState


@dataclass(frozen=True)
class SearchMetrics:
    generated: int = 0
    expanded: int = 0
    peak_frontier: int = 0
    planning_time_seconds: float = 0.0


@dataclass(frozen=True)
class SearchResult:
    plan: tuple[Action, ...] | None
    metrics: SearchMetrics


class SearchAgent(Protocol):
    def plan(self, problem: RecoveryProblem) -> SearchResult:
        """Return a complete plan and search metrics."""
        actionList = []
        state = problem.initial_state

        while not problem.is_goal(state):
            steps = problem.actions(state)
            costs = []

            for step in steps:
                next_state = problem.result(state, step)
                costs.append(problem.step_cost(state, step, next_state))

            low = min(costs)
            actionList.append(steps.index(low))
        return SearchResult(tuple(actionList), SearchMetrics())


        

class RandomRecoveryAgent:
    """Provided online baseline that chooses uniformly among legal actions."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def act(
        self, state: RecoveryState, legal_actions: tuple[Action, ...]
    ) -> Action:
        del state
        if not legal_actions:
            raise RuntimeError("RandomRecoveryAgent received no legal actions")
        return self._rng.choice(legal_actions)


class UniformCostSearchAgent:
    def plan(self, problem: RecoveryProblem) -> SearchResult:
        # TODO: Implement uniform-cost graph search and metric accounting.
        actionList = []
        state = problem.initial_state
        max = 24
        while not problem.is_goal(state) or max < 25:
            max +=1
            steps = problem.actions(state)
            costs = []
            bestAction = None
            bestCost = -1
            for step in steps:
                print(step)
                next_state = problem.result(state, step)
                cost = problem.step_cost(state, step, next_state)
                if cost < bestCost or bestCost < 0:
                    bestAction = step
                    bestCost = cost
            actionList.append(bestAction)
            state = next_state
        print("final list", actionList)

        return SearchResult(tuple(actionList), SearchMetrics()) 
        # raise NotImplementedError("Implement UniformCostSearchAgent.plan")


class AStarSearchAgent:
    def heuristic(self, state: RecoveryState) -> int:
        # TODO: Return a nonnegative, nontrivial admissible estimate.
        raise NotImplementedError("Implement AStarSearchAgent.heuristic")

    def plan(self, problem: RecoveryProblem) -> SearchResult:
        # TODO: Implement A* graph search and metric accounting.
        raise NotImplementedError("Implement AStarSearchAgent.plan")
