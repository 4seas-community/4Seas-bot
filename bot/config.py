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
    # 已加入但暂时不希望它开口的群：收消息、不回任何东西。
    # 测试期间把正式群放进来，比从白名单里删掉安全（删掉会触发退群逻辑）。
    telegram_muted_chats: str = ""
    # 非白名单群是否直接退群。默认 false —— 退群不可逆，白名单打错一个字符
    # 就会丢掉正式群的管理员身份。
    leave_unknown_chats: bool = False
    # 启动时丢弃积压的历史消息。生产环境建议 true（重启后不会把停机期间的
    # 消息一次性重放出去）；调试时设 false 才能接住刚发的测试消息。
    drop_pending_updates: bool = True
    daily_report_chat_id: int | None = None

    # 每日播报
    daily_report_time: str = "19:00"
    # 0 = 播当天，1 = 播明天。晚上预告次日活动用 1。
    daily_report_offset_days: int = Field(default=1, ge=0, le=7)
    # 从起始日再往后多算几天。0 = 只播起始日那一天。
    daily_report_days_ahead: int = Field(default=0, ge=0, le=30)
    daily_report_when_empty: bool = True
    tz: str = "Asia/Bangkok"

    # 播报排版:compact = 一行一场(默认);detailed = 完整卡片(地址/主办/人数/标签)
    digest_style: str = "editorial"

    # /events 手动查询默认看未来几天（从今天算起）
    events_command_days: int = Field(default=7, ge=0, le=30)

    # 问答用什么语言回复。静态文案（/help、欢迎语、播报）已经全部是英文，
    # 这个只管 LLM 生成的部分 —— 不设死的话模型会跟着提问者的语言走。
    reply_language: str = "English"

    # 数据源与同步
    sola_group: str = "4seas"
    sola_api_base: str = "https://api.sola.day"
    sola_web_base: str = "https://app.sola.day"
    # 逗号分隔，可以配多个时间点。播报前那次是必须的，其余是为了让白天新加的活动
    # 也能及时进库。
    sync_times: str = "08:30,18:30"
    sync_horizon_days: int = Field(default=60, ge=1, le=365)
    # venue 和 content 只有详情接口有,列表接口没有。补齐要一场一个请求,
    # 所以只补近期的 —— 播报和 /events 用不到 60 天以后那些。
    detail_enrich_days: int = Field(default=10, ge=0, le=60)
    detail_concurrency: int = Field(default=6, ge=1, le=20)
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

    # 自定义命令的配置目录。放 *.yaml,群里发 /reload 即可生效,不用重启。
    commands_dir: str = "data/commands"

    # 管理页。默认只绑 127.0.0.1 —— 这个页面能改 bot 在 776 人群里说什么，
    # 不该直接暴露到公网。远程访问用 SSH 隧道:
    #   ssh -N -L 8080:127.0.0.1:8080 user@host
    web_enabled: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8477
    # 留空则每次启动随机生成并打到日志里。填了才有稳定链接。
    # 绑非回环地址时必须显式填，否则拒绝启动。
    web_token: str = ""

    db_path: str = "data/bot.sqlite3"
    log_level: str = "INFO"

    @cached_property
    def admin_ids(self) -> list[int]:
        return _parse_ids(self.telegram_admin_ids)

    @cached_property
    def allowed_chats(self) -> list[int]:
        return _parse_ids(self.telegram_allowed_chats)

    @cached_property
    def muted_chat_ids(self) -> frozenset[int]:
        return frozenset(_parse_ids(self.telegram_muted_chats))

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
    def sync_at(self) -> list[dt.time]:
        times = [self._time(p.strip()) for p in self.sync_times.split(",") if p.strip()]
        return times or [self._time("08:30")]

    @cached_property
    def report_chat_id(self) -> int | None:
        if self.daily_report_chat_id is not None:
            return self.daily_report_chat_id
        return self.allowed_chats[0] if self.allowed_chats else None

    @cached_property
    def report_scope_label(self) -> str:
        base = {0: "当天", 1: "次日", 2: "后天"}.get(
            self.daily_report_offset_days, f"{self.daily_report_offset_days} 天后"
        )
        if self.daily_report_days_ahead == 0:
            return f"仅{base}"
        return f"{base}起 {self.daily_report_days_ahead + 1} 天"

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    def is_allowed_chat(self, chat_id: int) -> bool:
        # 未配置白名单时不做限制，方便本地调试
        return not self.allowed_chats or chat_id in self.allowed_chats


settings = Settings()  # type: ignore[call-arg]
