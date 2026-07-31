"""跨数据源的统一事件模型。

所有事件源（Sola API / iCal / 本地 YAML）都产出 Event，渲染层只认这一个结构。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class Event:
    id: str
    title: str
    start: dt.datetime  # 带时区
    end: dt.datetime | None
    tz: str = "Asia/Bangkok"

    place_title: str | None = None
    place_address: str | None = None
    # 只有详情接口才有的两个字段，列表接口拿不到：
    venue_name: str | None = None   # "Event Space - 1st Floor 4Seas Nimman"
    content: str | None = None      # 主办方写的完整介绍
    host: str | None = None
    participants: int | None = None
    max_participants: int | None = None
    tags: list[str] = field(default_factory=list)
    meeting_url: str | None = None
    notes: str | None = None
    require_approval: bool = False
    url: str | None = None
    source: str = "unknown"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def local_start(self) -> dt.datetime:
        return self.start.astimezone(self.zone)

    @property
    def local_end(self) -> dt.datetime | None:
        return self.end.astimezone(self.zone) if self.end else None

    @property
    def is_all_day(self) -> bool:
        """Sola 的全天活动表示为本地 00:00:00 → 23:59:59。"""
        s, e = self.local_start, self.local_end
        if e is None:
            return False
        return s.hour == 0 and s.minute == 0 and e.hour == 23 and e.minute == 59

    def overlaps(self, window_start: dt.datetime, window_end: dt.datetime) -> bool:
        """事件是否与 [window_start, window_end] 有交集（跨天活动也算）。"""
        end = self.end or self.start
        return self.start <= window_end and end >= window_start
