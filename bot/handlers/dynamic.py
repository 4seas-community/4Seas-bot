"""Wires config-driven commands into a running Application.

PTB lets you add and remove handlers while polling, so /reload can swap the whole
set without a restart. The manager keeps references to what it registered — you
cannot remove a handler you did not keep a handle on.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from ..deps import custom_commands, settings
from ..services.custom_commands import CustomCommand, LoadResult

log = logging.getLogger(__name__)

# Own handler group, so reload can drop them without touching built-ins.
DYNAMIC_GROUP = 5

# Built-in commands shown in Telegram's command menu alongside custom ones.
BUILTIN_MENU = [
    BotCommand("events", "What's coming up"),
    BotCommand("ask", "Ask a question, answered from the FAQ"),
    BotCommand("faq", "List FAQ topics"),
    BotCommand("help", "What this bot can do"),
]


def _make_callback(cmd: CustomCommand):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return

        if cmd.admin_only and not settings.is_admin(user.id if user else None):
            log.info("/%s denied: user %s is not an admin", cmd.command, user.id if user else "?")
            return

        is_private = chat.type == ChatType.PRIVATE
        if cmd.scope == "private" and not is_private:
            return
        if cmd.scope == "group" and is_private:
            return

        log.info(
            "custom command /%s | user=%s chat=%s | from %s",
            cmd.command, user.id if user else "?", chat.id, cmd.source_file,
        )
        await msg.reply_text(
            cmd.reply,
            parse_mode=cmd.telegram_parse_mode,
            disable_web_page_preview=cmd.disable_preview,
        )

    handler.__name__ = f"custom_{cmd.command}"
    return handler


class DynamicCommandManager:
    def __init__(self, app: Application) -> None:
        self.app = app
        self._registered: list[CommandHandler] = []

    def _unregister_all(self) -> int:
        for handler in self._registered:
            try:
                self.app.remove_handler(handler, group=DYNAMIC_GROUP)
            except Exception as exc:  # already gone — not worth failing a reload over
                log.debug("could not remove handler %s: %s", handler, exc)
        count = len(self._registered)
        self._registered = []
        return count

    def reload(self) -> LoadResult:
        """Re-read config and swap the live handler set. Safe to call repeatedly."""
        removed = self._unregister_all()
        result = custom_commands.load()

        for cmd in result.commands:
            handler = CommandHandler(cmd.command, _make_callback(cmd))
            self.app.add_handler(handler, group=DYNAMIC_GROUP)
            self._registered.append(handler)

        log.info(
            "custom commands: -%d +%d → %s",
            removed, len(self._registered),
            "/" + ", /".join(c.command for c in result.commands) if result.commands else "(none)",
        )
        return result

    async def publish_menu(self) -> None:
        """Push the command list to Telegram's UI menu.

        Admin-only and private-scoped commands are left out — the menu is shown to
        everyone in the group, and advertising a command that silently does nothing
        is worse than not listing it.
        """
        visible = [
            BotCommand(c.command, c.description or c.command)
            for c in custom_commands.commands
            if not c.admin_only and c.scope != "private"
        ]
        try:
            await self.app.bot.set_my_commands(BUILTIN_MENU + visible)
            log.info("command menu published: %d entries", len(BUILTIN_MENU) + len(visible))
        except Exception as exc:
            # Cosmetic only — the commands themselves still work.
            log.warning("failed to publish command menu: %s", exc)
