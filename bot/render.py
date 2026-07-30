"""把 Event 渲染成 Telegram 消息。

用 HTML parse_mode 而不是 MarkdownV2 —— 后者要求转义 `_*[]()~>#+-=|{}.!`,
活动标题里这些字符满地都是,漏一个就整条消息发不出去。
"""

from __future__ import annotations

import datetime as dt
import html

from .models import Event

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MAX_LEN = 4096  # Telegram 单条消息上限


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def fmt_date(d: dt.date) -> str:
    return f"{d.month}月{d.day}日 {WEEKDAYS[d.weekday()]}"


def fmt_time_range(ev: Event) -> str:
    if ev.is_all_day:
        return "🕘 全天"
    start = ev.local_start
    end = ev.local_end
    if end is None:
        return f"🕘 {start:%H:%M}"
    if end.date() != start.date():  # 跨天
        return f"🕘 {start:%H:%M} → {end:%m-%d %H:%M}"
    return f"🕘 {start:%H:%M}–{end:%H:%M}"


def render_event(ev: Event, index: int | None = None) -> str:
    """单个活动的详细卡片。"""
    title = esc(ev.title)
    head = f"<b>{index}. {title}</b>" if index else f"<b>{title}</b>"
    lines = [head, fmt_time_range(ev)]

    place = ev.place_title or ev.place_address
    if place and ev.place_title and ev.place_address and ev.place_title != ev.place_address:
        place = f"{ev.place_title} · {ev.place_address}"
    if place:
        lines.append(f"📍 {esc(place)}")

    meta: list[str] = []
    if ev.host:
        meta.append(f"👤 {esc(ev.host)}")
    if ev.participants is not None:
        seats = f"{ev.participants}"
        if ev.max_participants:
            seats += f"/{ev.max_participants}"
        meta.append(f"👥 {seats} 人已报名")
    if ev.require_approval:
        meta.append("🔒 需审核")
    if meta:
        lines.append(" · ".join(meta))

    if ev.tags:
        lines.append("🏷 " + esc(" · ".join(ev.tags[:5])))

    if ev.notes:
        note = " ".join(ev.notes.split())
        if note:
            lines.append(f"📝 {esc(note[:160])}{'…' if len(note) > 160 else ''}")

    if ev.meeting_url:
        lines.append(f'💻 <a href="{esc(ev.meeting_url)}">线上会议链接</a>')
    if ev.url:
        lines.append(f'🔗 <a href="{esc(ev.url)}">查看详情 / 报名</a>')

    return "\n".join(lines)


def day_label(day: dt.date, today: dt.date) -> str:
    """相对今天的口语化日期标签。"""
    delta = (day - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    return fmt_date(day)


def render_daily_report(
    events: list[Event],
    *,
    days_ahead: int,
    today: dt.date,
    offset_days: int = 0,
    source: str | None = None,
) -> str:
    """每日播报。

    offset_days=1 & days_ahead=0 → 「明日活动」（晚上预告次日）。
    days_ahead>0 时按日期分组排版。
    """
    first_day = today + dt.timedelta(days=offset_days)
    label = day_label(first_day, today)

    if days_ahead == 0:
        scope = f"{label}的活动"
    else:
        scope = f"{label}起 {days_ahead + 1} 天的活动"
    header = f"📅 <b>4Seas {scope}</b> · {fmt_date(first_day)}"

    if not events:
        return (
            f"{header}\n\n"
            f"{label}没有安排活动 ☕\n\n"
            '想办一场?到 <a href="https://app.sola.day/event/4seas">Social Layer</a> 发布,'
            "下一次播报就会自动带上。"
        )

    blocks = [header, ""]

    if days_ahead == 0:
        for i, ev in enumerate(events, 1):
            blocks.append(render_event(ev, i))
            blocks.append("")
    else:
        current: dt.date | None = None
        for ev in events:
            day = max(ev.local_start.date(), first_day)  # 跨天活动归到窗口首日
            if day != current:
                current = day
                blocks.append(f"───── <b>{day_label(day, today)}</b> ─────")
                blocks.append("")
            blocks.append(render_event(ev))
            blocks.append("")

    footer = f"共 {len(events)} 场"
    if source and source != "sola_api":
        footer += f" · 数据源 {source}"
    footer += ' · 来自 <a href="https://app.sola.day/event/4seas">Social Layer</a>'
    blocks.append(footer)

    return _truncate("\n".join(blocks))


def _truncate(text: str) -> str:
    """超长时按行截断,避免把 HTML 标签劈成两半导致整条消息发送失败。"""
    if len(text) <= MAX_LEN:
        return text
    tail = "\n\n… 活动较多,完整列表见 https://app.sola.day/event/4seas"
    budget = MAX_LEN - len(tail)
    kept: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > budget:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + tail
