"""SQLite 持久化：活动库、播报去重、关键词冷却、问答用量。

活动表的幂等靠 `PRIMARY KEY (source, event_id)` + UPSERT 保证：
同一个 Sola 活动无论同步多少次都只有一行。是否真的变过用 content_hash 判断，
只有内容变了才更新 updated_at —— 这样「有没有被改过」是可查的，而不是每次同步全表刷新。

写入量很小（每天几百行），同步 sqlite3 足够；单次调用微秒级，
放在 asyncio 事件循环里不会造成可观测阻塞。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    source           TEXT    NOT NULL,
    event_id         TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    start_ts         REAL    NOT NULL,
    end_ts           REAL,
    tz               TEXT    NOT NULL,
    place_title      TEXT,
    place_address    TEXT,
    host             TEXT,
    participants     INTEGER,
    max_participants INTEGER,
    tags             TEXT    NOT NULL DEFAULT '[]',
    meeting_url      TEXT,
    notes            TEXT,
    require_approval INTEGER NOT NULL DEFAULT 0,
    url              TEXT,
    content_hash     TEXT    NOT NULL,
    first_seen_at    TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    last_seen_at     TEXT    NOT NULL,
    deleted_at       TEXT,
    PRIMARY KEY (source, event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events (start_ts);
CREATE INDEX IF NOT EXISTS idx_events_live  ON events (deleted_at, start_ts);

CREATE TABLE IF NOT EXISTS sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    ran_at     TEXT NOT NULL,
    fetched    INTEGER NOT NULL,
    inserted   INTEGER NOT NULL,
    updated    INTEGER NOT NULL,
    unchanged  INTEGER NOT NULL,
    removed    INTEGER NOT NULL,
    ok         INTEGER NOT NULL,
    error      TEXT
);

CREATE TABLE IF NOT EXISTS report_log (
    chat_id     INTEGER NOT NULL,
    report_date TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, report_date)
);
CREATE TABLE IF NOT EXISTS keyword_cooldown (
    chat_id  INTEGER NOT NULL,
    rule_id  TEXT    NOT NULL,
    fired_at REAL    NOT NULL,
    PRIMARY KEY (chat_id, rule_id)
);
CREATE TABLE IF NOT EXISTS ask_usage (
    user_id  INTEGER NOT NULL,
    asked_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ask_usage ON ask_usage (user_id, asked_at);
"""

# 参与 content_hash 的字段。participants（报名人数）故意排除 ——
# 它每天都在变，算进去会让每次同步都判定为"内容变了"，updated_at 就失去意义了。
_HASH_FIELDS = (
    "title", "start_ts", "end_ts", "tz", "place_title", "place_address",
    "host", "max_participants", "tags", "meeting_url", "notes",
    "require_approval", "url",
)


@dataclass(slots=True)
class SyncResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0

    def __str__(self) -> str:
        return (
            f"fetched {self.fetched} · new {self.inserted} · updated {self.updated} · "
            f"unchanged {self.unchanged} · delisted {self.removed}"
        )


