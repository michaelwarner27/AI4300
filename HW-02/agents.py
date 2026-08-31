"""Agent implementations for U0-HW-02."""

from __future__ import annotations

import random
from typing import Protocol

from hallway import Action, Percept


class Agent(Protocol):
    def reset(self) -> None:
        """Clear episode-specific state."""

    def act(self, percept: Percept) -> Action:
        """Choose one action from the current percept."""
        return Action.WAIT


class RandomAgent:
    """Provided baseline that deliberately ignores every percept."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def reset(self) -> None:
        pass

    def act(self, percept: Percept) -> Action:
        del percept
        return self._rng.choice(list(Action))


class SimpleReflexAgent:
    """A condition-action agent with no memory between calls to act."""

    def reset(self) -> None:
        pass

    def act(self, percept: Percept) -> Action:
        if percept.package_here == True and percept.carrying == False:
            return Action.PICK_UP
        elif percept.destination_here == True and percept.carrying == True:
            return Action.DROP_OFF
        elif percept.location == "A":
            return Action.MOVE_RIGHT
        elif percept.location == "E":
            return Action.MOVE_LEFT
        return Action.MOVE_RIGHT


class ModelBasedReflexAgent:
    """A reflex agent that first updates an internal hallway model."""

    def __init__(self) -> None:
        self.destination = ""
        self.direction_left = False
        self.reset()

    def reset(self) -> None:
        # TODO: Initialize all episode-specific internal state.
        pass

    def act(self, percept: Percept) -> Action:
        # TODO: Update the model from the percept, then apply condition-action rules.
        if percept.destination_here == True:
            self.destination = percept.location

        if percept.package_here == True:
            if self.destination == "":
                pass
            elif percept.location < self.destination:
                self.direction_left = False

            elif percept.location > self.destination:
                self.direction_left = True


        if percept.package_here == True and percept.carrying == False:
            return Action.PICK_UP
        if percept.destination_here == True and percept.carrying == True:
            return Action.DROP_OFF
        
        if percept.location == "A":
            self.direction_left = False
        elif percept.location == "E":
            self.direction_left = True

        if self.direction_left == True:
            return Action.MOVE_LEFT
        elif self.direction_left == False:
            return Action.MOVE_RIGHT
        return Action.WAIT
