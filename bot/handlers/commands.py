"""命令处理：公开命令 + 管理员命令。"""

from __future__ import annotations

import datetime as dt
import logging
import time

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from ..deps import kb, keyword_rules, llm_service, settings, storage
from ..jobs.daily_report import load_events, send_daily_report
from ..jobs.sync_events import sync_events
from ..render import esc, render_daily_report

log = logging.getLogger(__name__)

HELP = """👋 我是 <b>4Seas 社区机器人</b>

<b>大家都能用：</b>
/events — 看看近期有什么活动
/ask 你的问题 — 基于社区 FAQ 回答
/faq — 列出 FAQ 目录
/help — 这条消息

也可以直接 @我 提问。

活动数据来自 <a href="https://app.sola.day/event/4seas">Social Layer</a>，
每天 {time} 我会在群里播报当天活动。"""

ADMIN_HELP = """
<b>管理员：</b>
/sync — 立刻从 Social Layer 导入一次活动（幂等，可重复执行）
/report — 立刻手动播报一次
/reload — 重新加载 FAQ 和关键词规则
/status — 运行状态"""


def _admin_only(update: Update) -> bool:
    user = update.effective_user
    return settings.is_admin(user.id if user else None)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_help(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = HELP.format(time=settings.daily_report_time)
    if _admin_only(update):
        text += ADMIN_HELP
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """手动查活动。可以带参数：/events 3 表示看未来 3 天。"""
    days = settings.daily_report_days_ahead
    if context.args:
        try:
            days = max(0, min(30, int(context.args[0])))
        except ValueError:
            pass

    msg = update.effective_message
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    try:
        events = await load_events(days)
    except Exception as exc:
        log.error("查活动失败：%s", exc, exc_info=True)
        await msg.reply_text("😵 活动数据暂时取不到，稍后再试；或直接看 https://app.sola.day/event/4seas")
        return

    text = render_daily_report(
        events, days_ahead=days, today=dt.datetime.now(settings.zone).date()
    )
    await msg.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    titles = kb.titles()
    if not titles:
        await update.effective_message.reply_text("FAQ 还是空的，管理员正在整理 📝")
        return
    body = "\n".join(f"• {esc(t)}" for t in titles)
    await update.effective_message.reply_text(
        f"📚 <b>社区 FAQ</b>\n\n{body}\n\n用 <code>/ask 你的问题</code> 提问。",
        parse_mode=ParseMode.HTML,
    )


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    """问答主链路。/ask 和 @我 共用。"""
    msg = update.effective_message
    user = update.effective_user
    question = question.strip()

    if not question:
        await msg.reply_text("问点什么呢？比如 <code>/ask 怎么加入社区</code>", parse_mode=ParseMode.HTML)
        return

    now = time.time()
    if user and not settings.is_admin(user.id):
        used = storage.ask_count_last_hour(user.id, now)
        if used >= settings.ask_rate_per_hour:
            await msg.reply_text(
                f"⏳ 你这一小时已经问了 {used} 次，歇会儿再来吧（管理员不限）"
            )
            return

    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    passages = kb.search(question, top_k=3)
    answer = await llm_service.answer(question, passages)

    if user:
        storage.record_ask(user.id, now)

    await msg.reply_text(answer, disable_web_page_preview=True)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await answer_question(update, context, " ".join(context.args or []))


# ── 管理员命令 ────────────────────────────────────────────────────────────


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    target = settings.report_chat_id or update.effective_chat.id
    result = await send_daily_report(context, target, force=True)
    await update.effective_message.reply_text(f"✅ {result}")


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return
    n_faq = kb.load()
    n_kw = keyword_rules.load()
    await update.effective_message.reply_text(
        f"🔄 已重新加载：FAQ {n_faq} 条，关键词规则 {n_kw} 条"
    )


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """手动触发一次导入。幂等，随便点几次都不会产生重复数据。"""
    if not _admin_only(update):
        return
    msg = update.effective_message
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    ok, detail = await sync_events()
    await msg.reply_text(("✅ 同步完成\n" if ok else "❌ 同步失败\n") + detail)


def _next_run(context: ContextTypes.DEFAULT_TYPE, name: str) -> str:
    jq = context.application.job_queue
    jobs = jq.get_jobs_by_name(name) if jq else []
    if jobs and jobs[0].next_t:
        return jobs[0].next_t.astimezone(settings.zone).strftime("%m-%d %H:%M")
    return "未调度"


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_only(update):
        return

    today = dt.datetime.now(settings.zone).date()
    scope = "仅当天" if settings.daily_report_days_ahead == 0 else f"当天 + 未来 {settings.daily_report_days_ahead} 天"
    stats = storage.event_stats()
    last = storage.last_sync()

    if last:
        sync_line = (
            f"{'✅' if last['ok'] else '❌'} {last['ran_at'][:16].replace('T', ' ')} UTC · "
            f"{last['source']}"
        )
        detail = (
            f"拉取 {last['fetched']} · 新增 {last['inserted']} · 更新 {last['updated']} · "
            f"无变化 {last['unchanged']} · 下架 {last['removed']}"
        )
        err = esc(last["error"]) if last["error"] else None
    else:
        sync_line, detail, err = "尚未同步过", "", None

    lines = [
        "<b>📊 运行状态</b>",
        "",
        "<b>活动库</b>",
        f"  在架 {stats['live']} / 总计 {stats['total']} 条",
        f"  上次同步：{sync_line}",
        f"  {detail}" if detail else None,
        f"  错误：{err}" if err else None,
        f"  下次同步：{_next_run(context, 'sync_events')}（每天 {settings.sync_time}，导入未来 {settings.sync_horizon_days} 天）",
        "",
        "<b>播报</b>",
        f"  目标群：<code>{settings.report_chat_id}</code>",
        f"  范围：{scope}",
        f"  下次播报：{_next_run(context, 'daily_report')}",
        f"  今天是否已播：{'是' if storage.already_reported(settings.report_chat_id or 0, today) else '否'}",
        "",
        "<b>问答</b>",
        f"  FAQ 条目：{len(kb.passages)}",
        f"  关键词规则：{len(keyword_rules.rules)}",
        f"  LLM：{'、'.join(p.name for p in llm_service.providers) or '未配置'}",
        f"  近 24h 提问：{storage.ask_count_today(time.time())} 次",
    ]
    await update.effective_message.reply_text(
        "\n".join(line for line in lines if line is not None), parse_mode=ParseMode.HTML
    )
