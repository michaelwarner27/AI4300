"""Student-completed schedule evaluator for U0-HW-04."""

from __future__ import annotations

from dataclasses import dataclass

from conference import ConferenceProblem, Schedule


@dataclass(frozen=True)
class Evaluation:
    hard_violations: int
    soft_penalty: int
    conflicted_talks: frozenset[str]
    details: tuple[str, ...]


def evaluate(problem: ConferenceProblem, schedule: Schedule) -> Evaluation:
    """Count hard violations and the precisely defined soft penalty.

    Hard violations are counted once per violated room-collision constraint,
    speaker-overlap constraint, and supplied audience-conflict constraint.
    Soft penalty is one per missed nonempty preferred-slot set plus, for each
    track, max(0, number of rooms used by that track - 1).
    """
    hard = 0
    soft = 0
    conflicted_talks: set[str] = set()
    details: list[str] = []
    '''
    This mostly working Michael written code was cleaned up by AI into what you see below. I just needed the if statement separated and my variables cleaned up.
    hard = 0
    soft = 0
    conflicted_talks = set()
    details = []
    for i in range(len(schedule.assignments)):
        talk1 = schedule.assignments[i]
        for j in range(i+1,len(schedule.assignments)):
            talk2 = schedule.assignments[j]
            if talk1[1] == talk2[1] or (problem.talk(talk1[0]).speaker == problem.talk(talk2[0]).speaker and talk1[1].slot == talk2[1].slot) or (frozenset({talk1[0],talk2[0]}) in problem.audience_conflicts and talk1[1].slot == talk2[1].slot):
                # print(talk1[0], talk2[0])
                hard +=1
                conflicted_talks.add(talk1[0])
                conflicted_talks.add(talk2[0])
    '''
    assignments = schedule.assignments  # tuple[(talk_id, Placement), ...]

    # --- Hard constraints: inspect every distinct talk pair once. ---
    # A single pair can violate two distinct constraints and both count.
    for i in range(len(assignments)):
        talk1, placement1 = assignments[i]
        for j in range(i + 1, len(assignments)):
            talk2, placement2 = assignments[j]
            talk_a = problem.talk(talk1)
            talk_b = problem.talk(talk2)

            violations: list[str] = []
            if placement1 == placement2:
                violations.append(
                    f"room collision: {talk1} and {talk2} in "
                    f"{placement1.room} @ {placement1.slot}"
                )
            if talk_a.speaker == talk_b.speaker and placement1.slot == placement2.slot:
                violations.append(
                    f"speaker overlap: {talk1} and {talk2} ({talk_a.speaker}) "
                    f"at {placement1.slot}"
                )
            if (
                frozenset({talk1, talk2}) in problem.audience_conflicts
                and placement1.slot == placement2.slot
            ):
                violations.append(
                    f"audience overlap: {talk1} and {talk2} at {placement1.slot}"
                )

            for violation in violations:
                hard += 1
                conflicted_talks.update((talk1, talk2))
                details.append(violation)

            # --- Soft penalty ---
    # One point for each talk outside its nonempty preferred-slot set.
    for talk_id, placement in assignments:
        talk = problem.talk(talk_id)
        if talk.preferred_slots and placement.slot not in talk.preferred_slots:
            soft += 1

    # For each track: max(0, number of distinct rooms used by that track - 1).
    rooms_by_track: dict[str, set[str]] = {}
    for talk_id, placement in assignments:
        track = problem.talk(talk_id).track
        rooms_by_track.setdefault(track, set()).add(placement.room)
    soft += sum(max(0, len(rooms) - 1) for rooms in rooms_by_track.values())

    return Evaluation(hard, soft, frozenset(conflicted_talks), tuple(details))


def is_feasible(problem: ConferenceProblem, schedule: Schedule) -> bool:
    return evaluate(problem, schedule).hard_violations == 0