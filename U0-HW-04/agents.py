"""Repair-agent interfaces and student implementation points."""

from __future__ import annotations

import random, time
from dataclasses import dataclass

from conference import ConferenceProblem, Placement, Schedule, move
from constraints import evaluate
from llm_client import ChatClient


@dataclass(frozen=True)
class RepairMetrics:
    edits: int = 0
    evaluator_calls: int = 0
    model_calls: int = 0
    malformed_requests: int = 0
    invalid_requests: int = 0
    repeated_requests: int = 0
    client_failures: int = 0
    runtime_seconds: float = 0.0


@dataclass(frozen=True)
class TraceStep:
    step: int
    request: str
    outcome: str
    hard_violations: int
    soft_penalty: int


@dataclass(frozen=True)
class RepairResult:
    schedule: Schedule
    feasible: bool
    metrics: RepairMetrics
    trace: tuple[TraceStep, ...]


class RandomMoveAgent:
    """Provided seeded baseline using the same one-talk move and budget."""

    def repair(
        self, problem: ConferenceProblem, initial: Schedule, budget: int, seed: int
    ) -> RepairResult:
        import time

        started = time.perf_counter()
        rng = random.Random(seed)
        schedule = initial
        calls = 1
        trace: list[TraceStep] = []
        current = evaluate(problem, schedule)
        for step in range(1, budget + 1):
            if current.hard_violations == 0:
                break
            talk_id = rng.choice(tuple(t.identifier for t in problem.talks))
            options = [p for p in problem.domain(talk_id) if p != schedule.placement(talk_id)]
            if not options:
                continue
            placement = rng.choice(options)
            schedule = move(problem, schedule, talk_id, placement)
            current = evaluate(problem, schedule)
            calls += 1
            trace.append(TraceStep(step, f"{talk_id}->{placement}", "applied", current.hard_violations, current.soft_penalty))
        return RepairResult(
            schedule,
            current.hard_violations == 0,
            RepairMetrics(
                edits=len(trace),
                evaluator_calls=calls,
                runtime_seconds=time.perf_counter() - started,
            ),
            tuple(trace),
        )


class MinConflictsAgent:
    def repair(
        self, problem: ConferenceProblem, initial: Schedule, budget: int, seed: int
    ) -> RepairResult:
        """Repair with conflicted-variable selection and lexicographic scoring."""
        started = time.perf_counter()
        rng = random.Random(seed)   
        schedule = initial
        calls = 1
        trace: list[TraceStep] = []
        current = evaluate(problem, schedule)
        for step in range(1, budget+1):
            current = evaluate(problem, schedule)
            calls += 1
            conflicted_talks = tuple(current.conflicted_talks)
            if current.hard_violations == 0:
                break #This schedule works
            
            talk_id = rng.choice(tuple(conflicted_talks))
            options = [p for p in problem.domain(talk_id) if p != schedule.placement(talk_id)]
            if not options:
                continue #can't move selected talk
            minCost = (current.hard_violations, current.soft_penalty)
            minOption = options[0]
            minEvaluation = current
            for o in options:
                tempSchedule = move(problem, schedule, talk_id, o)
                e = evaluate(problem, tempSchedule)
                calls += 1
                cost = (e.hard_violations, e.soft_penalty)
                if cost < minCost:
                    minCost = cost
                    minOption = o
                    minEvaluation = e
            placement = minOption #mostly this part deviates from random
            schedule = move(problem, schedule, talk_id, placement)
            current = minEvaluation#evaluate(problem, schedule)
            
            trace.append(TraceStep(step, f"{talk_id}->{placement}", "applied", current.hard_violations, current.soft_penalty))
                

        return RepairResult(
            schedule,
            current.hard_violations == 0,
            RepairMetrics(
                edits=len(trace),
                evaluator_calls=calls,
                runtime_seconds=time.perf_counter() - started,
            ),
            tuple(trace),
        )
        # raise NotImplementedError("Implement MinConflictsAgent.repair")


class LLMRepairAgent:
    def __init__(self, client: ChatClient, history_limit: int = 6) -> None:
        if history_limit < 0:
            raise ValueError("history_limit must be nonnegative")
        self.client = client
        self.history_limit = history_limit

    def repair(
        self, problem: ConferenceProblem, initial: Schedule, budget: int, seed: int
    ) -> RepairResult:
        """Request exactly one JSON move per model call and validate it in code."""
        # TODO: Implement the tool-using repair loop. history_limit=0 means no
        # prior outcomes are sent. Reject duplicate JSON object keys. Count a
        # call before invoking the client; catch Exception (not BaseException),
        # record only "client failure: <ExceptionType>", and continue safely.
        # The required request is:
        # {"tool":"move","talk_id":"T1","room":"Canyon","slot":"10:30"}
        raise NotImplementedError("Implement LLMRepairAgent.repair")
