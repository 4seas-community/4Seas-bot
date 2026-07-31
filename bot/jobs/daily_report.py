"""能力 1：每日活动播报。数据从本地库读，不直接打上游。"""

from __future__ import annotations

import datetime as dt
import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..deps import settings, storage
from ..models import Event
from ..render import render_daily_report, render_editorial
from ..services.digest_writer import digest_writer
from ..services.events import day_window
from .sync_events import sync_events

log = logging.getLogger(__name__)


class EventsUnavailable(RuntimeError):
    """读不到数据 —— 和「这天确实没有活动」是两回事，绝不能混为一谈。"""


async def load_events(
    days_ahead: int, *, offset_days: int = 0, allow_sync: bool = True
) -> list[Event]:
    """读库取活动。

    空结果有两种可能，必须分开：
      a) 库里有数据，只是这个时间窗没有 → 真的没活动，返回 []
      b) 库整个是空的（冷启动 / 从没同步成功过）→ 补一次同步；同步也失败就
         抛 EventsUnavailable

    混淆这两者的后果是往 776 人的群里发一条"Nothing scheduled tomorrow"，
    而实际上只是我们没读到数据。
    """
    start, end = day_window(days_ahead, settings.zone, offset_days=offset_days)
    events = storage.query_events(start, end)
    if events or not allow_sync:
        return events

    if storage.event_stats()["live"] > 0:
        return []  # 库里有别的活动，说明这天确实是空的

    log.info("库是空的，先补一次同步再判断")
    ok, detail = await sync_events()
    if not ok:
        raise EventsUnavailable(detail)
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
        log.info("chat %s already got today's digest, skipping", chat_id)
        return "Already posted today's digest (use /report to force a resend)"

    try:
        events = await load_events(days, offset_days=offset)
    except Exception as exc:
        # 关键：不发消息、不标记已播。发一条假的"今天没活动"比不发严重得多，
        # 而且 mark_reported 之后当天就再也不会重试了。
        log.error("取活动失败，本次不播报：%s", exc, exc_info=True)
        await _alert_admins(
            context,
            f"⚠️ Daily digest skipped — could not load events: {exc}\n"
            f"Nothing was sent to {chat_id}. It will retry on the next run, "
            f"or use /report once the data source is back.",
        )
        return f"Skipped — could not load events: {exc}"

    if not events and not settings.daily_report_when_empty:
        log.info("no events for the target day; configured to stay silent")
        if not force:
            storage.mark_reported(chat_id, today)
        return "No events for the target day — staying silent per config"

    target_date = today + dt.timedelta(days=offset)

    if settings.digest_style == "editorial":
        copy = await digest_writer.write(
            events,
            target_date=target_date,
            recent=storage.recent_digests(5),
            days_since_invite=storage.days_since_invite(target_date),
        )
        text = render_editorial(
            events,
            target_date=target_date,
            today=today,
            opening=copy.opening,
            lines=copy.lines,
            closing=copy.closing,
        )
        # 记在发送成功之后 —— 发失败还占掉一个"今天用过 X 角度"的名额，
        # 会让明天白白避开一个其实没出现过的句型。
        pending_digest = copy
    else:
        last = storage.last_sync()
        pending_digest = None
        text = render_daily_report(
            events,
            days_ahead=days,
            today=today,
            offset_days=offset,
            source=last["source"] if last else None,
            style=settings.digest_style,
        )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # 消息已经发出去了，不可撤回 —— 去重标记必须最先写。文案历史只是"下次别撞
    # 句型"的辅助数据，它挂了不能反过来让去重丢失、导致同一天重复播报。
    if not force:
        storage.mark_reported(chat_id, today)

    if pending_digest is not None:
        try:
            storage.record_digest(
                target_date,
                opening_angle=pending_digest.opening_angle,
                closing_angle=pending_digest.closing_angle,
                invite_used=pending_digest.invite_used,
                opening_text=pending_digest.opening,
                closing_text=pending_digest.closing,
                event_count=len(events),
            )
        except Exception as exc:
            log.error("文案历史写入失败（不影响已发出的播报）：%s", exc, exc_info=True)
    log.info(
        "播报完成：chat=%s 活动数=%d 文案=%s",
        chat_id, len(events),
        "LLM" if (pending_digest and pending_digest.generated) else "fallback/静态",
    )
    return f"Posted {len(events)} event(s)"


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
