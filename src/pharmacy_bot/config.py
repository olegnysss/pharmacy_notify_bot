from __future__ import annotations

from functools import lru_cache

from pydantic import Field, FilePath, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pharmacy_bot.application.onboarding import DocumentBundle


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr
    database_url: str = Field(
        pattern=r"^postgresql\+asyncpg://",
    )
    terms_version: str = Field(min_length=1, max_length=64)
    terms_url: HttpUrl
    privacy_version: str = Field(min_length=1, max_length=64)
    privacy_url: HttpUrl
    log_level: str = "INFO"
    extra_ca_cert_path: FilePath | None = None

    @field_validator("terms_url", "privacy_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("document URL must use HTTPS")
        return value

    def document_bundle(self) -> DocumentBundle:
        return DocumentBundle(
            terms_version=self.terms_version,
            terms_url=str(self.terms_url),
            privacy_version=self.privacy_version,
            privacy_url=str(self.privacy_url),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(pattern=r"^postgresql\+asyncpg://")
