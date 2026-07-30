"""入口：构建 Application、注册 handler 与定时任务、长轮询启动。"""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from .config import settings
from .handlers import commands, errors, interactions
from .jobs.daily_report import daily_report_job
from .jobs.sync_events import sync_events_job

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stdout,
    )
    # httpx 每次 getUpdates 都打一行 INFO，太吵
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_application() -> Application:
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # group -1：白名单守卫，先于一切业务逻辑
    app.add_handler(TypeHandler(Update, errors.guard_allowed_chat), group=-1)

    # group 0：命令
    app.add_handler(CommandHandler("start", commands.cmd_start))
    app.add_handler(CommandHandler("help", commands.cmd_help))
    app.add_handler(CommandHandler("events", commands.cmd_events))
    app.add_handler(CommandHandler("ask", commands.cmd_ask))
    app.add_handler(CommandHandler("faq", commands.cmd_faq))
    app.add_handler(CommandHandler("sync", commands.cmd_sync))
    app.add_handler(CommandHandler("report", commands.cmd_report))
    app.add_handler(CommandHandler("reload", commands.cmd_reload))
    app.add_handler(CommandHandler("status", commands.cmd_status))

    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, interactions.on_new_members)
    )

    # group 1：被 @ / 被回复 → 问答（命中后抛 ApplicationHandlerStop，阻断 group 2）
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, interactions.on_mention), group=1
    )

    # group 2：关键词主动触发
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, interactions.on_keyword), group=2
    )

    app.add_error_handler(errors.on_error)

    if app.job_queue is None:
        log.error("JobQueue 不可用 —— 请装 python-telegram-bot[job-queue]，定时任务全部不会运行")
        return app

    # 先同步、后播报。sync_time 默认比 daily_report_time 早半小时，
    # 保证播报读到的是当天最新的活动。
    app.job_queue.run_daily(sync_events_job, time=settings.sync_at, name="sync_events")
    app.job_queue.run_daily(daily_report_job, time=settings.report_time, name="daily_report")

    if settings.sync_on_startup:
        # 冷启动补一次，免得刚部署完库是空的。幂等，重启多少次都无副作用。
        app.job_queue.run_once(sync_events_job, when=5, name="sync_events_startup")

    log.info(
        "定时任务已调度：同步 %s（未来 %d 天）→ 播报 %s（%s），时区 %s，目标群 %s",
        settings.sync_time,
        settings.sync_horizon_days,
        settings.daily_report_time,
        "仅当天" if settings.daily_report_days_ahead == 0 else f"当天+{settings.daily_report_days_ahead}天",
        settings.tz,
        settings.report_chat_id,
    )
    return app


def preflight() -> list[str]:
    """启动前自检，返回警告列表（不阻断启动）。"""
    warnings: list[str] = []
    if not settings.admin_ids:
        warnings.append("TELEGRAM_ADMIN_IDS 为空 —— 管理员命令和异常告警都不会生效")
    if not settings.allowed_chats:
        warnings.append("TELEGRAM_ALLOWED_CHATS 为空 —— bot 会在任何群里工作")
    if settings.report_chat_id is None:
        warnings.append("未配置播报目标群 —— 每日播报会跳过")
    if not (settings.deepseek_api_key or settings.openai_api_key):
        warnings.append("未配置任何 LLM 密钥 —— 问答将只返回 FAQ 原文")
    return warnings


def main() -> None:
    setup_logging()
    for w in preflight():
        log.warning("⚠️  %s", w)

    app = build_application()
    log.info("4Seas Bot 启动，开始长轮询…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
