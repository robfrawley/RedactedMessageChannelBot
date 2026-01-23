import json
from typing import Any
from zoneinfo import ZoneInfo
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot import ENV_FILE_PATH


class SettingsManager(BaseSettings):
    discord_token: str = Field()
    sqlite_db_path: str = Field()
    debug_mode: bool = Field(default=False)
    bot_time_zone: ZoneInfo = Field(default=ZoneInfo("UTC"))
    redacted_channel_id: int | None = Field(default=None)
    log_channel_id: int | None = Field(default=None)
    log_mention_role_id: int | None = Field(default=None)
    warnings_post_delete_delay_seconds: float = Field(default=30.0)
    redacted_post_delete_delay_seconds: float | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("sqlite_db_path", mode="before")
    @classmethod
    def make_sqlite_db_path_absolute(cls, v: str) -> str:
        if not v:
            raise ValueError("sqlite_db_path cannot be empty")

        return str(Path(v).expanduser().resolve())

    @field_validator("bot_time_zone", mode="before")
    @classmethod
    def normalize_bot_time_zone(cls, v):
        return ZoneInfo(v) if isinstance(v, str) else v

    @field_validator("redacted_post_delete_delay_seconds", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


settings = SettingsManager() # type: ignore
