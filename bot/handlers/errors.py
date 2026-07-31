"""全局错误处理与群白名单守卫。"""

from __future__ import annotations

import html
import logging
import traceback

from telegram import Update
from telegram.constants import ParseMode
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


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("处理更新时出错", exc_info=context.error)

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
