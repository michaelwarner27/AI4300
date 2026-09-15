"""Student-completed search problem formulation for U0-HW-03."""

from __future__ import annotations

# from recovery import Action, RecoveryState
from recovery import (
    Action,
    BackupStatus,
    DatabaseStatus,
    RecoveryState,
    ServiceStatus,
    StorageStatus,
    TrafficMode,
    ValidationStatus,
    ACTION_COSTS,
    ACTION_ORDER,
)

class RecoveryProblem:
    def __init__(self, initial_state: RecoveryState) -> None:
        self.initial_state = initial_state

    def _services_healthy(self, state: RecoveryState) -> bool:
        return (
            state.storage is StorageStatus.HEALTHY
            and state.database is DatabaseStatus.HEALTHY
            and state.auth is ServiceStatus.HEALTHY
            and state.api is ServiceStatus.HEALTHY
        )

    def actions(self, state: RecoveryState) -> tuple[Action, ...]:
        """Return all legal actions in the supplied Action enumeration order."""
        """Michael written code before AI switched string comparisons to Action enums.
        
        legalActions  = []
        if state.traffic == "normal" or state.traffic == "canary":
            legalActions.append(Action.ISOLATE_TRAFFIC)
        if state.traffic == "isolated" and state.storage == "down":
            legalActions.append(Action.RESTART_STORAGE)
        if state.traffic == "isolated" and state.storage == "healthy":
            legalActions.append(Action.RESTART_DATABASE)
            if state.database == "corrupt" and state.backup == "available":
                legalActions.append(Action.RESTORE_DATABASE)
        if state.traffic == "isolated" and state.database == "healthy":
            if state.auth != "healthy":
                legalActions.append(Action.RESTART_AUTH)
            if state.auth == "healthy" and state.api != "healthy":
                legalActions.append(Action.ROLLBACK_AUTH)
            if state.auth == "healthy" and state.api == "faulty":
                legalActions.append(Action.ROLLBACK_API)
        #All 4 are healthy
        if state.traffic == "isolated" and state.database == "healthy" and state.storage == "healthy" and state.auth == "healthy" and state.api == "healthy":
            if state.validation == "not_run":
                legalActions.append(Action.VALIDATE_STACK)
            if state.validation == "passed":
                legalActions.append(Action.ENABLE_CANARY)
            if state.traffic == "canary":
                legalActions.append(Action.ENABLE_NORMAL)
        return tuple(legalActions)
        """
        actions: list[Action] = []
        isolated = state.traffic is TrafficMode.ISOLATED

        if not isolated:
            actions.append(Action.ISOLATE_TRAFFIC)
        if isolated and state.storage is StorageStatus.DOWN:
            actions.append(Action.RESTART_STORAGE)
        if (
            isolated
            and state.storage is StorageStatus.HEALTHY
            and state.database is DatabaseStatus.DOWN
        ):
            actions.append(Action.RESTART_DATABASE)
        if (
            isolated
            and state.storage is StorageStatus.HEALTHY
            and state.database is DatabaseStatus.CORRUPT
            and state.backup is BackupStatus.AVAILABLE
        ):
            actions.append(Action.RESTORE_DATABASE)
        if (
            isolated
            and state.database is DatabaseStatus.HEALTHY
            and state.auth is not ServiceStatus.HEALTHY
        ):
            actions.append(Action.RESTART_AUTH)
        if (
            isolated
            and state.database is DatabaseStatus.HEALTHY
            and state.auth is ServiceStatus.FAULTY
        ):
            actions.append(Action.ROLLBACK_AUTH)
        if (
            isolated
            and state.database is DatabaseStatus.HEALTHY
            and state.auth is ServiceStatus.HEALTHY
            and state.api is not ServiceStatus.HEALTHY
        ):
            actions.append(Action.RESTART_API)
        if (
            isolated
            and state.database is DatabaseStatus.HEALTHY
            and state.auth is ServiceStatus.HEALTHY
            and state.api is ServiceStatus.FAULTY
        ):
            actions.append(Action.ROLLBACK_API)
        if (
            isolated
            and self._services_healthy(state)
            and state.validation is ValidationStatus.NOT_RUN
        ):
            actions.append(Action.VALIDATE_STACK)
        if (
            isolated
            and self._services_healthy(state)
            and state.validation is ValidationStatus.PASSED
        ):
            actions.append(Action.ENABLE_CANARY)
        if (
            state.traffic is TrafficMode.CANARY
            and self._services_healthy(state)
            and state.validation is ValidationStatus.PASSED
        ):
            actions.append(Action.ENABLE_NORMAL)

        return tuple(action for action in ACTION_ORDER if action in actions)

    def result(self, state: RecoveryState, action: Action) -> RecoveryState:
        """Return the new immutable state, or raise ValueError if action is illegal."""
        if action not in self.actions(state):
            raise ValueError("illegal action")
