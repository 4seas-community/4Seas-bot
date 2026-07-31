"""Config-driven commands: validation and hot-reload.

The invariant that matters: a broken config file must never take down commands
that were working. Errors get reported, everything valid still loads.
"""

import pytest

from bot.services.custom_commands import RESERVED, CustomCommandRegistry


@pytest.fixture
def commands_dir(tmp_path):
    d = tmp_path / "commands"
    d.mkdir()
    return d


def write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


def test_loads_a_single_command(commands_dir):
    write(commands_dir, "wifi.yaml", """
- command: wifi
  description: Venue Wi-Fi
  reply: "Network: 4Seas-Guest"
""")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.ok
    assert [c.command for c in r.commands] == ["wifi"]
    assert r.commands[0].description == "Venue Wi-Fi"


def test_bare_mapping_without_list_works(commands_dir):
    write(commands_dir, "one.yaml", "command: solo\nreply: hi\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert [c.command for c in r.commands] == ["solo"]


def test_leading_slash_and_case_are_normalised(commands_dir):
    write(commands_dir, "a.yaml", "- command: /WiFi\n  reply: x\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands[0].command == "wifi"


def test_multiple_files_merge(commands_dir):
    write(commands_dir, "a.yaml", "- command: alpha\n  reply: a\n")
    write(commands_dir, "b.yml", "- command: beta\n  reply: b\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert sorted(c.command for c in r.commands) == ["alpha", "beta"]


def test_disabled_command_is_parsed_but_not_registered(commands_dir):
    write(commands_dir, "a.yaml", "- command: draft\n  reply: x\n  enabled: false\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands == [] and r.skipped == 1
    assert r.ok  # disabled is not an error


# ── validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(RESERVED))
def test_builtin_names_are_rejected(commands_dir, name):
    """Overriding /reload with a broken config would remove the only way to fix it."""
    write(commands_dir, "a.yaml", f"- command: {name}\n  reply: hijacked\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands == []
    assert any("built-in" in e for e in r.errors)


@pytest.mark.parametrize("bad", ["has space", "UPPER!", "wi-fi", "x" * 33, "emoji🎉"])
def test_invalid_telegram_names_rejected(commands_dir, bad):
    """One bad name makes setMyCommands reject the whole batch — blanking the menu."""
    write(commands_dir, "a.yaml", f'- command: "{bad}"\n  reply: x\n')
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands == [] and r.errors


def test_missing_reply_rejected(commands_dir):
    write(commands_dir, "a.yaml", "- command: empty\n  description: nothing\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands == []
    assert any("no `reply`" in e for e in r.errors)


def test_duplicate_across_files_reported(commands_dir):
    write(commands_dir, "a.yaml", "- command: dup\n  reply: first\n")
    write(commands_dir, "b.yaml", "- command: dup\n  reply: second\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert len(r.commands) == 1
    assert r.commands[0].reply == "first"  # first file wins
    assert any("already defined" in e for e in r.errors)


def test_unknown_scope_rejected(commands_dir):
    write(commands_dir, "a.yaml", "- command: x\n  reply: y\n  scope: everywhere\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert r.commands == [] and any("scope" in e for e in r.errors)


# ── resilience ────────────────────────────────────────────────────────


def test_broken_yaml_does_not_kill_other_files(commands_dir):
    """The whole point: one bad file must not take down working commands."""
    write(commands_dir, "good.yaml", "- command: good\n  reply: fine\n")
    write(commands_dir, "broken.yaml", "- command: [unclosed\n  reply: nope\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert [c.command for c in r.commands] == ["good"]
    assert any("broken.yaml" in e for e in r.errors)


def test_one_bad_entry_does_not_kill_its_siblings(commands_dir):
    write(commands_dir, "a.yaml", """
- command: ok1
  reply: a
- command: reload
  reply: hijack
- command: ok2
  reply: b
""")
    r = CustomCommandRegistry(commands_dir).load()
    assert sorted(c.command for c in r.commands) == ["ok1", "ok2"]
    assert len(r.errors) == 1


def test_empty_file_ignored(commands_dir):
    write(commands_dir, "empty.yaml", "")
    write(commands_dir, "a.yaml", "- command: real\n  reply: x\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert [c.command for c in r.commands] == ["real"]
    assert r.ok


def test_missing_directory_is_not_fatal(tmp_path):
    r = CustomCommandRegistry(tmp_path / "nope").load()
    assert r.commands == [] and r.ok


def test_non_yaml_files_ignored(commands_dir):
    write(commands_dir, "README.md", "# not a command\n")
    write(commands_dir, "a.yaml", "- command: real\n  reply: x\n")
    r = CustomCommandRegistry(commands_dir).load()
    assert [c.command for c in r.commands] == ["real"]


# ── reload semantics ──────────────────────────────────────────────────


def test_reload_picks_up_new_file(commands_dir):
    write(commands_dir, "a.yaml", "- command: first\n  reply: x\n")
    reg = CustomCommandRegistry(commands_dir)
    reg.load()
    write(commands_dir, "b.yaml", "- command: second\n  reply: y\n")
    r = reg.load()
    assert sorted(c.command for c in r.commands) == ["first", "second"]


def test_reload_drops_deleted_file(commands_dir):
    write(commands_dir, "a.yaml", "- command: gone\n  reply: x\n")
    reg = CustomCommandRegistry(commands_dir)
    assert len(reg.load().commands) == 1
    (commands_dir / "a.yaml").unlink()
    assert reg.load().commands == []
    assert reg.commands == []


def test_reload_is_idempotent(commands_dir):
    write(commands_dir, "a.yaml", "- command: stable\n  reply: x\n")
    reg = CustomCommandRegistry(commands_dir)
    first = [c.command for c in reg.load().commands]
    for _ in range(5):
        assert [c.command for c in reg.load().commands] == first


def test_shipped_config_files_are_valid():
    """The examples in data/commands/ must actually parse."""
    r = CustomCommandRegistry("data/commands").load()
    assert r.ok, f"shipped command configs have errors: {r.errors}"
