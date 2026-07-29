from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.dialog import (
    DialogRecovery,
    DialogScenario,
    RecoveryState,
    UpdateClaim,
)
from pharmacy_bot.domain.product_selection import ProductDraftStatus
from pharmacy_bot.domain.subscription_lifecycle import EditStatus
from pharmacy_bot.domain.subscription_setup import SetupStatus
from pharmacy_bot.domain.user_settings import SettingsStatus
from pharmacy_bot.infrastructure.models import (
    ProductSelectionDraftModel,
    SubscriptionEditDraftModel,
    SubscriptionSetupDraftModel,
    TelegramUpdateReceiptModel,
    UserPreferencesModel,
)


class SqlAlchemyDialogRecoveryRepository:
    _PRODUCT_ACTIVE: ClassVar[set[str]] = {
        ProductDraftStatus.CHOOSE_METHOD.value,
        ProductDraftStatus.AWAITING_INPUT.value,
        ProductDraftStatus.RESULTS.value,
        ProductDraftStatus.NO_RESULTS.value,
        ProductDraftStatus.CONFIRMATION.value,
        ProductDraftStatus.ERROR.value,
        ProductDraftStatus.SEARCHING.value,
    }
    _SETUP_TERMINAL: ClassVar[set[str]] = {
        SetupStatus.CREATED.value,
        SetupStatus.CANCELLED.value,
    }
    _EDIT_TERMINAL: ClassVar[set[str]] = {
        EditStatus.APPLIED.value,
        EditStatus.CANCELLED.value,
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def inspect_and_cleanup(
        self,
        user_id: int,
        *,
        now: datetime,
        schema_version: int,
    ) -> DialogRecovery:
        async with self._session_factory.begin() as session:
            reset = False
            edit = cast(
                SubscriptionEditDraftModel | None,
                await session.scalar(
                    select(SubscriptionEditDraftModel)
                    .where(SubscriptionEditDraftModel.user_id == user_id)
                    .with_for_update()
                ),
            )
            if edit and edit.status not in self._EDIT_TERMINAL:
                if edit.expires_at <= now or edit.schema_version != schema_version:
                    edit.status = EditStatus.CANCELLED.value
                    edit.generation += 1
                    reset = True
                else:
                    return DialogRecovery(
                        RecoveryState.ACTIVE,
                        DialogScenario.SUBSCRIPTION_EDIT,
                        edit.subscription_id,
                    )

            setup = cast(
                SubscriptionSetupDraftModel | None,
                await session.scalar(
                    select(SubscriptionSetupDraftModel)
                    .where(SubscriptionSetupDraftModel.user_id == user_id)
                    .with_for_update()
                ),
            )
            if setup and setup.status not in self._SETUP_TERMINAL:
                if setup.expires_at <= now or setup.schema_version != schema_version:
                    setup.status = SetupStatus.CANCELLED.value
                    setup.generation += 1
                    reset = True
                else:
                    return DialogRecovery(
                        RecoveryState.ACTIVE,
                        DialogScenario.SUBSCRIPTION_SETUP,
                    )

            product = cast(
                ProductSelectionDraftModel | None,
                await session.scalar(
                    select(ProductSelectionDraftModel)
                    .where(ProductSelectionDraftModel.user_id == user_id)
                    .with_for_update()
                ),
            )
            if product:
                product_active = product.status in self._PRODUCT_ACTIVE
                product_confirmed = product.status == ProductDraftStatus.CONFIRMED.value
                if product_active or product_confirmed:
                    if (
                        product.expires_at <= now
                        or product.schema_version != schema_version
                        or product.status == ProductDraftStatus.SEARCHING.value
                    ):
                        product.status = ProductDraftStatus.CANCELLED.value
                        product.generation += 1
                        reset = True
                    else:
                        return DialogRecovery(
                            RecoveryState.ACTIVE,
                            (
                                DialogScenario.SUBSCRIPTION_SETUP
                                if product_confirmed
                                else DialogScenario.PRODUCT_SELECTION
                            ),
                        )

            preferences = cast(
                UserPreferencesModel | None,
                await session.scalar(
                    select(UserPreferencesModel)
                    .where(UserPreferencesModel.user_id == user_id)
                    .with_for_update()
                ),
            )
            if preferences and preferences.editor_status != SettingsStatus.IDLE.value:
                if (
                    preferences.editor_expires_at is None
                    or preferences.editor_expires_at <= now
                    or preferences.editor_schema_version != schema_version
                ):
                    preferences.editor_status = SettingsStatus.IDLE.value
                    preferences.editor_location_mode = None
                    preferences.editor_location_candidates = []
                    preferences.editor_expires_at = None
                    preferences.generation += 1
                    reset = True
                else:
                    return DialogRecovery(
                        RecoveryState.ACTIVE,
                        DialogScenario.USER_SETTINGS,
                    )
            return DialogRecovery(RecoveryState.RESET if reset else RecoveryState.NONE)


class SqlAlchemyUpdateReceiptRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim(
        self,
        update_id: int,
        *,
        now: datetime,
        lease: timedelta,
    ) -> UpdateClaim:
        lease_until = now + lease
        async with self._session_factory.begin() as session:
            inserted = await session.scalar(
                insert(TelegramUpdateReceiptModel)
                .values(
                    update_id=update_id,
                    status="processing",
                    attempts=1,
                    lease_until=lease_until,
                )
                .on_conflict_do_nothing(index_elements=[TelegramUpdateReceiptModel.update_id])
                .returning(TelegramUpdateReceiptModel.update_id)
            )
            if inserted is not None:
                return UpdateClaim.CLAIMED
            receipt = cast(
                TelegramUpdateReceiptModel | None,
                await session.scalar(
                    select(TelegramUpdateReceiptModel)
                    .where(TelegramUpdateReceiptModel.update_id == update_id)
                    .with_for_update()
                ),
            )
            if receipt is None:
                return UpdateClaim.IN_PROGRESS
            if receipt.status == "completed":
                return UpdateClaim.COMPLETED
            if receipt.status == "processing" and receipt.lease_until > now:
                return UpdateClaim.IN_PROGRESS
            receipt.status = "processing"
            receipt.attempts += 1
            receipt.lease_until = lease_until
            receipt.last_error_id = None
            return UpdateClaim.CLAIMED

    async def complete(self, update_id: int, *, now: datetime) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(TelegramUpdateReceiptModel)
                .where(TelegramUpdateReceiptModel.update_id == update_id)
                .values(status="completed", completed_at=now, lease_until=now)
            )

    async def fail(
        self,
        update_id: int,
        *,
        now: datetime,
        correlation_id: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(TelegramUpdateReceiptModel)
                .where(TelegramUpdateReceiptModel.update_id == update_id)
                .values(
                    status="failed",
                    lease_until=now,
                    last_error_id=correlation_id,
                )
            )
