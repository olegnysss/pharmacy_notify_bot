from __future__ import annotations

import pytest
from pydantic import ValidationError

from pharmacy_bot.config import Settings


def settings_data() -> dict[str, str]:
    return {
        "bot_token": "42:secret",
        "database_url": "postgresql+asyncpg://user:pass@db:5432/pharmacy",
        "terms_version": "terms-v1",
        "terms_url": "https://example.com/terms",
        "privacy_version": "privacy-v1",
        "privacy_url": "https://example.com/privacy",
    }


def test_document_bundle_comes_from_validated_settings() -> None:
    settings = Settings(**settings_data())

    bundle = settings.document_bundle()

    assert bundle.terms_version == "terms-v1"
    assert bundle.terms_url == "https://example.com/terms"


@pytest.mark.parametrize("field", ["terms_url", "privacy_url"])
def test_document_urls_must_use_https(field: str) -> None:
    data = settings_data()
    data[field] = "http://example.com/document"

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(**data)


def test_database_url_must_use_async_postgresql_driver() -> None:
    data = settings_data()
    data["database_url"] = "sqlite:///local.db"

    with pytest.raises(ValidationError):
        Settings(**data)
