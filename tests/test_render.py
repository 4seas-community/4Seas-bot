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
    assert "没有安排活动" in out
    assert "Social Layer" in out


def test_single_day_report_numbers_events():
    out = render_daily_report(
        [make_event(), make_event(id="e2", title="Second")],
        days_ahead=0,
        today=dt.date(2026, 7, 30),
    )
    assert "今日活动" in out
    assert "<b>1." in out and "<b>2." in out
    assert "共 2 场" in out


def test_multi_day_report_groups_by_date():
    evs = [
        make_event(),
        make_event(id="e2", title="Next day", start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK), end=None),
    ]
    out = render_daily_report(evs, days_ahead=3, today=dt.date(2026, 7, 30))
    assert "近 4 天活动" in out
    assert "今天" in out
    assert "7月31日 周五" in out


def test_long_report_truncated_without_breaking_tags():
    evs = [make_event(id=f"e{i}", title=f"活动 {i} " + "长" * 80) for i in range(60)]
    out = render_daily_report(evs, days_ahead=0, today=dt.date(2026, 7, 30))
    assert len(out) <= MAX_LEN
    assert out.count("<b>") == out.count("</b>")
    assert "完整列表见" in out
