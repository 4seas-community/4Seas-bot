import datetime as dt
from zoneinfo import ZoneInfo

from bot.models import Event
from bot.services.events import SolaApiSource, day_window

BKK = ZoneInfo("Asia/Bangkok")

# 真实 API 返回的字段结构（截取自 api.sola.day 2026-07-30 的响应）
SAMPLE = {
    "id": "3s2y2qz7nh3zn",
    "title": "Language Corner",
    "start_time": "2026-07-29T17:00:00Z",
    "end_time": "2026-07-30T16:59:59Z",
    "timezone": "Asia/Bangkok",
    "place": {"title": "Zuzalu Library", "address": "2nd floor"},
    "owner": {"name": "zuzalu", "nickname": "Zuzulu library Chiangmai"},
    "participant_count": 3,
    "max_participant": None,
    "tags": ["Zuzalu"],
    "meeting_url": "",
    "notes": "",
    "require_approval": False,
}


def test_parses_real_api_shape():
    ev = SolaApiSource()._to_event(SAMPLE)
    assert ev is not None
    assert ev.title == "Language Corner"
    assert ev.host == "Zuzulu library Chiangmai"
    assert ev.participants == 3
    assert ev.tags == ["Zuzalu"]
    assert ev.meeting_url is None  # 空串归一成 None
    assert ev.url == "https://app.sola.day/event/detail/3s2y2qz7nh3zn"
    # UTC 17:00 → 曼谷次日 00:00，是一场全天活动
    assert ev.local_start.hour == 0
    assert ev.is_all_day


def test_missing_start_time_is_skipped_not_raised():
    assert SolaApiSource()._to_event({"id": "x", "title": "坏数据"}) is None


def test_unknown_fields_do_not_break_parsing():
    """Sola 没有 API 文档，契约随时可能加字段 —— 加字段不能让解析挂掉。"""
    ev = SolaApiSource()._to_event({**SAMPLE, "brand_new_field": {"a": 1}})
    assert ev is not None and ev.title == "Language Corner"


def test_null_place_and_owner():
    ev = SolaApiSource()._to_event({**SAMPLE, "place": None, "owner": None})
    assert ev is not None
    assert ev.place_title is None and ev.host is None


def test_day_window_today_only():
    now = dt.datetime(2026, 7, 30, 17, 30, tzinfo=BKK)
    start, end = day_window(0, BKK, now=now)
    assert start == dt.datetime(2026, 7, 30, 0, 0, tzinfo=BKK)
    assert end.date() == dt.date(2026, 7, 30) and end.hour == 23


def test_day_window_three_days_ahead():
    now = dt.datetime(2026, 7, 30, 17, 30, tzinfo=BKK)
    start, end = day_window(3, BKK, now=now)
    assert start.date() == dt.date(2026, 7, 30)
    assert end.date() == dt.date(2026, 8, 2)


def test_ongoing_multiday_event_counts_as_today():
    """跨天活动即使昨天开始，只要今天还在进行就该出现在今日播报里。"""
    start, end = day_window(0, BKK, now=dt.datetime(2026, 7, 30, 10, 0, tzinfo=BKK))
    ev = Event(
        id="m",
        title="Residency Week",
        start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
        end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK),
    )
    assert ev.overlaps(start, end)


def test_tomorrow_event_excluded_from_today_window():
    start, end = day_window(0, BKK, now=dt.datetime(2026, 7, 30, 10, 0, tzinfo=BKK))
    ev = Event(
        id="t",
        title="明天的",
        start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 31, 13, 0, tzinfo=BKK),
    )
    assert not ev.overlaps(start, end)


# ── 起始偏移（晚上预告次日）────────────────────────────────────────────


def test_window_offset_one_is_tomorrow_only():
    """每晚 19:00 播明天：窗口必须整个落在明天，不含今天。"""
    now = dt.datetime(2026, 7, 30, 19, 0, tzinfo=BKK)
    start, end = day_window(0, BKK, now=now, offset_days=1)
    assert start == dt.datetime(2026, 7, 31, 0, 0, tzinfo=BKK)
    assert end.date() == dt.date(2026, 7, 31) and end.hour == 23


def test_todays_remaining_event_excluded_from_tomorrow_preview():
    """19:00 播报时，今晚 20:00 那场不该出现在'明天'的预告里。"""
    start, end = day_window(0, BKK, now=dt.datetime(2026, 7, 30, 19, 0, tzinfo=BKK), offset_days=1)
    tonight = Event(
        id="tonight",
        title="今晚 20:00",
        start=dt.datetime(2026, 7, 30, 20, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 30, 22, 0, tzinfo=BKK),
    )
    assert not tonight.overlaps(start, end)


def test_tomorrow_event_included_in_tomorrow_preview():
    start, end = day_window(0, BKK, now=dt.datetime(2026, 7, 30, 19, 0, tzinfo=BKK), offset_days=1)
    tomorrow = Event(
        id="tmr",
        title="明天 11:00",
        start=dt.datetime(2026, 7, 31, 11, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, 31, 13, 0, tzinfo=BKK),
    )
    assert tomorrow.overlaps(start, end)


def test_multiday_event_spanning_tomorrow_is_included():
    """7-28 开始、8-03 结束的活动，明天仍在进行，预告里要有。"""
    start, end = day_window(0, BKK, now=dt.datetime(2026, 7, 30, 19, 0, tzinfo=BKK), offset_days=1)
    week = Event(
        id="week",
        title="Residency Week",
        start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
        end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK),
    )
    assert week.overlaps(start, end)


def test_offset_zero_keeps_old_behaviour():
    """默认 offset=0 时行为不变 —— /events 仍然从今天算起。"""
    now = dt.datetime(2026, 7, 30, 19, 0, tzinfo=BKK)
    assert day_window(0, BKK, now=now) == day_window(0, BKK, now=now, offset_days=0)
