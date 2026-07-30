"""能力 1：每日活动播报。数据从本地库读，不直接打上游。"""

from __future__ import annotations

import datetime as dt
import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..deps import settings, storage
from ..models import Event
from ..render import render_daily_report
from ..services.events import day_window
from .sync_events import sync_events

log = logging.getLogger(__name__)


async def load_events(
    days_ahead: int, *, offset_days: int = 0, allow_sync: bool = True
) -> list[Event]:
    """读库取活动。库里为空且允许同步时，先补一次同步再读。"""
    start, end = day_window(days_ahead, settings.zone, offset_days=offset_days)
    events = storage.query_events(start, end)
    if events or not allow_sync:
        return events

    # 冷启动，或定时同步还没跑过
    log.info("库内该时段无活动，先触发一次同步")
    ok, detail = await sync_events()
    if not ok:
        log.warning("补同步失败：%s", detail)
    return storage.query_events(start, end)


async def send_daily_report(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    force: bool = False,
    days_ahead: int | None = None,
    offset_days: int | None = None,
) -> str:
    """读库 → 渲染 → 发送。返回一句给管理员看的执行结果。"""
    today = dt.datetime.now(settings.zone).date()
    days = settings.daily_report_days_ahead if days_ahead is None else days_ahead
    offset = settings.daily_report_offset_days if offset_days is None else offset_days

    if not force and storage.already_reported(chat_id, today):
        log.info("chat %s 今天已播报过，跳过", chat_id)
        return "今天已经播报过了（用 /report 可强制重发）"

    try:
        events = await load_events(days, offset_days=offset)
    except Exception as exc:
        log.error("取活动失败：%s", exc, exc_info=True)
        await _alert_admins(context, f"⚠️ 每日播报取数失败：{exc}")
        return f"取数失败：{exc}"

    if not events and not settings.daily_report_when_empty:
        log.info("目标日期没有活动且配置为静默跳过")
        if not force:
            storage.mark_reported(chat_id, today)
        return "目标日期没有活动，按配置静默跳过"

    last = storage.last_sync()
    text = render_daily_report(
        events,
        days_ahead=days,
        today=today,
        offset_days=offset,
        source=last["source"] if last else None,
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    if not force:
        storage.mark_reported(chat_id, today)
    log.info("播报完成：chat=%s 活动数=%d", chat_id, len(events))
    return f"已播报 {len(events)} 场活动"


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = settings.report_chat_id
    if chat_id is None:
        log.error("未配置 DAILY_REPORT_CHAT_ID / TELEGRAM_ALLOWED_CHATS，跳过播报")
        return
    await send_daily_report(context, chat_id)


async def _alert_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception as exc:
            log.warning("给管理员 %s 发告警失败：%s", admin_id, exc)
