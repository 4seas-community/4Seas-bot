"""配置：环境变量 → 强类型 Settings。

逗号分隔的 id 列表统一声明为 str 再自行解析 —— pydantic-settings 对 list[int]
会先尝试 JSON 解码，而 `-1001242897290` 这种裸值会直接抛错。
"""

from __future__ import annotations

import datetime as dt
from functools import cached_property
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_ids(raw: str) -> list[int]:
    return [int(p.strip()) for p in raw.split(",") if p.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    telegram_bot_token: str
    telegram_admin_ids: str = ""
    telegram_allowed_chats: str = ""
    daily_report_chat_id: int | None = None

    # 每日播报
    daily_report_time: str = "09:00"
    daily_report_days_ahead: int = Field(default=0, ge=0, le=30)
    daily_report_when_empty: bool = True
    tz: str = "Asia/Bangkok"

    # 数据源与同步
    sola_group: str = "4seas"
    sola_api_base: str = "https://api.sola.day"
    sola_web_base: str = "https://app.sola.day"
    sync_time: str = "08:30"
    sync_horizon_days: int = Field(default=60, ge=1, le=365)
    sync_on_startup: bool = True

    # LLM
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # 限流
    ask_rate_per_hour: int = 10
    keyword_default_cooldown: int = 3600

    db_path: str = "data/bot.sqlite3"
    log_level: str = "INFO"

    @cached_property
    def admin_ids(self) -> list[int]:
        return _parse_ids(self.telegram_admin_ids)

    @cached_property
    def allowed_chats(self) -> list[int]:
        return _parse_ids(self.telegram_allowed_chats)

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def _time(self, raw: str) -> dt.time:
        hh, _, mm = raw.partition(":")
        return dt.time(int(hh), int(mm or 0), tzinfo=self.zone)

    @cached_property
    def report_time(self) -> dt.time:
        return self._time(self.daily_report_time)

    @cached_property
    def sync_at(self) -> dt.time:
        return self._time(self.sync_time)

    @cached_property
    def report_chat_id(self) -> int | None:
        if self.daily_report_chat_id is not None:
            return self.daily_report_chat_id
        return self.allowed_chats[0] if self.allowed_chats else None

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    def is_allowed_chat(self, chat_id: int) -> bool:
        # 未配置白名单时不做限制，方便本地调试
        return not self.allowed_chats or chat_id in self.allowed_chats


settings = Settings()  # type: ignore[call-arg]
