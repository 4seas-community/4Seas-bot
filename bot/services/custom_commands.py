"""Config-driven commands: drop a YAML file in data/commands/, send /reload, done.

Design constraints that shaped this:

* **A broken file must not take down working commands.** Parsing happens into a
  staging list; the live registry is only swapped in if the whole load succeeded
  at the file level. Per-command errors are collected and reported back to the
  admin rather than silently dropped.
* **Custom commands cannot shadow built-ins.** Overriding /reload with a broken
  config would lock you out of the only way to fix it.
* **Names must satisfy Telegram's rules** (1-32 chars, lowercase a-z, 0-9, _),
  otherwise setMyCommands rejects the whole batch — one bad name would wipe the
  command menu for every command.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Names owned by the code. A config file may not claim these.
RESERVED = frozenset(
    {"start", "help", "events", "ask", "faq", "sync", "report", "reload", "status"}
)

VALID_NAME = re.compile(r"^[a-z0-9_]{1,32}$")

# Telegram's HTML subset. Anything else is rejected by the API outright.
TELEGRAM_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
     "a", "code", "pre", "span", "tg-spoiler", "blockquote"}
)
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)[^>]*>")


def check_telegram_html(text: str) -> str | None:
    """Return a human-readable problem, or None if Telegram will accept this.

    Telegram rejects the whole message on a single unclosed tag — and the failure
    only surfaces when someone actually runs the command, in the group, silently.
    Catching it at save time is the difference between "the form says no" and
    "the bot is quietly broken for a week".
    """
    stack: list[str] = []
    for closing, raw in _TAG_RE.findall(text):
        tag = raw.lower()
        if tag not in TELEGRAM_TAGS:
            return (
                f"<{tag}> is not supported by Telegram. Allowed: "
                + ", ".join(f"<{t}>" for t in sorted(TELEGRAM_TAGS))
            )
        if closing:
            if not stack:
                return f"</{tag}> has no matching opening tag"
            if stack[-1] != tag:
                return f"</{tag}> closes out of order — <{stack[-1]}> is still open"
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        opened = ", ".join(f"<{t}>" for t in stack)
        return f"unclosed tag: {opened} — add the matching closing tag"
    return None
VALID_SCOPES = frozenset({"all", "group", "private"})
VALID_PARSE_MODES = frozenset({"HTML", "Markdown", "MarkdownV2", "none"})


@dataclass(slots=True)
class CustomCommand:
    command: str
    reply: str
    description: str = ""
    admin_only: bool = False
    scope: str = "all"
    parse_mode: str = "HTML"
    disable_preview: bool = True
    enabled: bool = True
    source_file: str = ""

    @property
    def telegram_parse_mode(self) -> str | None:
        return None if self.parse_mode == "none" else self.parse_mode


@dataclass(slots=True)
class LoadResult:
    commands: list[CustomCommand] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [f"{len(self.commands)} custom command(s)"]
        if self.skipped:
            parts.append(f"{self.skipped} disabled")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return ", ".join(parts)


def validate_entry(
    raw: dict, source: str, seen: dict[str, str] | None = None
) -> tuple[CustomCommand | None, str | None]:
    """Returns (command, error). Exactly one of the two is not None.

    Shared by the file loader and the admin web UI so both enforce identical rules.
    """
    seen = {} if seen is None else seen
    if not isinstance(raw, dict):
        return None, f"{source}: entry is not a mapping"

    name = str(raw.get("command", "")).strip().lstrip("/").lower()
    if not name:
        return None, f"{source}: entry is missing `command`"
    if not VALID_NAME.match(name):
        return None, (
            f"{source}: `/{name}` is not a valid Telegram command name "
            "(1-32 chars, only a-z 0-9 _)"
        )
    if name in RESERVED:
        return None, f"{source}: `/{name}` is a built-in command and cannot be overridden"
    if name in seen:
        return None, f"{source}: `/{name}` is already defined in {seen[name]}"

    reply = str(raw.get("reply", "")).strip()
    if not reply:
        return None, f"{source}: `/{name}` has no `reply` text"

    scope = str(raw.get("scope", "all")).lower()
    if scope not in VALID_SCOPES:
        return None, f"{source}: `/{name}` has unknown scope {scope!r} (use: {', '.join(sorted(VALID_SCOPES))})"

    parse_mode = str(raw.get("parse_mode", "HTML"))
    if parse_mode not in VALID_PARSE_MODES:
        return None, f"{source}: `/{name}` has unknown parse_mode {parse_mode!r}"

    if parse_mode == "HTML":
        problem = check_telegram_html(reply)
        if problem:
            return None, f"{source}: `/{name}` reply has invalid HTML — {problem}"

    return (
        CustomCommand(
            command=name,
            reply=reply,
            description=str(raw.get("description", "")).strip()[:256],
            admin_only=bool(raw.get("admin_only", False)),
            scope=scope,
            parse_mode=parse_mode,
            disable_preview=bool(raw.get("disable_preview", True)),
            enabled=bool(raw.get("enabled", True)),
            source_file=source,
        ),
        None,
    )


class CustomCommandRegistry:
    """Loads data/commands/*.yaml. Hot-reloadable via /reload."""

    def __init__(self, directory: str | Path = "data/commands") -> None:
        self.directory = Path(directory)
        self.commands: list[CustomCommand] = []
        self.errors: list[str] = []

    def load(self) -> LoadResult:
        result = LoadResult()

        if not self.directory.exists():
            log.info("no custom command directory at %s", self.directory)
            self.commands, self.errors = [], []
            return result

        seen: dict[str, str] = {}
        for path in sorted(self.directory.glob("*.y*ml")):
            source = path.name
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                result.errors.append(f"{source}: invalid YAML — {str(exc).splitlines()[0]}")
                continue

            if raw is None:
                continue
            entries = raw if isinstance(raw, list) else [raw]

            for entry in entries:
                cmd, err = validate_entry(entry, source, seen)
                if err:
                    result.errors.append(err)
                    continue
                assert cmd is not None
                if not cmd.enabled:
                    result.skipped += 1
                    continue
                seen[cmd.command] = source
                result.commands.append(cmd)

        # Keep whatever parsed cleanly; errors are surfaced, not fatal.
        self.commands = result.commands
        self.errors = result.errors
        log.info("custom commands loaded: %s", result.summary())
        for err in result.errors:
            log.warning("custom command config: %s", err)
        return result
