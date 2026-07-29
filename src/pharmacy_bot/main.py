from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from pharmacy_bot.application.navigation import NavigationService
from pharmacy_bot.application.onboarding import OnboardingService
from pharmacy_bot.application.product_selection import ProductSelectionService
from pharmacy_bot.application.subscription_lifecycle import SubscriptionLifecycleService
from pharmacy_bot.application.subscription_setup import SubscriptionSetupService
from pharmacy_bot.application.subscriptions import SubscriptionQueryService
from pharmacy_bot.application.user_settings import UserSettingsService
from pharmacy_bot.config import get_settings
from pharmacy_bot.domain.user_settings import ServiceLimits
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.manual_check_scheduler import (
    DemoManualCheckScheduler,
    UnavailableManualCheckScheduler,
)
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.infrastructure.product_discovery import (
    ConfiguredProductLinkPolicy,
    DemoProductDiscoveryGateway,
    UnavailableProductDiscoveryGateway,
)
from pharmacy_bot.infrastructure.product_draft_repository import (
    SqlAlchemyProductDraftRepository,
)
from pharmacy_bot.infrastructure.setup_capabilities import (
    ConfiguredSourceCapabilities,
    DefaultLocationResolver,
    DemoLocationResolver,
    demo_sources,
)
from pharmacy_bot.infrastructure.subscription_lifecycle_repository import (
    SourceConfigurationValidator,
    SqlAlchemySubscriptionLifecycleRepository,
)
from pharmacy_bot.infrastructure.subscription_repository import (
    SqlAlchemySubscriptionRepository,
)
from pharmacy_bot.infrastructure.subscription_setup_repository import (
    SqlAlchemySubscriptionSetupRepository,
)
from pharmacy_bot.infrastructure.user_settings_repository import (
    SqlAlchemyUserSettingsRepository,
)
from pharmacy_bot.presentation.lifecycle_router import router as lifecycle_router
from pharmacy_bot.presentation.navigation_router import (
    configure_private_commands,
)
from pharmacy_bot.presentation.navigation_router import (
    router as navigation_router,
)
from pharmacy_bot.presentation.onboarding_router import router as onboarding_router
from pharmacy_bot.presentation.product_selection_router import (
    router as product_selection_router,
)
from pharmacy_bot.presentation.subscription_router import router as subscription_router
from pharmacy_bot.presentation.subscription_setup_router import (
    router as subscription_setup_router,
)
from pharmacy_bot.presentation.user_settings_router import router as user_settings_router


