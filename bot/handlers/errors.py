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
    """白名单守卫。挂在 group=-1，先于所有业务 handler 执行。

    私聊放行（管理员要能私聊 bot）；不在白名单的群直接退出，不回任何消息 ——
    对拉群者静默是刻意的，省得变成骚扰。
    """
    chat = update.effective_chat
    if chat is None or chat.type == chat.PRIVATE:
        return
    if settings.is_allowed_chat(chat.id):
        return

    log.warning("收到非白名单群 %s (%s) 的消息，退出该群", chat.id, chat.title)
    try:
        await context.bot.leave_chat(chat.id)
    except Exception as exc:
        log.warning("退群失败：%s", exc)
    raise ApplicationHandlerStop


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("处理更新时出错", exc_info=context.error)

    if not settings.admin_ids:
        return

    tb = "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    )[-1500:]
    text = (
        "⚠️ <b>Bot 异常</b>\n\n"
        f"<pre>{html.escape(tb)}</pre>"
    )
    for admin_id in settings.admin_ids:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.warning("发送异常告警给 %s 失败：%s", admin_id, exc)
