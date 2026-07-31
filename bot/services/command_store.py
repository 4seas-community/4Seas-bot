"""CRUD over the command YAML files, for the admin web UI.

The files stay the source of truth — the UI is just another editor. That means a
command created in the browser is a plain YAML file you can read, diff, and commit,
and one hand-written in an editor shows up in the UI. No hidden database.

Trade-off: rewriting a file drops its comments. Files created by the UI carry a
header saying so; hand-written files are only rewritten when you actually edit a
command that lives in them, and the UI warns before that happens.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .custom_commands import RESERVED, VALID_NAME, validate_entry

log = logging.getLogger(__name__)

UI_HEADER = (
    "# Managed by the 4Seas Bot admin UI.\n"
    "# Safe to hand-edit — but comments are dropped when the UI next rewrites this file.\n"
)

FIELD_ORDER = (
    "command", "description", "reply", "enabled",
    "admin_only", "scope", "parse_mode", "disable_preview",
)


class StoreError(Exception):
    """A user-facing problem: bad input, name clash, missing command."""


@dataclass(slots=True)
class StoredCommand:
    """A raw entry plus where it lives. Includes disabled ones, unlike the registry."""

    data: dict
    source_file: str

    @property
    def name(self) -> str:
        return str(self.data.get("command", "")).strip().lstrip("/").lower()


class _Dumper(yaml.SafeDumper):
    """Emits multi-line strings as `|` blocks.

    Default safe_dump turns a two-line reply into a single-quoted scalar with a
    blank line standing in for the newline. It round-trips correctly but is
    horrible to hand-edit — and these files are meant to be hand-editable.
    """


def _str_representer(dumper: yaml.Dumper, data: str):
    if "\n" in data:
        # Trailing whitespace on a line makes `|` illegal; fall back rather than
        # emit YAML we can't read back.
        if not any(line != line.rstrip() for line in data.splitlines()):
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def _dump(entries: list[dict]) -> str:
    ordered = []
    for e in entries:
        row = {k: e[k] for k in FIELD_ORDER if k in e}
        row.update({k: v for k, v in e.items() if k not in FIELD_ORDER})
        ordered.append(row)
    return yaml.dump(
        ordered, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=100,
    )


class CommandStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    # ── reading ───────────────────────────────────────────────────────
    def _files(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("*.y*ml"))

    def _read(self, path: Path) -> list[dict]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StoreError(f"{path.name} is not valid YAML: {str(exc).splitlines()[0]}") from exc
        if raw is None:
            return []
        entries = raw if isinstance(raw, list) else [raw]
        return [e for e in entries if isinstance(e, dict)]

    def list(self) -> list[StoredCommand]:
        out: list[StoredCommand] = []
        for path in self._files():
            try:
                for entry in self._read(path):
                    out.append(StoredCommand(entry, path.name))
            except StoreError as exc:
                log.warning("skipping unreadable command file: %s", exc)
        return out

    def file_errors(self) -> list[str]:
        errors = []
        for path in self._files():
            try:
                self._read(path)
            except StoreError as exc:
                errors.append(str(exc))
        return errors

    def get(self, name: str) -> StoredCommand:
        for cmd in self.list():
            if cmd.name == name:
                return cmd
        raise StoreError(f"/{name} does not exist")

    # ── writing ───────────────────────────────────────────────────────
    def _write(self, path: Path, entries: list[dict], *, ui_managed: bool) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if not entries:
            path.unlink(missing_ok=True)
            return
        body = _dump(entries)
        path.write_text((UI_HEADER + "\n" + body) if ui_managed else body, encoding="utf-8")

    def _validated(self, payload: dict, *, exclude: str | None = None) -> dict:
        """Run the same validator the file loader uses, then normalise."""
        others = {c.name: c.source_file for c in self.list() if c.name != exclude}
        cmd, err = validate_entry(payload, "input", others)
        if err:
            # Strip the "input: " source prefix — it means nothing to a UI user.
            raise StoreError(err.split(": ", 1)[-1] if err.startswith("input: ") else err)
        assert cmd is not None
        return {
            "command": cmd.command,
            "description": cmd.description,
            "reply": cmd.reply,
            "enabled": cmd.enabled,
            "admin_only": cmd.admin_only,
            "scope": cmd.scope,
            "parse_mode": cmd.parse_mode,
            "disable_preview": cmd.disable_preview,
        }

    def create(self, payload: dict) -> StoredCommand:
        entry = self._validated(payload)
        name = entry["command"]
        path = self.directory / f"{name}.yaml"
        if path.exists():
            # The name is free but the filename is taken (e.g. a disabled entry
            # elsewhere). Don't clobber someone else's file.
            raise StoreError(f"{path.name} already exists — pick a different command name")
        self._write(path, [entry], ui_managed=True)
        log.info("admin UI created /%s in %s", name, path.name)
        return StoredCommand(entry, path.name)

    def update(self, name: str, payload: dict) -> StoredCommand:
        existing = self.get(name)
        entry = self._validated(payload, exclude=name)
        path = self.directory / existing.source_file

        entries = self._read(path)
        rewritten = [
            entry if str(e.get("command", "")).strip().lstrip("/").lower() == name else e
            for e in entries
        ]
        self._write(path, rewritten, ui_managed=True)

        # Renaming leaves the entry in the old file; that's fine and visible in the UI.
        log.info("admin UI updated /%s (now /%s) in %s", name, entry["command"], path.name)
        return StoredCommand(entry, path.name)

    def delete(self, name: str) -> None:
        existing = self.get(name)
        path = self.directory / existing.source_file
        remaining = [
            e for e in self._read(path)
            if str(e.get("command", "")).strip().lstrip("/").lower() != name
        ]
        # _write removes the file when nothing is left, rather than leaving `[]` behind.
        self._write(path, remaining, ui_managed=True)
        log.info("admin UI deleted /%s from %s", name, path.name)

    def set_enabled(self, name: str, enabled: bool) -> StoredCommand:
        existing = self.get(name)
        payload = dict(existing.data)
        payload["enabled"] = enabled
        return self.update(name, payload)

    def siblings_in_file(self, name: str) -> int:
        """How many other commands share this one's file — the UI warns before a rewrite."""
        try:
            existing = self.get(name)
        except StoreError:
            return 0
        return max(0, len(self._read(self.directory / existing.source_file)) - 1)


__all__ = ["CommandStore", "StoreError", "StoredCommand", "RESERVED", "VALID_NAME"]