#AI Generated If statements to avoid the tediousness of it. Base on first statement I made myself
        # if action == Action.ISOLATE_TRAFFIC:
        #     newState = RecoveryState(state.storage, state.database, state.auth, state.api, TrafficMode.ISOLATED, state.backup, state.validation)
        not_run = ValidationStatus.NOT_RUN
        if action == Action.ISOLATE_TRAFFIC:
            return RecoveryState(
                state.storage, state.database, state.auth, state.api,
                TrafficMode.ISOLATED, state.backup, state.validation
            )
        if action == Action.RESTART_STORAGE:
            return RecoveryState(
                StorageStatus.HEALTHY, state.database, state.auth, state.api,
                state.traffic, state.backup, not_run
            )
        if action == Action.RESTART_DATABASE:
            return RecoveryState(
                state.storage, DatabaseStatus.HEALTHY, state.auth, state.api,
                state.traffic, state.backup, not_run
            )
        if action == Action.RESTORE_DATABASE:
            return RecoveryState(
                state.storage, DatabaseStatus.HEALTHY, state.auth, state.api,
                state.traffic, BackupStatus.UNAVAILABLE, not_run
            )
        if action == Action.RESTART_AUTH:
            next_auth = (
                ServiceStatus.DOWN
                if state.auth == ServiceStatus.FAULTY
                else ServiceStatus.HEALTHY
            )
            return RecoveryState(
                state.storage, state.database, next_auth, state.api,
                state.traffic, state.backup, not_run
            )
        if action == Action.ROLLBACK_AUTH:
            return RecoveryState(
                state.storage, state.database, ServiceStatus.HEALTHY, state.api,
                state.traffic, state.backup, not_run
            )
        if action == Action.RESTART_API:
            next_api = (
                ServiceStatus.DOWN
                if state.api == ServiceStatus.FAULTY
                else ServiceStatus.HEALTHY
            )
            return RecoveryState(
                state.storage, state.database, state.auth, next_api,
                state.traffic, state.backup, not_run
            )
        if action == Action.ROLLBACK_API:
            return RecoveryState(
                state.storage, state.database, state.auth, ServiceStatus.HEALTHY,
                state.traffic, state.backup, not_run
            )
        if action == Action.VALIDATE_STACK:
            return RecoveryState(
                state.storage, state.database, state.auth, state.api,
                state.traffic, state.backup, ValidationStatus.PASSED
            )
        if action == Action.ENABLE_CANARY:
            return RecoveryState(
                state.storage, state.database, state.auth, state.api,
                TrafficMode.CANARY, state.backup, state.validation
            )
        if action == Action.ENABLE_NORMAL:
            return RecoveryState(
                state.storage, state.database, state.auth, state.api,
                TrafficMode.NORMAL, state.backup, state.validation
            )

        return state

    def is_goal(self, state: RecoveryState) -> bool:
        """Return whether state satisfies the complete recovery goal."""
        if state.storage == "healthy" and state.database == "healthy" and state.auth == "healthy" and state.api == "healthy" and state.traffic == "normal" and state.validation == "passed":
            return True
        return False
        # raise NotImplementedError("Implement RecoveryProblem.is_goal")

    def step_cost(self, state: RecoveryState, action: Action, next_state: RecoveryState) -> int:
        """Return action cost, or raise ValueError if the transition is illegal."""
        if action not in self.actions(state) or self.result(state, action) != next_state:
            raise ValueError("Invalid action at step_cost")
        return ACTION_COSTS[action]
        # raise NotImplementedError("Implement RecoveryProblem.step_cost")
