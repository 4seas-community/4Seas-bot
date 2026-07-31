"""Runtime settings: validation, layering, and cache invalidation.

These settings are edited from a web form while the bot is live, so a bad value
must be refused at the form rather than discovered at 19:00 in a 776-member group.
"""

import json

import pytest

from bot.services import runtime_config as rc


def field(key):
    return rc.BY_KEY[key]


# ── validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("19:00", "19:00"), ("9:5", "09:05"), (" 07:30 ", "07:30"), ("00:00", "00:00"),
])
def test_times_are_normalised(raw, expected):
    assert rc.coerce(field("daily_report_time"), raw) == expected


@pytest.mark.parametrize("bad", ["25:99", "7", "seven", "19:60", "", "19:00:00"])
def test_invalid_times_rejected(bad):
    with pytest.raises(rc.ConfigError):
        rc.coerce(field("daily_report_time"), bad)


def test_multiple_sync_times():
    assert rc.coerce(field("sync_times"), "8:30, 18:30") == "08:30,18:30"


def test_sync_times_cannot_be_empty():
    """No import at all means the digest quietly goes out on stale data."""
    with pytest.raises(rc.ConfigError):
        rc.coerce(field("sync_times"), "  ")


@pytest.mark.parametrize("raw,expected", [
    ("-100123, 456", "-100123,456"),
    ("-100123，456", "-100123,456"),   # full-width comma, easy to type on a Mac
    ("", ""),
    (" -100123 ", "-100123"),
])
def test_id_lists_are_normalised(raw, expected):
    assert rc.coerce(field("telegram_muted_chats"), raw) == expected


def test_non_numeric_id_rejected():
    with pytest.raises(rc.ConfigError, match="not a numeric id"):
        rc.coerce(field("telegram_muted_chats"), "-100123, @somegroup")


def test_target_chat_may_be_cleared():
    assert rc.coerce(field("daily_report_chat_id"), "") is None


def test_numeric_bounds_enforced():
    with pytest.raises(rc.ConfigError, match="at most"):
        rc.coerce(field("daily_report_offset_days"), "99")
    with pytest.raises(rc.ConfigError, match="at least"):
        rc.coerce(field("ask_rate_per_hour"), "0")


def test_choice_must_be_known():
    assert rc.coerce(field("digest_style"), "compact") == "compact"
    with pytest.raises(rc.ConfigError):
        rc.coerce(field("digest_style"), "fancy")


def test_secrets_are_not_editable():
    """A localhost form that can rewrite the bot token turns into a credential store."""
    for secret in ("telegram_bot_token", "deepseek_api_key", "openai_api_key", "web_token"):
        assert secret not in rc.BY_KEY


# ── persistence ───────────────────────────────────────────────────────


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "PATH", tmp_path / "rc.json")
    rc.save({"daily_report_time": "20:00", "digest_style": "compact"})
    assert rc.load() == {"daily_report_time": "20:00", "digest_style": "compact"}


def test_unknown_keys_are_dropped_on_load(tmp_path, monkeypatch):
    path = tmp_path / "rc.json"
    path.write_text(json.dumps({"daily_report_time": "20:00", "telegram_bot_token": "leaked"}))
    monkeypatch.setattr(rc, "PATH", path)
    loaded = rc.load()
    assert "telegram_bot_token" not in loaded
    assert loaded["daily_report_time"] == "20:00"


def test_corrupt_file_does_not_stop_the_bot(tmp_path, monkeypatch):
    """`.env` alone is a working configuration — a broken overrides file must not
    be fatal at import time."""
    path = tmp_path / "rc.json"
    path.write_text("{not json")
    monkeypatch.setattr(rc, "PATH", path)
    assert rc.load() == {}


def test_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "PATH", tmp_path / "nope.json")
    assert rc.load() == {}


def test_save_is_atomic(tmp_path, monkeypatch):
    """A crash mid-write must not leave a half-written config behind."""
    monkeypatch.setattr(rc, "PATH", tmp_path / "rc.json")
    rc.save({"digest_style": "compact"})
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "rc.json").read_text())["digest_style"] == "compact"


# ── live application ──────────────────────────────────────────────────


def test_apply_overrides_invalidates_cached_properties():
    """settings caches parsed values in __dict__. Without clearing them the page
    reports success while the bot keeps using the old chat id."""
    from bot.config import Settings

    s = Settings(telegram_bot_token="x", daily_report_chat_id=-1, daily_report_time="09:00")
    assert s.report_chat_id == -1
    assert s.report_time.hour == 9

    s.apply_overrides({"daily_report_chat_id": -222, "daily_report_time": "19:30"})
    assert s.report_chat_id == -222
    assert (s.report_time.hour, s.report_time.minute) == (19, 30)


def test_apply_overrides_updates_derived_id_lists():
    from bot.config import Settings

    s = Settings(telegram_bot_token="x", telegram_muted_chats="")
    assert s.muted_chat_ids == frozenset()
    s.apply_overrides({"telegram_muted_chats": "-100111,-100222"})
    assert s.muted_chat_ids == frozenset({-100111, -100222})


def test_apply_overrides_ignores_unknown_keys():
    from bot.config import Settings

    s = Settings(telegram_bot_token="x")
    s.apply_overrides({"nonsense_key": 1})
    assert not hasattr(s, "nonsense_key")


def test_every_field_has_help_text():
    """This form is the operating manual for whoever runs the bot next."""
    for f in rc.FIELDS:
        assert f.help and len(f.help) > 20, f"{f.key} needs a real explanation"
        assert f.label and f.group


def test_hand_edited_out_of_range_value_is_dropped_not_fatal(tmp_path, monkeypatch):
    """The form validates before writing, but this file is hand-editable. An
    out-of-range value sails past the pydantic Field constraints (assignment is
    not validated) and crashes the bot at import — and with no bot running there
    is nothing left to report the error with."""
    path = tmp_path / "rc.json"
    path.write_text(json.dumps({
        "daily_report_offset_days": 999,      # Field allows 0-7
        "daily_report_time": "garbage",       # would raise inside report_time
        "digest_style": "compact",            # valid, must survive
    }))
    monkeypatch.setattr(rc, "PATH", path)

    loaded = rc.load()
    assert loaded == {"digest_style": "compact"}


def test_hand_edited_file_cannot_break_startup(tmp_path, monkeypatch):
    from bot.config import Settings
    path = tmp_path / "rc.json"
    path.write_text(json.dumps({"daily_report_time": "not a time"}))
    monkeypatch.setattr(rc, "PATH", path)

    s = Settings(telegram_bot_token="x", daily_report_time="19:00")
    s.apply_overrides(rc.load())
    assert s.report_time.hour == 19  # untouched, still usable
