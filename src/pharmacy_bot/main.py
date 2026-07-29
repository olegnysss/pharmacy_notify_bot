from __future__ import annotations

import asyncio
import logging
import ssl

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from pharmacy_bot.application.navigation import NavigationService
from pharmacy_bot.application.onboarding import OnboardingService
from pharmacy_bot.config import get_settings
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.presentation.navigation_router import (
    configure_private_commands,
)
from pharmacy_bot.presentation.navigation_router import (
    router as navigation_router,
)
from pharmacy_bot.presentation.onboarding_router import router as onboarding_router


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

    dispatcher = Dispatcher()
    dispatcher.include_router(onboarding_router)
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
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await engine.dispose()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
