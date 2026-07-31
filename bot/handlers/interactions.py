"""能力 3、4：互动响应与关键词触发。

两者都挂在普通消息上，靠 handler group 分优先级：
  group 1 —— 被 @ / 回复 bot  → 走问答
  group 2 —— 关键词触发       → 模板回复（不烧 token）
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..deps import keyword_rules, settings, storage
from ..render import esc
from .commands import answer_question

log = logging.getLogger(__name__)

WELCOME = """👋 Welcome to <b>4Seas Community</b>, {names}!

Send /events to see what's coming up, or /help to see what I can do.
Got a question? Just @ me, or use <code>/ask</code>."""


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    members = [m for m in (msg.new_chat_members or []) if not m.is_bot]
    if not members:
        return
    log.info("新成员入群 | chat=%s 人数=%d", msg.chat_id, len(members))
    names = ", ".join(esc(m.full_name) for m in members[:5])
    if len(members) > 5:
        names += f" and {len(members) - 5} more"
    await msg.reply_text(
        WELCOME.format(names=names), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def on_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """被 @ 或被回复时走问答。"""
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    bot_username = context.bot.username

    is_reply_to_bot = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )
    mention = f"@{bot_username}"
    is_mention = mention.lower() in text.lower()

    if not (is_reply_to_bot or is_mention):
        return

    log.info("被%s | chat=%s user=%s", "回复" if is_reply_to_bot else "@", msg.chat_id,
             update.effective_user.id if update.effective_user else "?")
    question = text.replace(mention, "").replace(mention.lower(), "").strip()
    await answer_question(update, context, question)
    # 已经作为提问处理过了，不要再让关键词规则对同一条消息二次回复
    raise ApplicationHandlerStop


async def on_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """关键词主动触发。命中即回模板，带冷却。"""
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    if not text:
        return

    rule = keyword_rules.match(text)
    if rule is None:
        return

    if not storage.try_fire_keyword(msg.chat_id, rule.id, rule.cooldown, time.time()):
        log.debug("关键词 %s 在冷却中，跳过", rule.id)
        return

    log.info("关键词触发：rule=%s chat=%s", rule.id, msg.chat_id)
    await msg.reply_text(rule.reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
