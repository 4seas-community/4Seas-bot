import datetime as dt
from zoneinfo import ZoneInfo

from bot.models import Event
from bot.render import MAX_LEN, fmt_time_range, render_daily_report, render_event

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
    assert fmt_time_range(ev) == "🕘 全天"


def test_timed_event_range():
    assert fmt_time_range(make_event()) == "🕘 18:00–20:00"


def test_cross_day_event_shows_end_date():
    ev = make_event(end=dt.datetime(2026, 7, 31, 2, 0, tzinfo=BKK))
    assert "07-31 02:00" in fmt_time_range(ev)


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
    assert "3/20 人已报名" in out
    assert "需审核" in out
    assert "AI · Technology" in out
    assert "查看详情" in out


def test_empty_report_still_useful():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30))
    assert "今天没有安排活动" in out
    assert "Social Layer" in out


def test_single_day_report_numbers_events():
    out = render_daily_report(
        [make_event(), make_event(id="e2", title="Second")],
        days_ahead=0,
        today=dt.date(2026, 7, 30),
    )
    assert "今天的活动" in out
    assert "<b>1." in out and "<b>2." in out
    assert "共 2 场" in out


def test_multi_day_report_groups_by_date():
    evs = [
        make_event(),
        make_event(id="e2", title="Next day", start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK), end=None),
    ]
    out = render_daily_report(evs, days_ahead=3, today=dt.date(2026, 7, 30))
    assert "今天起 4 天的活动" in out
    assert "<b>今天</b>" in out
    assert "<b>明天</b>" in out


# ── 晚上 19:00 预告次日（offset_days=1）─────────────────────────────────


def test_tomorrow_preview_header_says_tomorrow():
    """每晚 19:00 播的是明天，标题日期必须是明天而不是今天。"""
    ev = make_event(
        start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 31, 13, 0, tzinfo=BKK),
    )
    out = render_daily_report([ev], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=1)
    assert "明天的活动" in out
    assert "7月31日 周五" in out
    assert "7月30日" not in out


def test_tomorrow_preview_empty_message_says_tomorrow():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=1)
    assert "明天没有安排活动" in out
    assert "今天没有" not in out


def test_offset_two_says_the_day_after_tomorrow():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=2)
    assert "后天没有安排活动" in out


def test_offset_beyond_three_days_falls_back_to_date():
    out = render_daily_report([], days_ahead=0, today=dt.date(2026, 7, 30), offset_days=5)
    assert "8月4日 周二" in out


def test_tomorrow_plus_range_groups_from_tomorrow():
    evs = [
        make_event(start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK), end=None),
        make_event(id="e2", title="后天的", start=dt.datetime(2026, 8, 1, 9, 0, tzinfo=BKK), end=None),
    ]
    out = render_daily_report(evs, days_ahead=1, today=dt.date(2026, 7, 30), offset_days=1)
    assert "明天起 2 天的活动" in out
    assert "<b>明天</b>" in out and "<b>后天</b>" in out


def test_ongoing_event_grouped_under_window_start_not_its_own_start():
    """7-28 就开始的跨天活动，出现在"明天"的播报里时应归到明天，而不是显示 7-28。"""
    ev = make_event(
        title="Residency Week",
        start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
        end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK),
    )
    out = render_daily_report([ev], days_ahead=2, today=dt.date(2026, 7, 30), offset_days=1)
    assert "<b>明天</b>" in out
    assert "7月28日" not in out


def test_long_report_truncated_without_breaking_tags():
    evs = [make_event(id=f"e{i}", title=f"活动 {i} " + "长" * 80) for i in range(60)]
    out = render_daily_report(evs, days_ahead=0, today=dt.date(2026, 7, 30))
    assert len(out) <= MAX_LEN
    assert out.count("<b>") == out.count("</b>")
    assert "完整列表见" in out
