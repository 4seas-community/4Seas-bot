"""Embedded admin web UI, running in the bot's own event loop.

Why in-process rather than a separate service: editing a command has to take effect
immediately, which means calling into the live Application to swap handlers. A separate
process would need IPC or a file watcher for the same result.

Security posture — this endpoint can change what the bot says to a 776-member group:

* Binds to 127.0.0.1 by default. Reaching it from elsewhere means an SSH tunnel,
  which is the right default for an admin surface with no user accounts.
* A token is always required. If none is configured one is generated at startup and
  printed to the log, so there is no "forgot to set a password" hole.
* Binding to a non-loopback address without an explicit token in config is refused.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from aiohttp import web

from ..config import settings
from ..deps import custom_commands, kb, keyword_rules, storage
from ..services.command_store import CommandStore, StoreError
from ..services.custom_commands import RESERVED

log = logging.getLogger(__name__)

INDEX = Path(__file__).parent / "index.html"
TOKEN_HEADER = "X-Admin-Token"


class AdminServer:
    def __init__(self, manager) -> None:
        self.manager = manager  # DynamicCommandManager
        self.store = CommandStore(settings.commands_dir)
        self.token = settings.web_token or secrets.token_urlsafe(18)
        self._generated = not settings.web_token
        self._runner: web.AppRunner | None = None

    # ── auth ──────────────────────────────────────────────────────────
    def _authorised(self, request: web.Request) -> bool:
        supplied = request.headers.get(TOKEN_HEADER) or request.query.get("token", "")
        # Constant-time compare: this endpoint is reachable by anything on the host.
        return bool(supplied) and secrets.compare_digest(supplied, self.token)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path == "/" or self._authorised(request):
            return await handler(request)
        return web.json_response({"error": "unauthorised"}, status=401)

    # ── routes ────────────────────────────────────────────────────────
    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(text=INDEX.read_text(encoding="utf-8"), content_type="text/html")

    def _serialise(self) -> list[dict]:
        live = {c.command for c in custom_commands.commands}
        rows = []
        for stored in self.store.list():
            data = stored.data
            name = stored.name
            rows.append({
                "command": name,
                "description": str(data.get("description", "")),
                "reply": str(data.get("reply", "")),
                "enabled": bool(data.get("enabled", True)),
                "admin_only": bool(data.get("admin_only", False)),
                "scope": str(data.get("scope", "all")),
                "parse_mode": str(data.get("parse_mode", "HTML")),
                "disable_preview": bool(data.get("disable_preview", True)),
                "source_file": stored.source_file,
                "live": name in live,
                "siblings": self.store.siblings_in_file(name),
            })
        return sorted(rows, key=lambda r: r["command"])

    async def _list(self, request: web.Request) -> web.Response:
        return web.json_response({
            "commands": self._serialise(),
            "reserved": sorted(RESERVED),
            "file_errors": self.store.file_errors(),
            "config_errors": custom_commands.errors,
        })

    async def _apply(self) -> dict:
        """Re-register handlers and refresh Telegram's command menu."""
        result = self.manager.reload()
        await self.manager.publish_menu()
        return {"live": [c.command for c in result.commands], "errors": result.errors}

    async def _create(self, request: web.Request) -> web.Response:
        payload = await request.json()
        try:
            self.store.create(payload)
        except StoreError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"ok": True, "applied": await self._apply()})

    async def _update(self, request: web.Request) -> web.Response:
        payload = await request.json()
        try:
            self.store.update(request.match_info["name"], payload)
        except StoreError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"ok": True, "applied": await self._apply()})

    async def _delete(self, request: web.Request) -> web.Response:
        try:
            self.store.delete(request.match_info["name"])
        except StoreError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"ok": True, "applied": await self._apply()})

    async def _toggle(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            self.store.set_enabled(request.match_info["name"], bool(body.get("enabled", True)))
        except StoreError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"ok": True, "applied": await self._apply()})

    async def _reload(self, request: web.Request) -> web.Response:
        kb.load()
        keyword_rules.load()
        return web.json_response({"ok": True, "applied": await self._apply()})

    async def _status(self, request: web.Request) -> web.Response:
        last = storage.last_sync()
        return web.json_response({
            "events": storage.event_stats(),
            "last_sync": {
                "at": last["ran_at"], "source": last["source"], "ok": bool(last["ok"]),
                "fetched": last["fetched"], "inserted": last["inserted"],
                "updated": last["updated"], "unchanged": last["unchanged"],
                "removed": last["removed"], "error": last["error"],
            } if last else None,
            "faq_entries": len(kb.passages),
            "keyword_rules": len(keyword_rules.rules),
            "custom_commands": len(custom_commands.commands),
            "report": {
                "chat_id": settings.report_chat_id,
                "time": settings.daily_report_time,
                "scope": settings.report_scope_label,
            },
            "sync_times": settings.sync_times,
            "muted_chats": sorted(settings.muted_chat_ids),
        })

    def _build(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware])
        app.add_routes([
            web.get("/", self._index),
            web.get("/api/commands", self._list),
            web.post("/api/commands", self._create),
            web.put("/api/commands/{name}", self._update),
            web.delete("/api/commands/{name}", self._delete),
            web.post("/api/commands/{name}/toggle", self._toggle),
            web.post("/api/reload", self._reload),
            web.get("/api/status", self._status),
        ])
        return app

    # ── lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> None:
        host = settings.web_host
        if host not in ("127.0.0.1", "localhost", "::1") and self._generated:
            log.error(
                "Refusing to bind the admin UI to %s without an explicit WEB_TOKEN. "
                "Set WEB_TOKEN in .env, or leave WEB_HOST=127.0.0.1 and use an SSH tunnel.",
                host,
            )
            return

        # The admin UI is a convenience. Never let it take down the bot —
        # a busy port or a permissions problem must not stop Telegram polling.
        try:
            self._runner = web.AppRunner(self._build(), access_log=None)
            await self._runner.setup()
            await web.TCPSite(self._runner, host, settings.web_port).start()
        except OSError as exc:
            hint = (
                f" — something else is already listening on {host}:{settings.web_port}. "
                "Set WEB_PORT in .env to a free port."
                if exc.errno in (48, 98)  # EADDRINUSE on BSD / Linux
                else ""
            )
            log.error("Admin UI failed to start%s (%s). The bot itself is unaffected.", hint, exc)
            await self._cleanup_runner()
            return
        except Exception as exc:
            log.error("Admin UI failed to start: %s. The bot itself is unaffected.", exc, exc_info=True)
            await self._cleanup_runner()
            return

        log.info("Admin UI on http://%s:%s/?token=%s", host, settings.web_port, self.token)
        if self._generated:
            log.warning(
                "WEB_TOKEN not set — generated one for this run only. "
                "It changes on every restart; set WEB_TOKEN in .env to keep the link stable."
            )

    async def _cleanup_runner(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                log.debug("admin UI runner cleanup failed", exc_info=True)
            self._runner = None

    async def stop(self) -> None:
        if self._runner is not None:
            await self._cleanup_runner()
            log.info("Admin UI stopped")
