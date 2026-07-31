"""Settings that can be changed from the admin UI without editing files.

Layering: `.env` is the baseline, `data/runtime_config.json` overrides it. The JSON
only ever contains keys someone actually changed, so `.env` stays readable as "how
this deployment was set up" and the JSON reads as "what was adjusted since".

Secrets are deliberately NOT editable here. A web form that can rewrite the bot
token or an API key turns a localhost page into a credential store; those stay in
`.env`, where file permissions are the control.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

PATH = Path("data/runtime_config.json")

FieldKind = Literal["int", "int_or_none", "str", "bool", "ids", "choice", "time", "times"]


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    kind: FieldKind
    label: str
    help: str
    group: str
    # 中文是管理页的默认语言：运营看中文，代码和日志留英文。
    label_zh: str = ""
    help_zh: str = ""
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    # Changing this needs the daily jobs re-registered; everything else is read
    # at call time and takes effect on the next use.
    reschedules: bool = False
    # Sending starts/stops going to a real group — worth a confirmation in the UI.
    sensitive: bool = False


GROUPS = {
    "Delivery": "投递",
    "Digest": "每日播报",
    "Events": "活动数据",
    "Q&A": "问答",
}

FIELDS: tuple[Field, ...] = (
    Field("daily_report_chat_id", "int_or_none",
          "Digest target chat",
          "Where the 19:00 post goes. The bot must already be a member of this chat — "
          "saving is refused otherwise.",
          "Delivery",
          "播报目标群",
          "每晚 19:00 的活动预告发到哪个群。bot 必须已经在这个群里，否则保存会被拒绝。",
          sensitive=True),
    Field("telegram_muted_chats", "ids",
          "Muted chats",
          "Comma-separated chat ids the bot listens to but never speaks in. "
          "Safer than removing a chat from the allow-list, which can trigger a leave.",
          "Delivery",
          "静默群",
          "逗号分隔的群 id。bot 在这些群里收消息但一句话不说。比从白名单里删掉安全 —— "
          "删掉可能触发退群。",
          sensitive=True),
    Field("telegram_allowed_chats", "ids",
          "Allowed chats",
          "Comma-separated chat ids the bot works in. Empty means every chat.",
          "Delivery",
          "白名单群",
          "逗号分隔的群 id，bot 只在这些群里工作。留空表示不限制。",
          sensitive=True),
    Field("telegram_admin_ids", "ids",
          "Admin user ids",
          "Comma-separated Telegram user ids allowed to run /sync, /reload, /status. "
          "Error alerts are sent to these people.",
          "Delivery",
          "管理员用户 id",
          "逗号分隔的 Telegram 用户 id，只有这些人能用 /sync、/reload、/status。"
          "异常告警也发给他们。"),

    Field("daily_report_time", "time",
          "Digest time", "24h HH:MM in the configured timezone.",
          "Digest",
          "播报时间", "24 小时制 HH:MM，按下方时区。",
          reschedules=True),
    Field("daily_report_offset_days", "int",
          "Which day", "0 = today, 1 = tomorrow. The evening post uses 1.",
          "Digest",
          "播哪一天", "0 = 当天，1 = 明天。晚上预告次日活动用 1。",
          minimum=0, maximum=7),
    Field("daily_report_days_ahead", "int",
          "Extra days", "0 = just that one day. 2 would cover three days in total.",
          "Digest",
          "多播几天", "0 = 只播那一天。填 2 就是一共三天。",
          minimum=0, maximum=30),
    Field("daily_report_when_empty", "bool",
          "Post when there is nothing on",
          "Off means stay silent on empty days instead of saying so.",
          "Digest",
          "没有活动时是否发",
          "关掉的话，没活动的日子就完全不发，而不是发一条「明天没有安排」。"),
    Field("digest_style", "choice",
          "Layout",
          "editorial = written copy per event. compact = one line each. "
          "detailed = full cards with address, host and headcount.",
          "Digest",
          "排版风格",
          "editorial = 每场一句推荐的社群文案；compact = 一行一场；"
          "detailed = 完整卡片，含地址、主办、报名人数。",
          choices=("editorial", "compact", "detailed")),

    Field("sync_times", "times",
          "Import times",
          "Comma-separated HH:MM. At least one must fall before the digest time, "
          "or the post can miss events added that day.",
          "Events",
          "导入时间",
          "逗号分隔的 HH:MM。至少要有一个早于播报时间，否则当天新加的活动赶不上当晚的预告。",
          reschedules=True),
    Field("sync_horizon_days", "int",
          "Import window (days)",
          "How far ahead to pull. Larger than the digest window on purpose, so "
          "changing the digest range needs no re-import.",
          "Events",
          "导入窗口（天）",
          "一次拉取未来多少天。刻意比播报范围大，这样改播报范围不用重新导入。",
          minimum=1, maximum=365),
    Field("detail_enrich_days", "int",
          "Detail lookup window (days)",
          "Venue and description come from a per-event request, so only nearby "
          "events are enriched.",
          "Events",
          "详情补齐窗口（天）",
          "场地和活动介绍要一场一个请求才拿得到，所以只补最近这些天的。",
          minimum=0, maximum=60),

    Field("reply_language", "str",
          "Reply language",
          "Language the bot writes in, whatever language it is asked in.",
          "Q&A",
          "回复语言", "bot 用哪种语言回答，不管别人用什么语言提问。"),
    Field("ask_rate_per_hour", "int",
          "Questions per user per hour",
          "How many times one person may use /ask or @-mention the bot per hour "
          "before it politely declines. Admins are exempt. This is the main guard "
          "on LLM spend.",
          "Q&A",
          "每人每小时提问上限",
          "一个人一小时内能用 /ask 或 @ bot 几次，超了会被礼貌拒绝。管理员不受限。"
          "这是控制 LLM 花费的主要手段。",
          minimum=1, maximum=100),
    Field("keyword_default_cooldown", "int",
          "Keyword cooldown (seconds)",
          "Default gap before the same keyword rule can fire again in one chat. "
          "Individual rules may override it.",
          "Q&A",
          "关键词冷却（秒）",
          "同一条关键词规则在一个群里两次触发之间的最小间隔。单条规则可以自己覆盖。",
          minimum=0, maximum=86400),
)

BY_KEY = {f.key: f for f in FIELDS}


class ConfigError(ValueError):
    """A user-facing validation problem."""


def _parse_time(value: str, label: str) -> str:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ConfigError(f"{label}: use HH:MM, e.g. 19:00")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise ConfigError(f"{label}: use HH:MM, e.g. 19:00") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ConfigError(f"{label}: {value!r} is not a valid time of day")
    return f"{hh:02d}:{mm:02d}"


def coerce(field: Field, raw: Any) -> Any:
    """Validate and normalise one submitted value, or raise ConfigError."""
    if field.kind == "bool":
        return bool(raw)

    text = "" if raw is None else str(raw).strip()

    if field.kind in ("int", "int_or_none"):
        if not text:
            if field.kind == "int_or_none":
                return None
            raise ConfigError(f"{field.label}: required")
        try:
            number = int(text)
        except ValueError:
            raise ConfigError(f"{field.label}: {text!r} is not a whole number") from None
        if field.minimum is not None and number < field.minimum:
            raise ConfigError(f"{field.label}: must be at least {field.minimum}")
        if field.maximum is not None and number > field.maximum:
            raise ConfigError(f"{field.label}: must be at most {field.maximum}")
        return number

    if field.kind == "ids":
        if not text:
            return ""
        out = []
        for part in text.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(str(int(part)))
            except ValueError:
                raise ConfigError(f"{field.label}: {part!r} is not a numeric id") from None
        return ",".join(out)

    if field.kind == "time":
        return _parse_time(text, field.label)

    if field.kind == "times":
        parts = [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]
        if not parts:
            raise ConfigError(f"{field.label}: at least one time is required")
        return ",".join(_parse_time(p, field.label) for p in parts)

    if field.kind == "choice":
        if text not in field.choices:
            raise ConfigError(f"{field.label}: pick one of {', '.join(field.choices)}")
        return text

    if not text:
        raise ConfigError(f"{field.label}: required")
    return text


def load() -> dict[str, Any]:
    if not PATH.exists():
        return {}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Never let a corrupt overrides file stop the bot booting — .env alone is
        # a working configuration.
        log.error("ignoring unreadable %s: %s", PATH, exc)
        return {}
    if not isinstance(data, dict):
        log.error("%s is not an object — ignoring", PATH)
        return {}

    # Re-validate on the way in. The web form validates before writing, but this
    # file is hand-editable, and an out-of-range or malformed value would sail
    # straight past the pydantic Field constraints (assignment isn't validated)
    # and crash the bot at import time — with no bot, there is nothing to report
    # the error with. Drop the bad key, keep the rest, say so in the log.
    clean: dict[str, Any] = {}
    for key, value in data.items():
        field = BY_KEY.get(key)
        if field is None:
            continue
        try:
            clean[key] = coerce(field, value)
        except ConfigError as exc:
            log.error("ignoring %s in %s: %s", key, PATH, exc)
    return clean


def save(overrides: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PATH)  # atomic: a crash mid-write must not leave a half file
    log.info("runtime config saved: %s", ", ".join(sorted(overrides)) or "(empty)")
