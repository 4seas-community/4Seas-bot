"""Regressions for the issues Codex found in PR #1.

Each test names the production failure it prevents. They exist because every one of
these ships a wrong message to a 776-member group or stops the bot starting.
"""

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from bot.models import Event
from bot.render import fmt_span, render_editorial
from bot.services.digest_writer import INVITE_ANGLE, choose_angles
from bot.storage import Storage

BKK = ZoneInfo("Asia/Bangkok")


def ev(eid="e1", *, venue=None, content=None, start=None, end=None, title="T"):
    return Event(
        id=eid, title=title,
        start=start or dt.datetime(2026, 8, 1, 10, 0, tzinfo=BKK),
        end=end if end is not None else dt.datetime(2026, 8, 1, 12, 0, tzinfo=BKK),
        tz="Asia/Bangkok", venue_name=venue, content=content, source="sola_api",
    )


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "t.sqlite3")
    yield s
    s.close()


def window():
    return (dt.datetime(2026, 8, 1, 0, 0, tzinfo=BKK),
            dt.datetime(2026, 8, 1, 23, 59, 59, tzinfo=BKK))


# ── HIGH: transient detail failure must not erase enrichment ──────────


def test_failed_enrichment_does_not_erase_stored_venue_and_content(store):
    """One detail-endpoint blip used to blank venue/content, so the nightly post
    went out as bare titles with no recommendation lines."""
    store.upsert_events([ev(venue="Zuzalu Library", content="Full description.")], source="sola_api")
    store.upsert_events([ev(venue=None, content=None)], source="sola_api")

    got = store.query_events(*window())[0]
    assert got.venue_name == "Zuzalu Library"
    assert got.content == "Full description."


def test_enrichment_flapping_is_not_counted_as_a_change(store):
    """Restoring the old values in SQL alone would keep the hash computed from
    None — so every sync would still report 'updated' and updated_at would churn."""
    rich = ev(venue="Library", content="Description.")
    store.upsert_events([rich], source="sola_api")

    blip = store.upsert_events([ev(venue=None, content=None)], source="sola_api")
    assert (blip.updated, blip.unchanged) == (0, 1)

    recovered = store.upsert_events([ev(venue="Library", content="Description.")], source="sola_api")
    assert (recovered.updated, recovered.unchanged) == (0, 1)


def test_a_real_venue_change_is_still_detected(store):
    """The guard must not be so eager that genuine edits stop registering."""
    store.upsert_events([ev(venue="Old Room", content="D.")], source="sola_api")
    result = store.upsert_events([ev(venue="New Room", content="D.")], source="sola_api")
    assert result.updated == 1
    assert store.query_events(*window())[0].venue_name == "New Room"


# ── HIGH: migration must not stop the bot booting ─────────────────────


