"""全局错误处理与群白名单守卫。"""

from __future__ import annotations

import html
import logging
import time
import traceback

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..deps import settings

log = logging.getLogger(__name__)


async def guard_allowed_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """白名单 / 静默名单守卫。挂在 group=-1，先于所有业务 handler 执行。

    三层：
      1. 私聊放行（管理员要能私聊 bot）
      2. MUTED_CHATS 里的群：收消息但一律不回，用于"已加入但还没准备好开口"的群
      3. 不在 ALLOWED_CHATS 里的群：默认静默忽略；只有显式打开
         LEAVE_UNKNOWN_CHATS 才退群

    退群做成 opt-in 是因为它不可逆得离谱：白名单少填一个 id，bot 就会自动退出
    正式群并丢掉管理员身份，重新拉回去还要重设管理员、重关 privacy mode。
    """
    chat = update.effective_chat
    if chat is None or chat.type == chat.PRIVATE:
        return

    if chat.id in settings.muted_chat_ids:
        log.info("群 %s (%s) 在静默名单里，不作任何响应", chat.id, chat.title)
        raise ApplicationHandlerStop

    if settings.is_allowed_chat(chat.id):
        return

    if settings.leave_unknown_chats:
        log.warning("收到非白名单群 %s (%s) 的消息，退出该群", chat.id, chat.title)
        try:
            await context.bot.leave_chat(chat.id)
        except Exception as exc:
            log.warning("退群失败：%s", exc)
    else:
        log.info("忽略非白名单群 %s (%s) 的消息", chat.id, chat.title)

    raise ApplicationHandlerStop


# 重启时新旧进程会短暂重叠（launchd/systemd 都是先起新的），旧进程的 getUpdates
# 被踢掉就抛 Conflict；掉线重连会抛 NetworkError/TimedOut。这些都是瞬时的，PTB
# 自己会退避重连。给管理员推一整页 traceback 只会让人开始忽略告警 —— 等真出事
# 那次也一起被忽略掉。所以：照常记日志，但只有持续发生才打扰人。
TRANSIENT = (Conflict, NetworkError, TimedOut)
TRANSIENT_ALERT_AFTER = 5        # 连续这么多次才告警
TRANSIENT_WINDOW_SECONDS = 300   # 超过这个间隔就当作新的一串，计数清零
# 跨过阈值之后还必须限频：真有两个实例并存时，getUpdates 冲突是几秒一次的，
# 不限频就会从"一条 traceback"变成"每秒一条告警"，比原来的问题更糟。
TRANSIENT_ALERT_COOLDOWN = 1800  # 30 分钟内最多再提醒一次

_transient_streak = 0
_transient_last = 0.0
_transient_alerted_at = 0.0


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _transient_streak, _transient_last, _transient_alerted_at

    error = context.error
    if isinstance(error, TRANSIENT):
        now = time.monotonic()
        if now - _transient_last > TRANSIENT_WINDOW_SECONDS:
            _transient_streak = 0
        _transient_last = now
        _transient_streak += 1

        if _transient_streak < TRANSIENT_ALERT_AFTER:
            log.warning(
                "%s（第 %d 次，%d 次后才告警）：%s",
                type(error).__name__, _transient_streak, TRANSIENT_ALERT_AFTER, error,
            )
            return

        if now - _transient_alerted_at < TRANSIENT_ALERT_COOLDOWN:
            log.warning(
                "%s 已连续 %d 次（%.0f 分钟内已提醒过，不重复打扰）",
                type(error).__name__, _transient_streak,
                TRANSIENT_ALERT_COOLDOWN / 60,
            )
            return

        _transient_alerted_at = now
        log.error("%s 已连续 %d 次，告警", type(error).__name__, _transient_streak)
        if settings.admin_ids:
            hint = (
                "另一个实例在抢同一个 token。检查是不是有两个进程在跑："
                "`./start.sh --status`、`launchctl list | grep 4seas`"
                if isinstance(error, Conflict) else "网络持续不通"
            )
            for admin_id in settings.admin_ids:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"⚠️ <b>{type(error).__name__}</b> 已连续 {_transient_streak} 次\n\n{hint}",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    log.warning("发送异常告警给 %s 失败：%s", admin_id, exc)
        return

    _transient_streak = 0
    _transient_alerted_at = 0.0
    log.error("处理更新时出错", exc_info=error)

    if not settings.admin_ids:
        return

    tb = "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    )[-1500:]
    text = (
        "⚠️ <b>Bot error</b>\n\n"
        f"<pre>{html.escape(tb)}</pre>"
    )
    for admin_id in settings.admin_ids:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.warning("发送异常告警给 %s 失败：%s", admin_id, exc)