def _event_to_row(ev: Event, now_iso: str) -> dict:
    row = {
        "source": ev.source,
        "event_id": ev.id,
        "title": ev.title,
        "start_ts": ev.start.timestamp(),
        "end_ts": ev.end.timestamp() if ev.end else None,
        "tz": ev.tz,
        "place_title": ev.place_title,
        "place_address": ev.place_address,
        "host": ev.host,
        "participants": ev.participants,
        "max_participants": ev.max_participants,
        "tags": json.dumps(ev.tags, ensure_ascii=False, sort_keys=True),
        "meeting_url": ev.meeting_url,
        "notes": ev.notes,
        "require_approval": int(ev.require_approval),
        "url": ev.url,
    }
    payload = json.dumps([row[f] for f in _HASH_FIELDS], ensure_ascii=False)
    row["content_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    row["first_seen_at"] = now_iso
    row["updated_at"] = now_iso
    row["last_seen_at"] = now_iso
    return row


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["event_id"],
        title=row["title"],
        start=dt.datetime.fromtimestamp(row["start_ts"], dt.UTC),
        end=dt.datetime.fromtimestamp(row["end_ts"], dt.UTC) if row["end_ts"] else None,
        tz=row["tz"],
        place_title=row["place_title"],
        place_address=row["place_address"],
        host=row["host"],
        participants=row["participants"],
        max_participants=row["max_participants"],
        tags=json.loads(row["tags"] or "[]"),
        meeting_url=row["meeting_url"],
        notes=row["notes"],
        require_approval=bool(row["require_approval"]),
        url=row["url"],
        source=row["source"],
    )


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── 活动同步（幂等） ──────────────────────────────────────────────
    def upsert_events(
        self,
        events: list[Event],
        *,
        source: str,
        window: tuple[dt.datetime, dt.datetime] | None = None,
        now: dt.datetime | None = None,
    ) -> SyncResult:
        """把一批活动写入库。同一个 (source, event_id) 反复写只会有一行。

        window 给定时，落在窗口内、但这次没被拉到的活动会打上 deleted_at
        （上游取消或删除）。窗口外的历史数据不动。
        """
        now_iso = (now or dt.datetime.now(dt.UTC)).isoformat()
        result = SyncResult(fetched=len(events))

        with self._lock:
            existing = {
                r["event_id"]: r["content_hash"]
                for r in self._conn.execute(
                    "SELECT event_id, content_hash FROM events WHERE source = ?", (source,)
                )
            }

            for ev in events:
                row = _event_to_row(ev, now_iso)
                row["source"] = source
                prev_hash = existing.get(row["event_id"])
                if prev_hash is None:
                    result.inserted += 1
                elif prev_hash != row["content_hash"]:
                    result.updated += 1
                else:
                    result.unchanged += 1

                self._conn.execute(
                    """
                    INSERT INTO events (
                        source, event_id, title, start_ts, end_ts, tz,
                        place_title, place_address, host, participants, max_participants,
                        tags, meeting_url, notes, require_approval, url,
                        content_hash, first_seen_at, updated_at, last_seen_at, deleted_at
                    ) VALUES (
                        :source, :event_id, :title, :start_ts, :end_ts, :tz,
                        :place_title, :place_address, :host, :participants, :max_participants,
                        :tags, :meeting_url, :notes, :require_approval, :url,
                        :content_hash, :first_seen_at, :updated_at, :last_seen_at, NULL
                    )
                    ON CONFLICT(source, event_id) DO UPDATE SET
                        title            = excluded.title,
                        start_ts         = excluded.start_ts,
                        end_ts           = excluded.end_ts,
                        tz               = excluded.tz,
                        place_title      = excluded.place_title,
                        place_address    = excluded.place_address,
                        host             = excluded.host,
                        participants     = excluded.participants,
                        max_participants = excluded.max_participants,
                        tags             = excluded.tags,
                        meeting_url      = excluded.meeting_url,
                        notes            = excluded.notes,
                        require_approval = excluded.require_approval,
                        url              = excluded.url,
                        last_seen_at     = excluded.last_seen_at,
                        deleted_at       = NULL,
                        updated_at       = CASE
                            WHEN events.content_hash <> excluded.content_hash
                            THEN excluded.updated_at ELSE events.updated_at END,
                        content_hash     = excluded.content_hash
                    """,
                    row,
                )

            if window is not None:
                ws, we = window
                cur = self._conn.execute(
                    """
                    UPDATE events SET deleted_at = ?
                    WHERE source = ? AND deleted_at IS NULL
                      AND last_seen_at < ?
                      AND start_ts >= ? AND start_ts <= ?
                    """,
                    (now_iso, source, now_iso, ws.timestamp(), we.timestamp()),
                )
                result.removed = cur.rowcount or 0

            self._conn.commit()
        return result

    def log_sync(self, source: str, result: SyncResult, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sync_log (source, ran_at, fetched, inserted, updated, unchanged, "
                "removed, ok, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source, dt.datetime.now(dt.UTC).isoformat(), result.fetched,
                    result.inserted, result.updated, result.unchanged, result.removed,
                    int(ok), error,
                ),
            )
            self._conn.commit()

    def last_sync(self) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
            ).fetchone()

    # ── 活动查询 ──────────────────────────────────────────────────────
    def query_events(self, window_start: dt.datetime, window_end: dt.datetime) -> list[Event]:
        """取与时间窗有交集的、未下架的活动，按开始时间升序。

        `COALESCE(end_ts, start_ts) >= ?` 让跨天活动（昨天开始、今天还在进行）
        也能出现在今天的播报里。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events
                WHERE deleted_at IS NULL
                  AND start_ts <= ?
                  AND COALESCE(end_ts, start_ts) >= ?
                ORDER BY start_ts ASC
                """,
                (window_end.timestamp(), window_start.timestamp()),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def event_stats(self) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS live "
                "FROM events"
            ).fetchone()
        return {"total": row["total"] or 0, "live": row["live"] or 0}

    # ── 播报去重 ──────────────────────────────────────────────────────
    def mark_reported(self, chat_id: int, day: dt.date) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO report_log (chat_id, report_date, sent_at) VALUES (?, ?, ?)",
                    (chat_id, day.isoformat(), dt.datetime.now(dt.UTC).isoformat()),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def already_reported(self, chat_id: int, day: dt.date) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM report_log WHERE chat_id = ? AND report_date = ?",
                (chat_id, day.isoformat()),
            ).fetchone()
        return row is not None

    # ── 关键词冷却 ────────────────────────────────────────────────────
    def try_fire_keyword(self, chat_id: int, rule_id: str, cooldown: int, now: float) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT fired_at FROM keyword_cooldown WHERE chat_id = ? AND rule_id = ?",
                (chat_id, rule_id),
            ).fetchone()
            if row and now - row["fired_at"] < cooldown:
                return False
            self._conn.execute(
                "INSERT INTO keyword_cooldown (chat_id, rule_id, fired_at) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, rule_id) DO UPDATE SET fired_at = excluded.fired_at",
                (chat_id, rule_id, now),
            )
            self._conn.commit()
            return True

    # ── 问答限流 ──────────────────────────────────────────────────────
    def ask_count_last_hour(self, user_id: int, now: float) -> int:
        with self._lock:
            self._conn.execute("DELETE FROM ask_usage WHERE asked_at < ?", (now - 86400,))
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM ask_usage WHERE user_id = ? AND asked_at > ?",
                (user_id, now - 3600),
            ).fetchone()
            self._conn.commit()
        return int(row["c"]) if row else 0

    def record_ask(self, user_id: int, now: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ask_usage (user_id, asked_at) VALUES (?, ?)", (user_id, now)
            )
            self._conn.commit()

    def ask_count_today(self, now: float) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM ask_usage WHERE asked_at > ?", (now - 86400,)
            ).fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
