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
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    # Changing this needs the daily jobs re-registered; everything else is read
    # at call time and takes effect on the next use.
    reschedules: bool = False
    # Sending starts/stops going to a real group — worth a confirmation in the UI.
    sensitive: bool = False


FIELDS: tuple[Field, ...] = (
    Field("daily_report_chat_id", "int_or_none", "Digest target chat",
          "Where the 19:00 post goes. The bot must already be a member of this chat — "
          "saving is refused otherwise.", "Delivery", sensitive=True),
    Field("telegram_muted_chats", "ids", "Muted chats",
          "Comma-separated chat ids the bot listens to but never speaks in. "
          "Safer than removing a chat from the allow-list, which can trigger a leave.",
          "Delivery", sensitive=True),
    Field("telegram_allowed_chats", "ids", "Allowed chats",
          "Comma-separated chat ids the bot works in. Empty means every chat.",
          "Delivery", sensitive=True),
    Field("telegram_admin_ids", "ids", "Admin user ids",
          "Comma-separated Telegram user ids allowed to run /sync, /reload, /status. "
          "Error alerts are sent to these people.", "Delivery"),

    Field("daily_report_time", "time", "Digest time", "24h HH:MM in the configured timezone.",
          "Digest", reschedules=True),
    Field("daily_report_offset_days", "int", "Which day",
          "0 = today, 1 = tomorrow. The evening post uses 1.", "Digest", minimum=0, maximum=7),
    Field("daily_report_days_ahead", "int", "Extra days",
          "0 = just that one day. 2 would cover three days in total.",
          "Digest", minimum=0, maximum=30),
    Field("daily_report_when_empty", "bool", "Post when there is nothing on",
          "Off means stay silent on empty days instead of saying so.", "Digest"),
    Field("digest_style", "choice", "Layout",
          "editorial = written copy per event. compact = one line each. "
          "detailed = full cards with address, host and headcount.",
          "Digest", choices=("editorial", "compact", "detailed")),

    Field("sync_times", "times", "Import times",
          "Comma-separated HH:MM. At least one must fall before the digest time, "
          "or the post can miss events added that day.", "Events", reschedules=True),
    Field("sync_horizon_days", "int", "Import window (days)",
          "How far ahead to pull. Larger than the digest window on purpose, so "
          "changing the digest range needs no re-import.", "Events", minimum=1, maximum=365),
    Field("detail_enrich_days", "int", "Detail lookup window (days)",
          "Venue and description come from a per-event request, so only nearby "
          "events are enriched.", "Events", minimum=0, maximum=60),

    Field("reply_language", "str", "Reply language",
          "Language the bot writes in, whatever language it is asked in.", "Q&A"),
    Field("ask_rate_per_hour", "int", "Questions per user per hour",
          "How many times one person may use /ask or @-mention the bot per hour "
          "before it politely declines. Admins are exempt. This is the main guard "
          "on LLM spend.", "Q&A", minimum=1, maximum=100),
    Field("keyword_default_cooldown", "int", "Keyword cooldown (seconds)",
          "Default gap before the same keyword rule can fire again in one chat. "
          "Individual rules may override it.", "Q&A", minimum=0, maximum=86400),
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
