"""同步的幂等性 —— 这是最核心的不变量，测试写得比别处厚。"""

import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from bot.models import Event
from bot.storage import Storage

BKK = ZoneInfo("Asia/Bangkok")
SRC = "sola_api"


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "test.sqlite3")
    yield s
    s.close()


def ev(eid="e1", title="活动 A", day=30, hour=18, **kw) -> Event:
    base = dict(
        id=eid,
        title=title,
        start=dt.datetime(2026, 7, day, hour, 0, tzinfo=BKK),
        end=dt.datetime(2026, 7, day, hour + 2, 0, tzinfo=BKK),
        source=SRC,
    )
    base.update(kw)
    return Event(**base)  # type: ignore[arg-type]


def window(d1=29, d2=31):
    return (
        dt.datetime(2026, 7, d1, 0, 0, tzinfo=BKK),
        dt.datetime(2026, 7, d2, 23, 59, 59, tzinfo=BKK),
    )


# ── 幂等 ──────────────────────────────────────────────────────────────


def test_repeated_sync_never_duplicates(store):
    events = [ev("e1"), ev("e2", "活动 B", hour=10)]
    first = store.upsert_events(events, source=SRC)
    assert (first.inserted, first.updated, first.unchanged) == (2, 0, 0)

    for _ in range(9):
        again = store.upsert_events(events, source=SRC)
        assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)

    assert store.event_stats() == {"total": 2, "live": 2}


def test_same_id_different_source_coexist(store):
    """主键是 (source, event_id)，不同源的同名 id 不该互相覆盖。"""
    store.upsert_events([ev("shared")], source="sola_api")
    store.upsert_events([replace(ev("shared"), source="local_yaml")], source="local_yaml")
    assert store.event_stats()["total"] == 2


def test_content_change_bumps_updated_at_but_not_row_count(store):
    store.upsert_events([ev("e1", "旧标题")], source=SRC)
    before = store.query_events(*window())[0]

    result = store.upsert_events([ev("e1", "新标题")], source=SRC)
    assert (result.inserted, result.updated) == (0, 1)

    after = store.query_events(*window())
    assert len(after) == 1
    assert after[0].title == "新标题"
    assert before.title == "旧标题"


def test_participant_count_change_is_not_a_content_change(store):
    """报名人数天天变。算进 content_hash 会让每次同步都判定为'改过'。"""
    store.upsert_events([ev("e1", participants=3)], source=SRC)
    result = store.upsert_events([ev("e1", participants=17)], source=SRC)
    assert (result.updated, result.unchanged) == (0, 1)
    # 但新的人数仍然要落库
    assert store.query_events(*window())[0].participants == 17


def test_empty_sync_does_not_wipe_the_table(store):
    """上游返回空不该被当成'全部取消'——除非带窗口显式对账。"""
    store.upsert_events([ev("e1"), ev("e2", hour=10)], source=SRC)
    store.upsert_events([], source=SRC)
    assert store.event_stats()["live"] == 2


# ── 软删除 ────────────────────────────────────────────────────────────


def test_event_missing_from_window_is_soft_deleted(store):
    now1 = dt.datetime(2026, 7, 20, 8, 0, tzinfo=dt.UTC)
    store.upsert_events([ev("e1"), ev("e2", hour=10)], source=SRC, window=window(), now=now1)

    now2 = now1 + dt.timedelta(days=1)
    result = store.upsert_events([ev("e1")], source=SRC, window=window(), now=now2)

    assert result.removed == 1
    assert store.event_stats() == {"total": 2, "live": 1}
    assert [e.id for e in store.query_events(*window())] == ["e1"]


def test_soft_deleted_event_revives_when_it_comes_back(store):
    now1 = dt.datetime(2026, 7, 20, 8, 0, tzinfo=dt.UTC)
    store.upsert_events([ev("e1"), ev("e2", hour=10)], source=SRC, window=window(), now=now1)
    store.upsert_events([ev("e1")], source=SRC, window=window(), now=now1 + dt.timedelta(days=1))
    assert store.event_stats()["live"] == 1

    store.upsert_events(
        [ev("e1"), ev("e2", hour=10)], source=SRC, window=window(), now=now1 + dt.timedelta(days=2)
    )
    assert store.event_stats()["live"] == 2


def test_events_outside_window_are_untouched(store):
    """只对账窗口内的数据，窗口外的历史不能被误删。"""
    now1 = dt.datetime(2026, 7, 20, 8, 0, tzinfo=dt.UTC)
    store.upsert_events([ev("old", day=1), ev("e1")], source=SRC, now=now1)

    store.upsert_events([ev("e1")], source=SRC, window=window(29, 31), now=now1 + dt.timedelta(days=1))
    assert store.event_stats()["live"] == 2  # 7-01 那条在窗口外，保住了


# ── 查询 ──────────────────────────────────────────────────────────────


def test_query_returns_sorted_by_start(store):
    store.upsert_events([ev("late", hour=20), ev("early", hour=9)], source=SRC)
    assert [e.id for e in store.query_events(*window())] == ["early", "late"]


def test_ongoing_multiday_event_shows_up_today(store):
    """7-28 开始、8-03 结束的活动，在 7-30 当天的窗口里必须出现。"""
    store.upsert_events(
        [
            Event(
                id="week",
                title="Residency Week",
                start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
                end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK),
                source=SRC,
            )
        ],
        source=SRC,
    )
    today = (
        dt.datetime(2026, 7, 30, 0, 0, tzinfo=BKK),
        dt.datetime(2026, 7, 30, 23, 59, 59, tzinfo=BKK),
    )
    assert [e.id for e in store.query_events(*today)] == ["week"]


def test_roundtrip_preserves_all_fields(store):
    original = ev(
        "full",
        place_title="Zuzalu Library",
        place_address="2nd floor",
        host="karlen",
        participants=3,
        max_participants=20,
        tags=["AI", "Web3"],
        meeting_url="https://meet.example/x",
        notes="带上笔记本",
        require_approval=True,
        url="https://app.sola.day/event/detail/full",
    )
    store.upsert_events([original], source=SRC)
    got = store.query_events(*window())[0]

    assert got.title == original.title
    assert got.start == original.start and got.end == original.end
    assert got.place_title == "Zuzalu Library" and got.place_address == "2nd floor"
    assert got.host == "karlen"
    assert (got.participants, got.max_participants) == (3, 20)
    assert got.tags == ["AI", "Web3"]
    assert got.meeting_url == "https://meet.example/x"
    assert got.notes == "带上笔记本"
    assert got.require_approval is True
    assert got.url == original.url


def test_report_dedupe_is_idempotent(store):
    day = dt.date(2026, 7, 30)
    assert store.mark_reported(-100, day) is True
    assert store.mark_reported(-100, day) is False
    assert store.already_reported(-100, day) is True
    assert store.already_reported(-100, dt.date(2026, 7, 31)) is False


def test_keyword_cooldown(store):
    now = 1_000_000.0
    assert store.try_fire_keyword(-100, "visa", 3600, now) is True
    assert store.try_fire_keyword(-100, "visa", 3600, now + 10) is False
    assert store.try_fire_keyword(-100, "visa", 3600, now + 3601) is True
    # 不同群互不影响
    assert store.try_fire_keyword(-200, "visa", 3600, now + 10) is True
