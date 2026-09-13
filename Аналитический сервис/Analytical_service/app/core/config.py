from __future__ import annotations

import re
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    service_name: str = "analytical-service"
    internal_token: str | None = Field(default=None, validation_alias="ANALYTICS_INTERNAL_TOKEN")
    postgres_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("ANALYTICS_POSTGRES_HOST", "ENERGY_AGG_POSTGRES_HOST", "POSTGRES_HOST"))
    postgres_port: int = Field(default=15432, validation_alias=AliasChoices("ANALYTICS_POSTGRES_PORT", "ENERGY_AGG_POSTGRES_PORT", "POSTGRES_PORT"))
    postgres_db: str = Field(default="student", validation_alias=AliasChoices("ANALYTICS_POSTGRES_DB", "ENERGY_AGG_POSTGRES_DB"))
    postgres_user: str = Field(default="student", validation_alias=AliasChoices("ANALYTICS_POSTGRES_USER", "ENERGY_AGG_POSTGRES_USER"))
    postgres_password: str = Field(default="st1211@98w", validation_alias=AliasChoices("ANALYTICS_POSTGRES_PASSWORD", "ENERGY_AGG_POSTGRES_PASSWORD"))
    postgres_connect_timeout: int = Field(default=5, validation_alias="ANALYTICS_POSTGRES_CONNECT_TIMEOUT")
    postgres_keepalives: int = Field(default=1, validation_alias="POSTGRES_KEEPALIVES")
    postgres_keepalives_idle: int = Field(default=30, validation_alias="POSTGRES_KEEPALIVES_IDLE")
    postgres_keepalives_interval: int = Field(default=10, validation_alias="POSTGRES_KEEPALIVES_INTERVAL")
    postgres_keepalives_count: int = Field(default=5, validation_alias="POSTGRES_KEEPALIVES_COUNT")
    hourly_table: str = Field(
        default="student_schema.electricity_sensor_readings_hourly",
        validation_alias=AliasChoices("ANALYTICS_HOURLY_TABLE", "ENERGY_HOURLY_TABLE"),
    )
    timezone_name: str = Field(default="Europe/Moscow", validation_alias="ANALYTICS_TIMEZONE")

    @field_validator("hourly_table")
    @classmethod
    def validate_hourly_table(cls, value: str) -> str:
        if not IDENTIFIER_RE.match(value):
            raise ValueError("ANALYTICS_HOURLY_TABLE must be a table name or schema-qualified table name")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
