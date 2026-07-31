"""Render Event objects into Telegram messages.

HTML parse mode, not MarkdownV2: MarkdownV2 requires escaping `_*[]()~>#+-=|{}.!`
and real event titles are full of those characters (mixed EN/TH/ZH, `&`, `#`).
Miss one escape and the whole message fails to send.
"""

from __future__ import annotations

import datetime as dt
import html

from .models import Event

MAX_LEN = 4096  # Telegram's per-message limit
SOLA_URL = "https://app.sola.day/event/4seas"


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def fmt_date(d: dt.date) -> str:
    """e.g. 'Fri, Jul 31'"""
    return d.strftime("%a, %b %-d")


def fmt_time_range(ev: Event) -> str:
    if ev.is_all_day:
        return "🕘 All day"
    start = ev.local_start
    end = ev.local_end
    if end is None:
        return f"🕘 {start:%H:%M}"
    if end.date() != start.date():  # spans midnight
        return f"🕘 {start:%H:%M} → {end:%b %-d, %H:%M}"
    return f"🕘 {start:%H:%M}–{end:%H:%M}"


def day_label(day: dt.date, today: dt.date) -> str:
    """Colloquial label relative to today."""
    delta = (day - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return fmt_date(day)


TITLE_MAX = 58


def _short_title(title: str) -> str:
    """Sola titles run long ('Language Corner  Sa-Wat-Dee Thai Learn Basic Thai
    Together'). One long title wrapping to three lines ruins a compact list."""
    clean = " ".join(title.split())
    if len(clean) <= TITLE_MAX:
        return clean
    return clean[: TITLE_MAX - 1].rstrip() + "…"


def render_event_line(ev: Event) -> str:
    """One line per event: when, and a linked title. Nothing else.

    Deliberately sparse — a digest that lists address, host, headcount and tags for
    every event reads as a wall of text, and people stop opening it. Anyone who
    wants the detail taps through.
    """
    when = "All day" if ev.is_all_day else f"{ev.local_start:%H:%M}"
    title = esc(_short_title(ev.title))
    linked = f'<a href="{esc(ev.url)}">{title}</a>' if ev.url else f"<b>{title}</b>"
    return f"<code>{when:>7}</code>  {linked}"


def render_event(ev: Event, index: int | None = None) -> str:
    """One event as a detailed card."""
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
        meta.append(f"👥 {seats} going")
    if ev.require_approval:
        meta.append("🔒 Approval required")
    if meta:
        lines.append(" · ".join(meta))

    if ev.tags:
        lines.append("🏷 " + esc(" · ".join(ev.tags[:5])))

    if ev.notes:
        note = " ".join(ev.notes.split())
        if note:
            lines.append(f"📝 {esc(note[:160])}{'…' if len(note) > 160 else ''}")

    if ev.meeting_url:
        lines.append(f'💻 <a href="{esc(ev.meeting_url)}">Join online</a>')
    if ev.url:
        lines.append(f'🔗 <a href="{esc(ev.url)}">Details / RSVP</a>')

    return "\n".join(lines)


def render_daily_report(
    events: list[Event],
    *,
    days_ahead: int,
    today: dt.date,
    offset_days: int = 0,
    source: str | None = None,
    style: str = "compact",
) -> str:
    """The scheduled digest.

    offset_days=1 & days_ahead=0 → tomorrow's events (the 19:00 preview).
    days_ahead>0 groups events by date.

    style="compact" (default) is one line per event — the digest is a nudge to look,
    not a replacement for the event page. style="detailed" keeps the full cards.
    """
    first_day = today + dt.timedelta(days=offset_days)
    label = day_label(first_day, today)
    compact = style != "detailed"

    if days_ahead == 0:
        header = f"📅 <b>{label}</b> · {fmt_date(first_day)}"
    else:
        header = f"📅 <b>Next {days_ahead + 1} days</b> · from {fmt_date(first_day)}"

    if not events:
        lower = label.lower() if label in ("Today", "Tomorrow") else f"on {label}"
        return (
            f"{header}\n\n"
            f"Nothing scheduled {lower} ☕\n\n"
            f'Hosting something? Post it on <a href="{SOLA_URL}">Social Layer</a>.'
        )

    blocks = [header, ""]
    render_one = render_event_line if compact else render_event

    if days_ahead == 0:
        for i, ev in enumerate(events, 1):
            blocks.append(render_one(ev) if compact else render_event(ev, i))
            if not compact:
                blocks.append("")
    else:
        current: dt.date | None = None
        for ev in events:
            day = max(ev.local_start.date(), first_day)  # multi-day events pin to window start
            if day != current:
                if current is not None:
                    blocks.append("")
                current = day
                blocks.append(f"<b>{day_label(day, today)}</b>")
            blocks.append(render_one(ev))
            if not compact:
                blocks.append("")

    plural = "event" if len(events) == 1 else "events"
    footer = f"\n{len(events)} {plural} · <a href=\"{SOLA_URL}\">details &amp; RSVP</a>"
    if source and source != "sola_api":
        footer += f" · source: {source}"
    blocks.append(footer)

    return _truncate("\n".join(blocks))


def _truncate(text: str) -> str:
    """Truncate on line boundaries so HTML tags never get split in half."""
    if len(text) <= MAX_LEN:
        return text
    tail = f"\n\n… more events than fit here — see {SOLA_URL}"
    budget = MAX_LEN - len(tail)
    kept: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > budget:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + tail
