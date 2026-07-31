"""The suite must run on a clean checkout with no .env and no secrets.

Round 2 of the Codex review never produced a verdict: the reviewer could not run
the tests at all. `bot/config.py` instantiates `Settings()` at import time and
`telegram_bot_token` has no default, so without a `.env` the whole suite died
during collection. Anyone cloning the repo, and any CI job, hit the same wall.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_suite_collects_without_dotenv_or_secrets():
    """Run a real subprocess with a scrubbed environment — importing the module
    in-process would prove nothing, since conftest has already run here."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("TELEGRAM_", "DEEPSEEK_", "OPENAI_", "SOLA_", "WEB_"))
    }
    env["BOT_SETTINGS_ENV_FILE"] = ""  # pretend .env does not exist

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        "collection failed on a secret-free environment:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )


def test_conftest_contains_no_real_credentials():
    """Placeholders only — a leaked bot token here would be committed to a public repo."""
    text = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    for marker in ("4SEA_TELE_BOT_TOKEN", "8674534389", "-1001242897290", "sk-", "nvapi-"):
        assert marker not in text, f"{marker!r} must not appear in conftest.py"
