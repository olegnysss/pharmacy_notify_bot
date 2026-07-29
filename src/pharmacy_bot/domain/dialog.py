from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DialogScenario(StrEnum):
    PRODUCT_SELECTION = "product_selection"
    SUBSCRIPTION_SETUP = "subscription_setup"
    SUBSCRIPTION_EDIT = "subscription_edit"
    USER_SETTINGS = "user_settings"


class RecoveryState(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class DialogRecovery:
    state: RecoveryState
    scenario: DialogScenario | None = None
    subscription_id: int | None = None


class UpdateClaim(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
