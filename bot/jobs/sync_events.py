"""定时把 Social Layer 的活动导入本地数据库。

幂等由三层保证：
  1. 主键 (source, event_id) + UPSERT —— 同一个活动重复导入只会有一行
  2. content_hash —— 内容没变就不动 updated_at，"改过没有"始终可查
  3. 窗口内软删除 —— 上游取消的活动打 deleted_at，而不是物理删除

因此这个任务可以任意频率重复执行，跑 100 次和跑 1 次的库状态完全一致。
"""

from __future__ import annotations

import asyncio
import logging

from telegram.ext import ContextTypes

from ..deps import event_service, settings, storage
from ..storage import SyncResult

log = logging.getLogger(__name__)

# 同一时刻只允许一次同步：定时任务和管理员手动 /sync 可能撞车
_sync_lock = asyncio.Lock()


# 单次同步的最坏耗时：列表最多 5 页 × 20s，加上详情补齐 ceil(N/6) 批 × 20s，
# 合计可达 200s 上下 —— 不是"几秒"。所以等锁必须有上限，整次同步也要有上限。
LOCK_WAIT_TIMEOUT = 240.0
SYNC_TIMEOUT = 300.0


async def sync_events(horizon_days: int | None = None) -> tuple[bool, str]:
    """执行一次导入。返回 (是否成功, 给人看的结果描述)。

    撞车时等待而不是放弃：早期版本直接返回 False，调用方（每日播报）把它当成
    "没有数据"，于是在冷启动 + 定时播报同时发生时往群里发了一条
    "Nothing scheduled tomorrow"。

    但"无限期等下去"是另一个极端。持锁方若卡在没有超时保护的路径上（比如
    upsert_events 撞上被别的进程锁住的 SQLite），等锁的一方会静默挂死 ——
    既不失败也不成功，播报永远不发，也永远不告警。所以两处都设上限。
    """
    if _sync_lock.locked():
        log.info("a sync is in flight — waiting up to %.0fs for it", LOCK_WAIT_TIMEOUT)

    try:
        await asyncio.wait_for(_sync_lock.acquire(), timeout=LOCK_WAIT_TIMEOUT)
    except TimeoutError:
        log.error("等锁超时（%.0fs）—— 上一次同步疑似卡死", LOCK_WAIT_TIMEOUT)
        return False, f"Timed out after {LOCK_WAIT_TIMEOUT:.0f}s waiting for an in-flight sync"

    try:
        days = settings.sync_horizon_days if horizon_days is None else horizon_days
        try:
            outcome = await asyncio.wait_for(
                event_service.fetch_upstream(days), timeout=SYNC_TIMEOUT
            )
        except TimeoutError:
            log.error("同步超时（%.0fs）", SYNC_TIMEOUT)
            storage.log_sync("unknown", SyncResult(), ok=False, error="timed out")
            return False, f"Fetch timed out after {SYNC_TIMEOUT:.0f}s"
        except Exception as exc:
            log.error("活动同步失败：%s", exc, exc_info=True)
            storage.log_sync("unknown", SyncResult(), ok=False, error=str(exc))
            return False, f"Fetch failed: {exc}"

        result = storage.upsert_events(
            outcome.events, source=outcome.source, window=outcome.window
        )
        storage.log_sync(outcome.source, result, ok=True)
        log.info("活动同步完成（%s）：%s", outcome.source, result)
        return True, f"{outcome.source} · {result}"
    finally:
        # 必须放 finally —— 中间任何一条 return 或异常都不能把锁漏掉，
        # 否则之后每次同步都会等满 LOCK_WAIT_TIMEOUT 然后失败。
        _sync_lock.release()


async def sync_events_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue 回调。失败时告警管理员，但不抛出 —— 免得整个 job 被摘掉。"""
    ok, detail = await sync_events()
    if ok:
        return
    for admin_id in settings.admin_ids:
        try:
            await context.bot.send_message(admin_id, f"⚠️ Event sync failed: {detail}")
        except Exception as exc:
            log.warning("给管理员 %s 发同步告警失败：%s", admin_id, exc)
