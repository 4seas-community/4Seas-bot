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
from ..deps import custom_commands, kb, keyword_rules, settings as _s, storage
from ..services.command_store import CommandStore, StoreError
from ..services.custom_commands import RESERVED, check_telegram_html
from ..services import runtime_config as rc

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

    async def _check_html(self, request: web.Request) -> web.Response:
        """Live syntax check for the reply box — the save path enforces the same
        rule, but telling someone only after they hit Save is a poor trade."""
        body = await request.json()
        if str(body.get("parse_mode", "HTML")) != "HTML":
            return web.json_response({"problem": None})
        return web.json_response({"problem": check_telegram_html(str(body.get("reply", "")))})

    async def _send_digest(self, request: web.Request) -> web.Response:
        """Send the digest now.

        This lives here rather than as a /report chat command because it is an
        ops action, not something a group member does — and because the digest
        goes to DAILY_REPORT_CHAT_ID, not to wherever the command was typed,
        which made a chat command genuinely dangerous to hand out.

        Real use: the 19:00 job is `run_daily`, so a missed evening (laptop
        asleep) is simply never sent. This is the only way to catch up.
        """
        chat_id = _s.report_chat_id
        if chat_id is None:
            return web.json_response({"error": "no DAILY_REPORT_CHAT_ID configured"}, status=400)
        if chat_id in _s.muted_chat_ids:
            return web.json_response(
                {"error": f"chat {chat_id} is muted — nothing would be sent"}, status=400
            )

        from ..jobs.daily_report import send_daily_report

        class _Ctx:  # send_daily_report only ever touches .bot
            bot = self.manager.app.bot

        try:
            detail = await send_daily_report(_Ctx(), chat_id, force=True)
        except Exception as exc:
            log.error("admin UI digest send failed: %s", exc, exc_info=True)
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"ok": True, "detail": detail, "chat_id": chat_id})

    # ── settings ──────────────────────────────────────────────────────
    async def _settings_get(self, request: web.Request) -> web.Response:
        overrides = rc.load()
        return web.json_response({
            "fields": [
                {
                    "key": f.key, "kind": f.kind,
                    "label": f.label, "help": f.help, "group": f.group,
                    "label_zh": f.label_zh or f.label,
                    "help_zh": f.help_zh or f.help,
                    "group_zh": rc.GROUPS.get(f.group, f.group),
                    "choices": list(f.choices),
                    "min": f.minimum, "max": f.maximum,
                    "reschedules": f.reschedules, "sensitive": f.sensitive,
                    "value": getattr(_s, f.key, None),
                    "overridden": f.key in overrides,
                }
                for f in rc.FIELDS
            ],
            "timezone": _s.tz,
        })

    async def _verify_chat(self, chat_id: int) -> str | None:
        """Confirm the bot can actually post there. Returns a problem, or None.

        Pointing the digest at a chat the bot was never added to fails silently at
        19:00 — the job runs, the send raises, and the only trace is a log line
        nobody reads. Check it while someone is looking at the screen.
        """
        bot = self.manager.app.bot
        try:
            chat = await bot.get_chat(chat_id)
        except Exception as exc:
            return (
                f"can't reach chat {chat_id}: {exc}. "
                "Add the bot to that chat first, then try again."
            )
        if chat.type == "private":
            return None

        try:
            me = await bot.get_chat_member(chat_id, bot.id)
        except Exception as exc:
            return f"the bot is not a member of {chat.title!r}: {exc}"
        if me.status not in ("administrator", "creator"):
            return (
                f"the bot is in {chat.title!r} but only a {me.status}. "
                "Make it an administrator, or it may be unable to post."
            )
        return None

    async def _settings_put(self, request: web.Request) -> web.Response:
        body = await request.json()
        submitted = body if isinstance(body, dict) else {}

        cleaned: dict = {}
        try:
            for key, raw in submitted.items():
                field = rc.BY_KEY.get(key)
                if field is None:
                    continue  # unknown keys are ignored, not an error
                cleaned[key] = rc.coerce(field, raw)
        except rc.ConfigError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        # Verify chat membership before persisting anything.
        target = cleaned.get("daily_report_chat_id")
        if target is not None and target != _s.report_chat_id:
            problem = await self._verify_chat(int(target))
            if problem:
                return web.json_response({"error": problem}, status=400)

        overrides = {**rc.load(), **cleaned}
        rc.save(overrides)
        _s.apply_overrides(overrides)

        notes: list[str] = []
        if any(rc.BY_KEY[k].reschedules for k in cleaned):
            notes.append(self._reschedule())
        if _s.report_chat_id in _s.muted_chat_ids:
            notes.append(
                f"⚠️ chat {_s.report_chat_id} is on the muted list — the digest will not go out"
            )
        return web.json_response({"ok": True, "notes": notes})

    def _reschedule(self) -> str:
        """Re-register the daily jobs so a new time takes effect without a restart."""
        from ..jobs.daily_report import daily_report_job
        from ..jobs.sync_events import sync_events_job

        jq = self.manager.app.job_queue
        if jq is None:
            return "job queue unavailable — restart to apply the new times"

        for job in jq.jobs():
            if job.name and (job.name.startswith("sync_events") or job.name == "daily_report"):
                job.schedule_removal()
        for i, at in enumerate(_s.sync_at):
            jq.run_daily(sync_events_job, time=at,
                         name="sync_events" if i == 0 else f"sync_events_{i}")
        jq.run_daily(daily_report_job, time=_s.report_time, name="daily_report")
        log.info("rescheduled: sync %s, digest %s", _s.sync_times, _s.daily_report_time)
        return f"rescheduled — sync {_s.sync_times}, digest {_s.daily_report_time}"

    async def _check_chat(self, request: web.Request) -> web.Response:
        """Pre-flight a chat id from the form, before anything is saved."""
        body = await request.json()
        try:
            chat_id = int(str(body.get("chat_id", "")).strip())
        except ValueError:
            return web.json_response({"problem": "not a numeric chat id"})
        problem = await self._verify_chat(chat_id)
        if problem:
            return web.json_response({"problem": problem})
        try:
            chat = await self.manager.app.bot.get_chat(chat_id)
            title = chat.title or chat.username or "private chat"
            members = await self.manager.app.bot.get_chat_member_count(chat_id)
            return web.json_response({"problem": None, "ok": f"{title} — {members} members"})
        except Exception:
            return web.json_response({"problem": None, "ok": "reachable"})

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
            web.post("/api/check-html", self._check_html),
            web.post("/api/send-digest", self._send_digest),
            web.get("/api/settings", self._settings_get),
            web.put("/api/settings", self._settings_put),
            web.post("/api/check-chat", self._check_chat),
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
