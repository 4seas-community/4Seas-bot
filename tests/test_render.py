import datetime as dt
from zoneinfo import ZoneInfo

from bot.models import Event
from bot.render import (
    MAX_LEN, fmt_time_range, render_daily_report, render_event, render_event_line,
)

BKK = ZoneInfo("Asia/Bangkok")


def make_event(**kw) -> Event:
    base = dict(
        id="e1",
        title="Build your AI Co-Founder",
        start=dt.datetime(2026, 7, 30, 18, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 30, 20, 0, tzinfo=BKK),
        tz="Asia/Bangkok",
    )
    base.update(kw)
    return Event(**base)  # type: ignore[arg-type]


def test_all_day_detected():
    ev = make_event(
        start=dt.datetime(2026, 7, 30, 0, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 30, 23, 59, 59, tzinfo=BKK),
    )
    assert ev.is_all_day
    assert fmt_time_range(ev) == "🕘 All day"


def test_timed_event_range():
    assert fmt_time_range(make_event()) == "🕘 18:00–20:00"


def test_cross_day_event_shows_end_date():
    ev = make_event(end=dt.datetime(2026, 7, 31, 2, 0, tzinfo=BKK))
    assert "Jul 31, 02:00" in fmt_time_range(ev)


def test_title_is_html_escaped():
    ev = make_event(title="Rust & C++ <hack> night")
    out = render_event(ev)
    assert "&amp;" in out and "&lt;hack&gt;" in out
    assert "<hack>" not in out


def test_event_card_includes_details():
    ev = make_event(
        place_title="Zuzalu Library",
        place_address="2nd floor",
        host="karlen",
        participants=3,
        max_participants=20,
        tags=["AI", "Technology"],
        require_approval=True,
        url="https://app.sola.day/event/detail/e1",
    )
    out = render_event(ev, 1)
    assert "Zuzalu Library · 2nd floor" in out
    assert "karlen" in out
    assert "3/20 going" in out
    assert "Approval required" in out
    assert "AI · Technology" in out
    assert "Details / RSVP" in out


def test_empty_report_still_useful():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30))
    assert "Nothing scheduled today" in out
    assert "Social Layer" in out


def test_single_day_report_numbers_events():
    out = render_daily_report(
        [make_event(), make_event(id="e2", title="Second")],
        days_ahead=0,
        today=dt.date(2026, 7, 30),
        style="detailed",
    )
    assert "<b>Today</b>" in out
    assert "<b>1." in out and "<b>2." in out
    assert "2 events" in out


def test_multi_day_report_groups_by_date():
    evs = [
        make_event(),
        make_event(id="e2", title="Next day", start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK), end=None),
    ]
    out = render_daily_report(evs, days_ahead=3, today=dt.date(2026, 7, 30))
    assert "Next 4 days" in out
    assert "<b>Today</b>" in out
    assert "<b>Tomorrow</b>" in out


# ── 晚上 19:00 预告次日（offset_days=1）─────────────────────────────────


def test_tomorrow_preview_header_says_tomorrow():
    """The 19:00 digest covers tomorrow — the header date must be tomorrow, not today."""
    ev = make_event(
        start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 31, 13, 0, tzinfo=BKK),
    )
    out = render_daily_report([ev], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=1)
    assert "<b>Tomorrow</b>" in out
    assert "Fri, Jul 31" in out
    assert "Jul 30" not in out


def test_tomorrow_preview_empty_message_says_tomorrow():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=1)
    assert "Nothing scheduled tomorrow" in out
    assert "scheduled today" not in out


def test_offset_two_falls_back_to_a_date():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=2)
    assert "Nothing scheduled on Sat, Aug 1" in out


def test_offset_five_falls_back_to_date():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=5)
    assert "Tue, Aug 4" in out


