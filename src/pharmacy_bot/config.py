from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, FilePath, HttpUrl, SecretStr, field_validator, model_validator
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
    product_query_min_length: int = Field(default=2, ge=1, le=32)
    product_query_max_length: int = Field(default=160, ge=16, le=512)
    product_url_max_length: int = Field(default=2048, ge=128, le=4096)
    product_results_page_size: int = Field(default=5, ge=1, le=8)
    product_draft_ttl_seconds: int = Field(default=3600, ge=60, le=604800)
    product_discovery_mode: Literal["unavailable", "demo"] = "unavailable"
    supported_product_hosts: str = ""
    setup_draft_ttl_seconds: int = Field(default=7200, ge=300, le=604800)
    location_input_min_length: int = Field(default=2, ge=1, le=32)
    location_input_max_length: int = Field(default=256, ge=16, le=512)
    monitoring_min_radius_meters: int = Field(default=1000, ge=100, le=100000)
    monitoring_max_radius_meters: int = Field(default=25000, ge=1000, le=500000)

    @field_validator("terms_url", "privacy_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("document URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def validate_product_query_limits(self) -> Settings:
        if self.product_query_min_length > self.product_query_max_length:
            raise ValueError("product query minimum cannot exceed maximum")
        if self.location_input_min_length > self.location_input_max_length:
            raise ValueError("location input minimum cannot exceed maximum")
        if self.monitoring_min_radius_meters > self.monitoring_max_radius_meters:
            raise ValueError("monitoring minimum radius cannot exceed maximum")
        return self

    def document_bundle(self) -> DocumentBundle:
        return DocumentBundle(
            terms_version=self.terms_version,
            terms_url=str(self.terms_url),
            privacy_version=self.privacy_version,
            privacy_url=str(self.privacy_url),
        )

    def product_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip().lower().rstrip(".")
            for host in self.supported_product_hosts.split(",")
            if host.strip()
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
