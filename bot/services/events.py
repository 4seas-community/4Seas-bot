"""活动数据源：Sola API → Sola iCal → 本地 YAML 三级降级。

Sola 没有公开 API 文档，端点是从开源前端 sociallayer-im/seastar-app 的
packages/sola-sdk 源码里读出来的，契约随时可能变 —— 所以这里对字段缺失一律容错，
单个事件解析失败只跳过它，不让整次播报挂掉。
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import httpx
import yaml

from ..config import settings
from ..models import Event

log = logging.getLogger(__name__)

MAX_PAGES = 5  # 分页拉取上限，防止数据源异常时无限翻页
PAGE_SIZE = 100
HTTP_TIMEOUT = 20.0


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def day_window(
    days_ahead: int,
    tz: ZoneInfo,
    now: dt.datetime | None = None,
    offset_days: int = 0,
):
    """返回 [今天+offset 00:00, 今天+offset+days_ahead 23:59:59]（本地时区）。

    offset_days=0 → 从今天算起；=1 → 从明天算起（晚上预告次日活动用）。
    """
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = base + dt.timedelta(days=offset_days)
    end = (start + dt.timedelta(days=days_ahead)).replace(hour=23, minute=59, second=59)
    return start, end


# ── 数据源 ────────────────────────────────────────────────────────────────


class EventSource:
    name = "base"

    async def fetch(self, window_start: dt.datetime, window_end: dt.datetime) -> list[Event]:
        raise NotImplementedError


class SolaApiSource(EventSource):
    """GET {api}/api/v1/events?group_id=<group>&collection=upcoming"""

    name = "sola_api"

    def _to_event(self, raw: dict) -> Event | None:
        start = _parse_iso(raw.get("start_time"))
        if start is None:
            return None
        place = raw.get("place") or {}
        owner = raw.get("owner") or {}
        eid = str(raw.get("id") or "")
        return Event(
            id=eid,
            title=(raw.get("title") or "(无标题)").strip(),
            start=start,
            end=_parse_iso(raw.get("end_time")),
            tz=raw.get("timezone") or settings.tz,
            place_title=(place.get("title") or None),
            place_address=(place.get("address") or raw.get("location") or None),
            host=(owner.get("nickname") or owner.get("name") or None),
            participants=raw.get("participant_count"),
            max_participants=raw.get("max_participant"),
            tags=[t for t in (raw.get("tags") or []) if t],
            meeting_url=(raw.get("meeting_url") or None),
            notes=(raw.get("notes") or None),
            require_approval=bool(raw.get("require_approval")),
            url=f"{settings.sola_web_base}/event/detail/{eid}" if eid else None,
            source=self.name,
        )

    async def fetch(self, window_start: dt.datetime, window_end: dt.datetime) -> list[Event]:
        events: list[Event] = []
        url = f"{settings.sola_api_base}/api/v1/events"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            for page in range(1, MAX_PAGES + 1):
                resp = await client.get(
                    url,
                    params={
                        "group_id": settings.sola_group,
                        "collection": "upcoming",
                        "limit": PAGE_SIZE,
                        "page": page,
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                rows = body.get("data") or []
                if not rows:
                    break

                parsed = [e for e in (self._to_event(r) for r in rows) if e]
                events.extend(parsed)

                meta = body.get("meta") or {}
                if not meta.get("next_page"):
                    break
                # 结果按开始时间升序；本页已整体越过窗口就不必再翻
                if parsed and min(e.start for e in parsed) > window_end:
                    break

        if not events:
            raise RuntimeError("Sola API 返回 0 条事件")
        return events


class SolaIcsSource(EventSource):
    """GET {api}/api/v1/groups/<group>/calendar.ics —— 全量历史，仅作降级。"""

    name = "sola_ics"

    async def fetch(self, window_start: dt.datetime, window_end: dt.datetime) -> list[Event]:
        from icalendar import Calendar

        url = f"{settings.sola_api_base}/api/v1/groups/{settings.sola_group}/calendar.ics"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            cal = Calendar.from_ical(resp.content)

        events: list[Event] = []
        for comp in cal.walk("VEVENT"):
            start = comp.get("DTSTART")
            if start is None:
                continue
            start_dt = start.dt
            if not isinstance(start_dt, dt.datetime):  # DATE → 当天 00:00
                start_dt = dt.datetime.combine(start_dt, dt.time.min)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=settings.zone)

            end = comp.get("DTEND")
            end_dt = end.dt if end is not None else None
            if end_dt is not None and not isinstance(end_dt, dt.datetime):
                end_dt = dt.datetime.combine(end_dt, dt.time.max)
            if end_dt is not None and end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=settings.zone)

            uid = str(comp.get("UID") or "")
            events.append(
                Event(
                    id=uid,
                    title=str(comp.get("SUMMARY") or "(无标题)"),
                    start=start_dt,
                    end=end_dt,
                    tz=settings.tz,
                    place_address=str(comp.get("LOCATION")) if comp.get("LOCATION") else None,
                    notes=str(comp.get("DESCRIPTION")) if comp.get("DESCRIPTION") else None,
                    url=str(comp.get("URL")) if comp.get("URL") else None,
                    source=self.name,
                )
            )
        if not events:
            raise RuntimeError("iCal 里没有 VEVENT")
        return events


class LocalYamlSource(EventSource):
    """data/events.yaml —— 兜底 + 补充 sola.day 上没有的线下活动。"""

    name = "local_yaml"

    def __init__(self, path: str | Path = "data/events.yaml") -> None:
        self.path = Path(path)

    async def fetch(self, window_start: dt.datetime, window_end: dt.datetime) -> list[Event]:
        if not self.path.exists():
            raise RuntimeError(f"{self.path} 不存在")
        rows = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []

        events: list[Event] = []
        for i, r in enumerate(rows):
            start = _parse_iso(r.get("start"))
            if start is None:
                log.warning("events.yaml 第 %d 条缺少合法 start，跳过", i + 1)
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=settings.zone)
            end = _parse_iso(r.get("end"))
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=settings.zone)
            events.append(
                Event(
                    id=str(r.get("id") or f"local-{i}"),
                    title=r.get("title") or "(无标题)",
                    start=start,
                    end=end,
                    tz=r.get("timezone") or settings.tz,
                    place_address=r.get("location"),
                    host=r.get("host"),
                    tags=r.get("tags") or [],
                    notes=r.get("notes"),
                    url=r.get("url"),
                    source=self.name,
                )
            )
        if not events:
            raise RuntimeError("events.yaml 是空的")
        return events


# ── 降级链 ────────────────────────────────────────────────────────────────


class FetchOutcome(NamedTuple):
    events: list[Event]
    window: tuple[dt.datetime, dt.datetime]
    source: str


class EventService:
    """只负责从上游把活动拉下来。落库、去重、查询都归 Storage。"""

    def __init__(self, sources: list[EventSource] | None = None) -> None:
        self.sources = sources or [SolaApiSource(), SolaIcsSource(), LocalYamlSource()]
        self.last_source: str | None = None
        self.last_error: str | None = None

    async def fetch_upstream(self, horizon_days: int | None = None) -> FetchOutcome:
        """按降级链拉取 [今天, 今天+horizon] 内的活动。

        全部数据源都失败时抛出最后一个异常 —— 由调用方决定是报警还是静默。
        """
        days = settings.sync_horizon_days if horizon_days is None else horizon_days
        start, end = day_window(days, settings.zone)

        last_exc: Exception | None = None
        for source in self.sources:
            try:
                raw = await source.fetch(start, end)
            except Exception as exc:  # 单个源失败就换下一个
                log.warning("事件源 %s 失败：%s", source.name, exc)
                last_exc = exc
                continue

            hits = sorted(
                (e for e in raw if e.overlaps(start, end)), key=lambda e: e.start
            )
            self.last_source = source.name
            self.last_error = None
            log.info("事件源 %s 命中 %d/%d 条（未来 %d 天）", source.name, len(hits), len(raw), days)
            return FetchOutcome(hits, (start, end), source.name)

        self.last_error = str(last_exc)
        raise last_exc or RuntimeError("没有可用的事件源")


event_service = EventService()