def test_tomorrow_plus_range_groups_from_tomorrow():
    evs = [
        make_event(start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK), end=None),
        make_event(id="e2", title="后天的", start=dt.datetime(2026, 8, 1, 9, 0, tzinfo=BKK), end=None),
    ]
    out = render_daily_report(evs, days_ahead=1, today=dt.date(2026, 7, 30), offset_days=1)
    assert "Next 2 days" in out
    assert "<b>Tomorrow</b>" in out and "<b>Sat, Aug 1</b>" in out


def test_ongoing_event_grouped_under_window_start_not_its_own_start():
    """A multi-day event starting Jul 28 must group under "Tomorrow", not show Jul 28."""
    ev = make_event(
        title="Residency Week",
        start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
        end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK),
    )
    out = render_daily_report([ev], days_ahead=2, today=dt.date(2026, 7, 30), offset_days=1)
    assert "<b>Tomorrow</b>" in out
    assert "Jul 28" not in out


def test_long_report_truncated_without_breaking_tags():
    evs = [make_event(id=f"e{i}", title=f"活动 {i} " + "长" * 80) for i in range(60)]
    out = render_daily_report(evs, days_ahead=0, today=dt.date(2026, 7, 30))
    assert len(out) <= MAX_LEN
    assert out.count("<b>") == out.count("</b>")
    assert "more events than fit here" in out


# ── compact digest (default) ──────────────────────────────────────────


def test_compact_is_one_line_per_event():
    """The digest is a nudge to look, not a replacement for the event page."""
    out = render_daily_report(
        [make_event(), make_event(id="e2", title="Second", start=dt.datetime(2026, 7, 30, 20, 0, tzinfo=BKK))],
        days_ahead=0, today=dt.date(2026, 7, 30),
    )
    body = [l for l in out.split("\n") if l.strip() and not l.startswith("📅") and "events ·" not in l]
    assert len(body) == 2


def test_compact_omits_address_host_and_tags():
    ev = make_event(
        place_address="2 20 Nimmana Haeminda Rd Lane 15, Tambon Su Thep, Chiang Mai 50200",
        host="karlen", participants=3, tags=["AI", "Web3"],
    )
    out = render_daily_report([ev], days_ahead=0, today=dt.date(2026, 7, 30))
    assert "Nimmana" not in out
    assert "karlen" not in out
    assert "going" not in out
    assert "Web3" not in out


def test_compact_title_links_to_the_event():
    ev = make_event(url="https://app.sola.day/event/detail/e1")
    line = render_event_line(ev)
    assert 'href="https://app.sola.day/event/detail/e1"' in line
    assert "Build your AI Co-Founder" in line


def test_compact_falls_back_to_bold_without_url():
    assert "<b>" in render_event_line(make_event(url=None))


def test_long_titles_truncated():
    """Sola titles run long; one wrapping to three lines ruins a compact list."""
    ev = make_event(title="Language Corner " + "very long title " * 10)
    line = render_event_line(ev)
    assert "…" in line and len(line) < 260


def test_all_day_shown_as_all_day():
    ev = make_event(
        start=dt.datetime(2026, 7, 30, 0, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 30, 23, 59, 59, tzinfo=BKK),
    )
    assert "All day" in render_event_line(ev)


def test_compact_escapes_titles():
    assert "&amp;" in render_event_line(make_event(title="Rust & C++"))


def test_detailed_style_still_available():
    ev = make_event(host="karlen", participants=3)
    out = render_daily_report([ev], days_ahead=0, today=dt.date(2026, 7, 30), style="detailed")
    assert "karlen" in out and "3 going" in out


def test_compact_is_much_shorter_than_detailed():
    """Measured in lines, not characters — 'wall of text' is a vertical problem,
    and the fixed header/footer dilute a character-count ratio."""
    evs = [make_event(id=f"e{i}", place_address="Somewhere long in Nimman", host="host",
                      participants=5, tags=["A", "B"]) for i in range(5)]
    kw = dict(days_ahead=0, today=dt.date(2026, 7, 30))
    compact = render_daily_report(evs, **kw).count("\n")
    detailed = render_daily_report(evs, style="detailed", **kw).count("\n")
    assert compact < detailed / 3, f"compact {compact} lines vs detailed {detailed}"
