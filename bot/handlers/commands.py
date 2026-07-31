"""Command handlers: public commands + admin commands."""

from __future__ import annotations

import datetime as dt
import logging
import time

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from ..deps import custom_commands, kb, keyword_rules, llm_service, settings, storage
from ..jobs.daily_report import load_events, send_daily_report
from ..jobs.sync_events import sync_events
from ..render import SOLA_URL, esc, render_daily_report, render_editorial

log = logging.getLogger(__name__)

HELP = """👋 I'm the <b>4Seas community bot</b>.

<b>For everyone:</b>
/events — what's on tomorrow (<code>/events 3</code> for more days)
/ask &lt;question&gt; — ask anything, answered from the community FAQ
/faq — list FAQ topics
/help — this message

You can also just @ me with a question.

Event data comes from <a href="{sola}">Social Layer</a>.
Every evening at {time} I post what's happening <b>tomorrow</b>."""

ADMIN_HELP = """
<b>Admin:</b>
/sync — import events from Social Layer now (idempotent, safe to repeat)
/report — post the digest now
/reload — reload FAQ and keyword rules
/status — runtime status"""


def _log_cmd(update: Update, name: str, extra: str = "") -> None:
    """Log every command. Without this you can't tell "handler never fired"
    apart from "fired but sent nothing" when verifying behaviour."""
    user = update.effective_user
    chat = update.effective_chat
    log.info(
        "command /%s%s | user=%s(@%s) chat=%s(%s)",
        name, f" {extra}" if extra else "",
        user.id if user else "?", user.username if user else "?",
        chat.id if chat else "?", (chat.title or chat.type) if chat else "?",
    )


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return settings.is_admin(user.id if user else None)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_help(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_cmd(update, "help")
    text = HELP.format(time=settings.daily_report_time, sola=SOLA_URL)

    is_admin = _is_admin(update)
    extras = [c for c in custom_commands.commands if is_admin or not c.admin_only]
    if extras:
        lines = "\n".join(
            f"/{c.command}{' — ' + esc(c.description) if c.description else ''}"
            f"{' <i>(admin)</i>' if c.admin_only else ''}"
            for c in extras
        )
        text += f"\n\n<b>Also available:</b>\n{lines}"

    if is_admin:
        text += ADMIN_HELP
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual lookup, same window as the evening digest by default.

    /events    → tomorrow (exactly what the 19:00 post will cover)
    /events 3  → tomorrow plus 3 more days

    Deliberately shares DAILY_REPORT_OFFSET_DAYS / DAILY_REPORT_DAYS_AHEAD with
    the digest rather than having its own setting: if someone checks /events and
    then sees a different set of events posted at 19:00, that reads as a bug.
    """
    days = settings.daily_report_days_ahead
    offset = settings.daily_report_offset_days
    if context.args:
        try:
            days = max(0, min(30, int(context.args[0])))
        except ValueError:
            pass
    _log_cmd(update, "events", f"offset={offset} days={days}")

    msg = update.effective_message
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    try:
        events = await load_events(days, offset_days=offset)
    except Exception as exc:
        log.error("failed to load events: %s", exc, exc_info=True)
        await msg.reply_text(f"😵 Can't reach the event data right now. Try again shortly, or see {SOLA_URL}")
        return

    # DIGEST_STYLE=editorial 在这里会落到 compact：editorial 的每场一句推荐要
    # 走一次 LLM，而 /events 是一周的量、随时可能被任何人触发。列表就够了。
    today = dt.datetime.now(settings.zone).date()
    if settings.digest_style == "editorial" and days == 0:
        # Single day → same editorial layout as the 19:00 post, but without an LLM
        # call: /events is unmetered and anyone in the group can spam it. The
        # organisers' own descriptions carry the recommendation lines.
        from ..services.digest_writer import digest_writer
        copy = await digest_writer.write(
            events, target_date=today + dt.timedelta(days=offset),
            recent=[], days_since_invite=None, use_llm=False,
        )
        text = render_editorial(
            events, target_date=today + dt.timedelta(days=offset), today=today,
            lines=copy.lines,
        )
    else:
        style = "compact" if settings.digest_style == "editorial" else settings.digest_style
        text = render_daily_report(
            events, days_ahead=days, today=today, offset_days=offset, style=style,
        )
    await msg.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_cmd(update, "faq")
    titles = kb.titles()
    if not titles:
        await update.effective_message.reply_text("The FAQ is still empty — admins are working on it 📝")
        return
    body = "\n".join(f"• {esc(t)}" for t in titles)
    await update.effective_message.reply_text(
        f"📚 <b>Community FAQ</b>\n\n{body}\n\nAsk with <code>/ask your question</code>.",
        parse_mode=ParseMode.HTML,
    )


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    """Shared Q&A path for /ask and @-mentions."""
    msg = update.effective_message
    user = update.effective_user
    question = question.strip()

    if not question:
        await msg.reply_text(
            "What would you like to know? e.g. <code>/ask how do I join</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    now = time.time()
    if user and not settings.is_admin(user.id):
        used = storage.ask_count_last_hour(user.id, now)
        if used >= settings.ask_rate_per_hour:
            await msg.reply_text(
                f"⏳ That's {used} questions in the past hour — give it a rest for a bit."
            )
            return

    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    passages = kb.search(question, top_k=3)
    log.info(
        "Q&A %r → FAQ hits: %s",
        question[:40], [p.title for p in passages] or "none (will say it doesn't know)",
    )
    answer = await llm_service.answer(question, passages)

    if user:
        storage.record_ask(user.id, now)

    await msg.reply_text(answer, disable_web_page_preview=True)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_cmd(update, "ask")
    await answer_question(update, context, " ".join(context.args or []))


# ── Admin commands ────────────────────────────────────────────────────────


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_cmd(update, "report")
    if not _is_admin(update):
        return
    target = settings.report_chat_id or update.effective_chat.id
    result = await send_daily_report(context, target, force=True)
    await update.effective_message.reply_text(f"✅ {result}")


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hot-reload everything editable: FAQ, keyword rules, custom commands."""
    _log_cmd(update, "reload")
    if not _is_admin(update):
        return

    n_faq = kb.load()
    n_kw = keyword_rules.load()

    lines = [
        "🔄 <b>Reloaded</b>",
        f"• FAQ: {n_faq} entries",
        f"• Keyword rules: {n_kw}",
    ]

    manager = context.application.bot_data.get("dynamic_commands")
    if manager is not None:
        result = manager.reload()
        await manager.publish_menu()
        if result.commands:
            listed = ", ".join(f"/{c.command}" for c in result.commands)
            lines.append(f"• Custom commands: {len(result.commands)} — {listed}")
        else:
            lines.append("• Custom commands: none")
        if result.skipped:
            lines.append(f"• Disabled (enabled: false): {result.skipped}")
        if result.errors:
            # Report loudly. A silently-ignored typo means an admin edits a file,
            # sees "reloaded", and never learns their command didn't register.
            lines.append("")
            lines.append(f"⚠️ <b>{len(result.errors)} config error(s)</b> — these were skipped:")
            for err in result.errors[:8]:
                lines.append(f"  • {esc(err)}")
            if len(result.errors) > 8:
                lines.append(f"  • … and {len(result.errors) - 8} more (see logs)")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual import. Idempotent — running it repeatedly changes nothing."""
    _log_cmd(update, "sync")
    if not _is_admin(update):
        return
    msg = update.effective_message
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    ok, detail = await sync_events()
    await msg.reply_text(("✅ Sync complete\n" if ok else "❌ Sync failed\n") + detail)


def _next_run(context: ContextTypes.DEFAULT_TYPE, name: str) -> str:
    jq = context.application.job_queue
    jobs = jq.get_jobs_by_name(name) if jq else []
    if jobs and jobs[0].next_t:
        return jobs[0].next_t.astimezone(settings.zone).strftime("%b %-d, %H:%M")
    return "not scheduled"


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log_cmd(update, "status")
    if not _is_admin(update):
        return

    today = dt.datetime.now(settings.zone).date()
    stats = storage.event_stats()
    last = storage.last_sync()

    if last:
        sync_line = (
            f"{'✅' if last['ok'] else '❌'} {last['ran_at'][:16].replace('T', ' ')} UTC · "
            f"{last['source']}"
        )
        detail = (
            f"fetched {last['fetched']} · new {last['inserted']} · updated {last['updated']} · "
            f"unchanged {last['unchanged']} · delisted {last['removed']}"
        )
        err = esc(last["error"]) if last["error"] else None
    else:
        sync_line, detail, err = "never synced", "", None

    lines = [
        "<b>📊 Status</b>",
        "",
        "<b>Event store</b>",
        f"  {stats['live']} live / {stats['total']} total",
        f"  Last sync: {sync_line}",
        f"  {detail}" if detail else None,
        f"  Error: {err}" if err else None,
        f"  Sync schedule: daily at {settings.sync_times} (next {settings.sync_horizon_days} days)",
        "",
        "<b>Digest</b>",
        f"  Target chat: <code>{settings.report_chat_id}</code>",
        f"  Daily at {settings.daily_report_time}, covering {settings.report_scope_label}",
        f"  Next run: {_next_run(context, 'daily_report')}",
        f"  Sent today: {'yes' if storage.already_reported(settings.report_chat_id or 0, today) else 'no'}",
        "",
        "<b>Q&amp;A</b>",
        f"  FAQ entries: {len(kb.passages)}",
        f"  Keyword rules: {len(keyword_rules.rules)}",
        f"  Custom commands: {len(custom_commands.commands)}"
        + (f" ⚠️ {len(custom_commands.errors)} config error(s)" if custom_commands.errors else ""),
        f"  LLM: {', '.join(p.name for p in llm_service.providers) or 'not configured'}",
        f"  Questions in last 24h: {storage.ask_count_today(time.time())}",
    ]
    if settings.muted_chat_ids:
        lines += ["", f"<b>Muted chats</b>: <code>{sorted(settings.muted_chat_ids)}</code>"]

    await update.effective_message.reply_text(
        "\n".join(line for line in lines if line is not None), parse_mode=ParseMode.HTML
    )
