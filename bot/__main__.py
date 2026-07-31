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
from .handlers.dynamic import DynamicCommandManager
from .jobs.daily_report import daily_report_job
from .jobs.sync_events import sync_events_job
from .web.server import AdminServer

log = logging.getLogger(__name__)


async def _on_start(app: Application) -> None:
    """Telegram 的命令菜单和 HTTP 服务都要等事件循环起来之后才能弄。"""
    manager = app.bot_data.get("dynamic_commands")
    if manager is not None:
        await manager.publish_menu()
    server = app.bot_data.get("admin_server")
    if server is not None:
        await server.start()


async def _on_stop(app: Application) -> None:
    server = app.bot_data.get("admin_server")
    if server is not None:
        await server.stop()


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

    # group 5：配置驱动的自定义命令。放在 bot_data 里，/reload 才拿得到它。
    manager = DynamicCommandManager(app)
    manager.reload()
    app.bot_data["dynamic_commands"] = manager

    if settings.web_enabled:
        app.bot_data["admin_server"] = AdminServer(manager)
    app.post_init = _on_start
    app.post_shutdown = _on_stop

    if app.job_queue is None:
        log.error("JobQueue 不可用 —— 请装 python-telegram-bot[job-queue]，定时任务全部不会运行")
        return app

    # 先同步、后播报。SYNC_TIMES 里至少要有一个时间点早于 DAILY_REPORT_TIME，
    # 保证播报读到的是最新活动。
    for i, at in enumerate(settings.sync_at):
        app.job_queue.run_daily(sync_events_job, time=at, name="sync_events" if i == 0 else f"sync_events_{i}")
    app.job_queue.run_daily(daily_report_job, time=settings.report_time, name="daily_report")

    if settings.sync_on_startup:
        # 冷启动补一次，免得刚部署完库是空的。幂等，重启多少次都无副作用。
        app.job_queue.run_once(sync_events_job, when=5, name="sync_events_startup")

    log.info(
        "定时任务已调度：同步 %s（未来 %d 天）→ 播报 %s（%s），时区 %s，目标群 %s",
        settings.sync_times,
        settings.sync_horizon_days,
        settings.daily_report_time,
        settings.report_scope_label,
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
    if settings.leave_unknown_chats:
        warnings.append(
            "LEAVE_UNKNOWN_CHATS=true —— 非白名单群会被自动退出，"
            "白名单填错会丢掉正式群的管理员身份"
        )
    if muted := settings.muted_chat_ids:
        warnings.append(f"静默名单生效，这些群里 bot 不会说任何话：{sorted(muted)}")
    if settings.report_chat_id in settings.muted_chat_ids:
        warnings.append(
            f"播报目标群 {settings.report_chat_id} 在静默名单里 —— 每日播报不会发出"
        )
    return warnings


def main() -> None:
    setup_logging()
    for w in preflight():
        log.warning("⚠️  %s", w)

    app = build_application()
    log.info("4Seas Bot 启动，开始长轮询…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=settings.drop_pending_updates,
    )


if __name__ == "__main__":
    main()