class _FlakyConn:
    """Delegating proxy — sqlite3.Connection is an immutable C type, so the only
    way to inject a failure is to hand Storage a stand-in."""

    def __init__(self, real, fail_on, error):
        self._real, self._fail_on, self._error = real, fail_on, error
        self.fired = 0

    def execute(self, sql, *args):
        if self._fail_on in str(sql) and self.fired == 0:
            self.fired += 1
            raise self._error
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name in ("_real", "_fail_on", "_error", "fired"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


def _old_schema_db(tmp_path, name):
    """A database from before venue_name/content existed."""
    import sqlite3
    path = tmp_path / name
    Storage(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE events DROP COLUMN venue_name")
        conn.execute("ALTER TABLE events DROP COLUMN content")
    return path


def _patch_connect(monkeypatch, error):
    import sqlite3
    import bot.storage as storage_mod
    holder = {}
    real_connect = sqlite3.connect

    def fake_connect(*a, **kw):
        holder["conn"] = _FlakyConn(real_connect(*a, **kw), "ADD COLUMN", error)
        return holder["conn"]

    monkeypatch.setattr(storage_mod.sqlite3, "connect", fake_connect)
    return holder


def test_migration_tolerates_a_column_added_concurrently(tmp_path, monkeypatch):
    """Two processes overlapping on restart both pass the PRAGMA check; the loser
    used to raise 'duplicate column' out of Storage() at import time and the bot
    never came up."""
    import sqlite3
    path = _old_schema_db(tmp_path, "race.sqlite3")
    holder = _patch_connect(
        monkeypatch, sqlite3.OperationalError("duplicate column name: venue_name")
    )

    Storage(path).close()  # must not raise

    assert holder["conn"].fired == 1
    monkeypatch.undo()
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert "content" in cols  # the second column still got added


def test_migration_still_raises_on_a_real_error(tmp_path, monkeypatch):
    """Swallowing every OperationalError would hide genuine corruption."""
    import sqlite3
    path = _old_schema_db(tmp_path, "bad.sqlite3")
    _patch_connect(monkeypatch, sqlite3.OperationalError("database disk image is malformed"))

    with pytest.raises(sqlite3.OperationalError, match="malformed"):
        Storage(path)


# ── HIGH: malformed LLM output must not abort the digest ──────────────


@pytest.mark.parametrize("payload", [
    '[]',                                             # top level is a list
    '"just a string"',
    '{"opening":"x","closing":"y","items":1}',        # items is not a list
    '{"opening":"x","closing":"y","items":"nope"}',
    '{"opening":"","closing":"y","items":[]}',        # empty opening
    '{"items":[]}',                                   # no opening/closing at all
    'not json at all',
])
async def test_malformed_llm_output_falls_back_instead_of_crashing(payload, monkeypatch):
    """Shape validation used to sit outside the try, so valid-JSON-wrong-shape
    escaped the provider loop and took the whole 19:00 job down."""
    from bot.services import digest_writer as dw

    class FakeCompletions:
        async def create(self, **kw):
            msg = type("M", (), {"content": payload})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    fake = type("P", (), {
        "name": "fake",
        "model": "m",
        "client": type("C", (), {"chat": type("Ch", (), {"completions": FakeCompletions()})()})(),
    })()
    monkeypatch.setattr(dw.llm_service, "providers", [fake])

    events = [ev(content="Bring a book and read quietly together for two hours.")]
    copy = await dw.digest_writer.write(
        events, target_date=dt.date(2026, 8, 1), recent=[], days_since_invite=None
    )
    assert copy.generated is False           # fell back rather than blew up
    assert copy.lines[events[0].id]           # organiser's own words still used


async def test_well_formed_llm_output_is_used(monkeypatch):
    """Guard against the validation being so strict nothing ever passes."""
    from bot.services import digest_writer as dw

    good = json.dumps({"opening": "A quiet morning.", "closing": "See you there.",
                       "items": [{"id": "e1", "line": "Read together for two hours."}]})

    class FakeCompletions:
        async def create(self, **kw):
            msg = type("M", (), {"content": good})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    fake = type("P", (), {
        "name": "fake", "model": "m",
        "client": type("C", (), {"chat": type("Ch", (), {"completions": FakeCompletions()})()})(),
    })()
    monkeypatch.setattr(dw.llm_service, "providers", [fake])

    copy = await dw.digest_writer.write(
        [ev()], target_date=dt.date(2026, 8, 1), recent=[], days_since_invite=None
    )
    assert copy.generated is True
    assert copy.opening == "A quiet morning."
    assert copy.lines["e1"] == "Read together for two hours."


# ── MEDIUM: first deploy must not open with a solicitation ────────────


def test_first_ever_digest_never_uses_the_invite():
    """'Come start your own event' as the very first thing a community bot says."""
    import random
    for seed in range(50):
        _, closing, used = choose_angles(
            dt.date(2026, 8, 1), [], None, random.Random(seed)
        )
        assert not used and closing != INVITE_ANGLE


# ── LOW: empty-day wording must follow the configured offset ──────────


def test_empty_digest_says_today_when_offset_is_zero():
    out = render_editorial([], target_date=dt.date(2026, 8, 1), today=dt.date(2026, 8, 1))
    assert "listed today" in out and "tomorrow" not in out


def test_empty_digest_says_tomorrow_when_offset_is_one():
    out = render_editorial([], target_date=dt.date(2026, 8, 1), today=dt.date(2026, 7, 31))
    assert "listed tomorrow" in out


def test_empty_digest_names_the_date_for_further_offsets():
    out = render_editorial([], target_date=dt.date(2026, 8, 1), today=dt.date(2026, 7, 30))
    assert "on Sat, Aug 1" in out
    assert "tomorrow" not in out


# ── LOW: carry-over events must not quote another day's start time ────


def test_multiday_event_does_not_show_a_start_time_from_another_day():
    """Residency Week began 09:00 on Jul 28. Under tomorrow's heading that 09:00
    reads as 'starts 9am tomorrow'."""
    week = ev("w", title="Residency Week",
              start=dt.datetime(2026, 7, 28, 9, 0, tzinfo=BKK),
              end=dt.datetime(2026, 8, 3, 18, 0, tzinfo=BKK))
    span = fmt_span(week, dt.date(2026, 8, 1))
    assert "09:00" not in span
    assert span.startswith("Ongoing")
    assert "Aug 3" in span


def test_carry_over_event_ending_today_shows_its_end_time():
    overnight = ev("o", start=dt.datetime(2026, 7, 31, 22, 0, tzinfo=BKK),
                   end=dt.datetime(2026, 8, 1, 2, 0, tzinfo=BKK))
    assert fmt_span(overnight, dt.date(2026, 8, 1)) == "Ongoing · until 02:00"


def test_same_day_events_are_unaffected():
    assert fmt_span(ev(), dt.date(2026, 8, 1)) == "10:00–12:00"


def test_span_without_target_date_keeps_old_behaviour():
    assert fmt_span(ev()) == "10:00–12:00"


# ── CRITICAL: never post "nothing scheduled" when we merely failed to read ──


class _FakeStorage:
    """Minimal stand-in so the branch can be exercised without a real DB."""

    def __init__(self, *, live: int, window_events: list):
        self._live = live
        self._window = window_events
        self.queries = 0

    def query_events(self, start, end):
        self.queries += 1
        return list(self._window)

    def event_stats(self):
        return {"live": self._live, "total": self._live}


async def test_cold_store_with_failing_sync_raises_instead_of_reporting_empty(monkeypatch):
    """The expensive failure: cold DB + sync unavailable used to return [], and the
    caller happily posted 'Nothing scheduled tomorrow' to 776 people, then marked
    the day reported so the real events never went out."""
    from bot.jobs import daily_report as dr

    fake = _FakeStorage(live=0, window_events=[])
    monkeypatch.setattr(dr, "storage", fake)

    async def failing_sync(*a, **kw):
        return False, "A sync is already running"

    monkeypatch.setattr(dr, "sync_events", failing_sync)

    with pytest.raises(dr.EventsUnavailable):
        await dr.load_events(0, offset_days=1)


async def test_populated_store_reports_a_genuinely_empty_day(monkeypatch):
    """The other half: when the store clearly has data, an empty window really is
    an empty day — and must not trigger a sync or an alert."""
    from bot.jobs import daily_report as dr

    fake = _FakeStorage(live=42, window_events=[])
    monkeypatch.setattr(dr, "storage", fake)

    async def must_not_run(*a, **kw):
        raise AssertionError("should not sync when the store already has events")

    monkeypatch.setattr(dr, "sync_events", must_not_run)

    assert await dr.load_events(0, offset_days=1) == []


async def test_cold_store_recovers_when_sync_succeeds(monkeypatch):
    from bot.jobs import daily_report as dr

    found = [ev()]
    fake = _FakeStorage(live=0, window_events=[])
    monkeypatch.setattr(dr, "storage", fake)

    async def good_sync(*a, **kw):
        fake._window = found
        return True, "ok"

    monkeypatch.setattr(dr, "sync_events", good_sync)

    assert await dr.load_events(0, offset_days=1) == found


# ── Waiting for an in-flight sync must be bounded ─────────────────────


async def test_sync_lock_wait_is_bounded(monkeypatch):
    """Waiting forever is the mirror image of the Critical bug: the holder can wedge
    on a path with no timeout (SQLite locked by another process), and the waiter then
    hangs silently — no digest, no failure, no alert."""
    import asyncio
    from bot.jobs import sync_events as se

    monkeypatch.setattr(se, "LOCK_WAIT_TIMEOUT", 0.05)

    await se._sync_lock.acquire()
    try:
        ok, detail = await se.sync_events()
    finally:
        se._sync_lock.release()

    assert ok is False
    assert "Timed out" in detail


async def test_sync_releases_the_lock_on_failure(monkeypatch):
    """A leaked lock would make every later sync wait out the full timeout and fail."""
    from bot.jobs import sync_events as se

    async def boom(*a, **kw):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(se.event_service, "fetch_upstream", boom)
    monkeypatch.setattr(se.storage, "log_sync", lambda *a, **kw: None)

    ok, _ = await se.sync_events()
    assert ok is False
    assert not se._sync_lock.locked(), "lock leaked after a failed sync"


async def test_sync_releases_the_lock_on_timeout(monkeypatch):
    import asyncio
    from bot.jobs import sync_events as se

    async def slow(*a, **kw):
        await asyncio.sleep(10)

    monkeypatch.setattr(se, "SYNC_TIMEOUT", 0.05)
    monkeypatch.setattr(se.event_service, "fetch_upstream", slow)
    monkeypatch.setattr(se.storage, "log_sync", lambda *a, **kw: None)

    ok, detail = await se.sync_events()
    assert ok is False and "timed out" in detail.lower()
    assert not se._sync_lock.locked(), "lock leaked after a timeout"


def test_upsert_does_not_mutate_the_caller_s_events(store):
    """Backfilling enrichment must not reach back into the caller's objects —
    an implicit contract like that is exactly what the next call site trips on."""
    store.upsert_events([ev(venue="Library", content="Description.")], source="sola_api")

    incoming = ev(venue=None, content=None)
    store.upsert_events([incoming], source="sola_api")

    assert incoming.venue_name is None, "upsert_events mutated the Event it was given"
    assert incoming.content is None
    # …while the stored row still keeps the old values
    assert store.query_events(*window())[0].venue_name == "Library"