def create_telegram_session(extra_ca_cert_path: str | None) -> AiohttpSession:
    session = AiohttpSession()
    if extra_ca_cert_path is None:
        return session

    ssl_context = session._connector_init["ssl"]
    if not isinstance(ssl_context, ssl.SSLContext):
        raise RuntimeError("aiogram session did not create an SSL context")
    ssl_context.load_verify_locations(cafile=extra_ca_cert_path)
    # Some corporate TLS-inspection roots predate the extension requirements
    # enforced by Python 3.13's VERIFY_X509_STRICT default. Signature, hostname,
    # validity-period and trust-chain verification remain enabled.
    ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return session


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyOnboardingRepository(session_factory)
    onboarding_service = OnboardingService(repository, settings.document_bundle())
    navigation_service = NavigationService(onboarding_service)
    product_discovery = (
        DemoProductDiscoveryGateway()
        if settings.product_discovery_mode == "demo"
        else UnavailableProductDiscoveryGateway()
    )
    product_draft_repository = SqlAlchemyProductDraftRepository(session_factory)
    product_selection_service = ProductSelectionService(
        onboarding_service,
        product_draft_repository,
        product_discovery,
        ConfiguredProductLinkPolicy(settings.product_hosts()),
        query_min_length=settings.product_query_min_length,
        query_max_length=settings.product_query_max_length,
        url_max_length=settings.product_url_max_length,
        page_size=settings.product_results_page_size,
        draft_ttl=timedelta(seconds=settings.product_draft_ttl_seconds),
    )
    demo_mode = settings.product_discovery_mode == "demo"
    location_resolver = DemoLocationResolver() if demo_mode else DefaultLocationResolver()
    source_capabilities = ConfiguredSourceCapabilities(demo_sources() if demo_mode else ())
    service_limits = ServiceLimits(
        min_radius_meters=settings.monitoring_min_radius_meters,
        max_radius_meters=settings.monitoring_max_radius_meters,
        max_sources_per_subscription=settings.max_sources_per_subscription,
        max_active_subscriptions=settings.max_active_subscriptions,
        manual_check_cooldown_seconds=settings.manual_check_cooldown_seconds,
        location_min_length=settings.location_input_min_length,
        location_max_length=settings.location_input_max_length,
        product_query_min_length=settings.product_query_min_length,
        product_query_max_length=settings.product_query_max_length,
    )
    user_settings_repository = SqlAlchemyUserSettingsRepository(session_factory)
    user_settings_service = UserSettingsService(
        onboarding_service,
        user_settings_repository,
        location_resolver,
        source_capabilities,
        service_limits,
        max_points_per_message=settings.max_points_per_notification,
    )
    subscription_setup_service = SubscriptionSetupService(
        onboarding_service,
        product_draft_repository,
        SqlAlchemySubscriptionSetupRepository(session_factory),
        location_resolver,
        source_capabilities,
        draft_ttl=timedelta(seconds=settings.setup_draft_ttl_seconds),
        location_min_length=settings.location_input_min_length,
        location_max_length=settings.location_input_max_length,
        min_radius_meters=settings.monitoring_min_radius_meters,
        max_radius_meters=settings.monitoring_max_radius_meters,
        preferences=user_settings_repository,
        max_active_subscriptions=settings.max_active_subscriptions,
        max_sources_per_subscription=settings.max_sources_per_subscription,
    )
    subscription_query_service = SubscriptionQueryService(
        onboarding_service,
        SqlAlchemySubscriptionRepository(session_factory),
        DemoManualCheckScheduler() if demo_mode else UnavailableManualCheckScheduler(),
        page_size=settings.subscription_results_page_size,
        manual_check_cooldown=timedelta(seconds=settings.manual_check_cooldown_seconds),
    )
    subscription_lifecycle_service = SubscriptionLifecycleService(
        onboarding_service,
        SqlAlchemySubscriptionLifecycleRepository(session_factory),
        location_resolver,
        source_capabilities,
        SourceConfigurationValidator(source_capabilities),
        draft_ttl=timedelta(seconds=settings.setup_draft_ttl_seconds),
        min_radius_meters=settings.monitoring_min_radius_meters,
        max_radius_meters=settings.monitoring_max_radius_meters,
        location_min_length=settings.location_input_min_length,
        location_max_length=settings.location_input_max_length,
        preferences=user_settings_repository,
        max_sources_per_subscription=settings.max_sources_per_subscription,
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(onboarding_router)
    dispatcher.include_router(product_selection_router)
    dispatcher.include_router(subscription_setup_router)
    dispatcher.include_router(lifecycle_router)
    dispatcher.include_router(subscription_router)
    dispatcher.include_router(user_settings_router)
    dispatcher.include_router(navigation_router)
    telegram_session = create_telegram_session(
        str(settings.extra_ca_cert_path) if settings.extra_ca_cert_path else None,
    )
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        session=telegram_session,
    )

    try:
        await configure_private_commands(bot)
        await dispatcher.start_polling(
            bot,
            onboarding_service=onboarding_service,
            navigation_service=navigation_service,
            product_selection_service=product_selection_service,
            subscription_setup_service=subscription_setup_service,
            subscription_query_service=subscription_query_service,
            subscription_lifecycle_service=subscription_lifecycle_service,
            user_settings_service=user_settings_service,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await engine.dispose()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
