"""Test environment setup.

`bot/config.py` builds a `Settings()` at import time and `telegram_bot_token` has
no default, so on a clean checkout with no `.env` the whole suite dies during
collection with a pydantic ValidationError — before a single test runs.

That is a real papercut: anyone cloning the repo, and any CI job, hits it. Fill in
placeholders here so `pytest` works with nothing but `pip install -e ".[dev]"`.

Real values must never leak in: `TEST_ENV` is applied with `setdefault`, so an
explicitly exported variable still wins, and `.env` (which is gitignored and holds
the production token) is disabled outright for the test run.
"""

from __future__ import annotations

import os

TEST_ENV = {
    "TELEGRAM_BOT_TOKEN": "0000000000:test-token-not-a-real-bot",
    "TELEGRAM_ADMIN_IDS": "1",
    "TELEGRAM_ALLOWED_CHATS": "-100000000001",
    "TELEGRAM_MUTED_CHATS": "",
    "DAILY_REPORT_CHAT_ID": "-100000000001",
    # No LLM keys: tests must exercise the deterministic paths by default and
    # never make a billable call by accident.
    "DEEPSEEK_API_KEY": "",
    "OPENAI_API_KEY": "",
    "DB_PATH": ":memory:",
    "WEB_ENABLED": "false",
    "SYNC_ON_STARTUP": "false",
    "TZ": "Asia/Bangkok",
}

for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)

# Ignore the developer's real .env — otherwise a local machine and CI run against
# different configuration and tests pass in one place but not the other.
os.environ.setdefault("BOT_SETTINGS_ENV_FILE", "")
