"""Stable action-ordering wrappers and student ranking-key stubs."""

from collections.abc import Callable
from typing import Any

from jetan import JetanBoard, Move, Player
from student_strategies import PIECE_VALUES

OrderingFunction = Callable[
    [JetanBoard, tuple[Move, ...], Player], tuple[Move, ...]
]


def ordering_key_1(
    board: JetanBoard, action: Move, perspective: Player
) -> Any:
    """Return the first ranking key; larger keys are searched first at MAX."""
    # TODO: Design the first general action-ordering key.
    victim = board.at(action.destination)
    if not victim:
        return 0.0
    pieceValue = PIECE_VALUES[victim.kind]
    if board.player() is perspective:
        return pieceValue
    
    del board, action, perspective
    return -pieceValue


def ordering_key_2(
    board: JetanBoard, action: Move, perspective: Player
) -> Any:
    """Return a distinct ranking key; larger keys are searched first at MAX."""
    # TODO: Design the second general action-ordering key.
    child = board.result(action)
    mine = child.attacked_locations(perspective)
    theirs = child.attacked_locations(perspective.opponent)
    return float(len(mine) - len(theirs))


def _stable_order(
    board: JetanBoard,
    actions: tuple[Move, ...],
    perspective: Player,
    key: Callable[[JetanBoard, Move, Player], Any],
) -> tuple[Move, ...]:
    """Rank the supplied canonical tuple while retaining input-order ties."""
    return tuple(
        sorted(
            actions,
            key=lambda action: key(board, action, perspective),
            reverse=board.player() is perspective,
        )
    )


def order_actions_1(
    board: JetanBoard, actions: tuple[Move, ...], perspective: Player
) -> tuple[Move, ...]:
    """Apply the first student key to the supplied canonical action tuple."""
    return _stable_order(board, actions, perspective, ordering_key_1)


def order_actions_2(
    board: JetanBoard, actions: tuple[Move, ...], perspective: Player
) -> tuple[Move, ...]:
    """Apply the second student key to the supplied canonical action tuple."""
    return _stable_order(board, actions, perspective, ordering_key_2)
