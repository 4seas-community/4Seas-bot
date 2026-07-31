"""Digest copy: angle rotation, invite cadence, grounding guards.

The rotation rules are the whole point of this module — an LLM told to "be varied"
converges on the same three sentences within a week. These tests pin the mechanics
that make variety structural rather than hoped-for.
"""

import datetime as dt
import random

import pytest

from bot.models import Event
from bot.services.digest_writer import (
    CLOSING_ANGLES,
    INVITE_ANGLE,
    INVITE_MAX_GAP_DAYS,
    INVITE_MIN_GAP_DAYS,
    OPENING_ANGLES,
    _cap,
    _fallback_line,
    choose_angles,
)

BKK = "Asia/Bangkok"


def row(opening="scene", closing="see_you"):
    """Stand-in for a sqlite3.Row from digest_log."""
    return {"opening_angle": opening, "closing_angle": closing,
            "opening_text": "", "closing_text": ""}


def rng(seed=0):
    return random.Random(seed)


# ── angle rotation ────────────────────────────────────────────────────


def test_opening_angle_avoids_the_last_two_days():
    recent = [row(opening="scene"), row(opening="question")]
    for seed in range(30):
        opening, _, _ = choose_angles(dt.date(2026, 8, 1), recent, 1, rng(seed))
        assert opening not in {"scene", "question"}


def test_closing_angle_avoids_the_last_two_days():
    recent = [row(closing="see_you"), row(closing="curiosity")]
    for seed in range(30):
        _, closing, _ = choose_angles(dt.date(2026, 8, 1), recent, 1, rng(seed))
        assert closing not in {"see_you", "curiosity"}


def test_two_deep_memory_breaks_abab_alternation():
    """One-deep memory permits A/B/A/B, which reads as a pattern within a week."""
    recent = [row(opening="scene"), row(opening="question")]
    picks = {choose_angles(dt.date(2026, 8, 1), recent, 1, rng(s))[0] for s in range(50)}
    assert not (picks & {"scene", "question"})
    assert len(picks) > 1  # actually rotating, not just shifted to one other angle


def test_falls_back_gracefully_when_every_angle_is_recent():
    """Never crash just because history is dense — repeat rather than fail."""
    recent = [row(opening=a, closing=c) for a, c in zip(OPENING_ANGLES, CLOSING_ANGLES)]
    opening, closing, _ = choose_angles(dt.date(2026, 8, 1), recent, 1, rng())
    assert opening in OPENING_ANGLES and closing in CLOSING_ANGLES


def test_no_history_still_picks_valid_angles():
    opening, closing, _ = choose_angles(dt.date(2026, 8, 1), [], None, rng())
    assert opening in OPENING_ANGLES and closing in CLOSING_ANGLES


# ── invite cadence ────────────────────────────────────────────────────


def test_invite_never_fires_before_the_minimum_gap():
    """'Start your own event' as a daily sign-off is exactly what was asked against."""
    for days in range(INVITE_MIN_GAP_DAYS):
        for seed in range(25):
            _, closing, used = choose_angles(dt.date(2026, 8, 1), [], days, rng(seed))
            assert not used and closing != INVITE_ANGLE, f"fired after only {days} day(s)"


def test_invite_is_forced_once_past_the_maximum_gap():
    """Otherwise it can lose enough coin flips to vanish for weeks."""
    for seed in range(25):
        _, closing, used = choose_angles(
            dt.date(2026, 8, 1), [], INVITE_MAX_GAP_DAYS, rng(seed)
        )
        assert used and closing == INVITE_ANGLE


def test_invite_is_possible_but_not_certain_inside_the_window():
    outcomes = {
        choose_angles(dt.date(2026, 8, 1), [], INVITE_MIN_GAP_DAYS, rng(s))[2]
        for s in range(40)
    }
    assert outcomes == {True, False}


def test_invite_angle_is_never_picked_as_an_ordinary_closing():
    """It must only ever arrive through the cadence gate, never at random."""
    for seed in range(60):
        _, closing, used = choose_angles(dt.date(2026, 8, 1), [], 0, rng(seed))
        assert (closing == INVITE_ANGLE) == used


def test_simulated_fortnight_keeps_invite_within_cadence():
    """End-to-end on the rule that matters: never daily, never absent for long."""
    history: list[dict] = []
    last_invite: int | None = None
    fired_on: list[int] = []

    for day in range(14):
        since = None if last_invite is None else day - last_invite
        opening, closing, used = choose_angles(dt.date(2026, 8, 1), history, since, rng(day))
        if used:
            fired_on.append(day)
            last_invite = day
        history.insert(0, row(opening=opening, closing=closing))

    gaps = [b - a for a, b in zip(fired_on, fired_on[1:])]
    assert all(g >= INVITE_MIN_GAP_DAYS for g in gaps), f"fired too often: {fired_on}"
    assert all(g <= INVITE_MAX_GAP_DAYS for g in gaps), f"went quiet too long: {fired_on}"


# ── length guards ─────────────────────────────────────────────────────


def test_cap_leaves_short_text_alone():
    assert _cap("Short and sweet.", 100) == "Short and sweet."


def test_cap_prefers_a_sentence_boundary():
    text = "First sentence here. Second one runs on and on and on and on and on."
    out = _cap(text, 45)
    assert out.endswith(".") and "Second" not in out


def test_cap_falls_back_to_word_boundary():
    out = _cap("word " * 60, 40)
    assert len(out) <= 41 and not out.endswith("wor…")


def test_cap_collapses_whitespace():
    assert _cap("a\n\n  b\tc", 100) == "a b c"


# ── grounding fallback ────────────────────────────────────────────────


def make_event(content=None, title="Reading Club"):
    return Event(
        id="e1", title=title,
        start=dt.datetime(2026, 8, 1, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=7))),
        end=dt.datetime(2026, 8, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=7))),
        tz=BKK, content=content,
    )


def test_fallback_uses_the_organisers_first_sentence():
    ev = make_event("Bring a book or an e-reader and read quietly together. Tea provided.")
    assert _fallback_line(ev).startswith("Bring a book")
    assert "Tea provided" not in _fallback_line(ev)


def test_fallback_is_empty_when_there_is_no_description():
    """Silence beats a generated-sounding line that says nothing."""
    assert _fallback_line(make_event(None)) == ""
    assert _fallback_line(make_event("   ")) == ""


def test_fallback_never_invents_from_the_title_alone():
    ev = make_event(content=None, title="Something Very Specific")
    assert "Specific" not in _fallback_line(ev)


def test_fallback_truncates_a_wall_of_text():
    ev = make_event("no sentence breaks here " * 40)
    out = _fallback_line(ev)
    assert len(out) <= 170 and out.endswith("…")


# ── Markdown in organiser descriptions ────────────────────────────────

MD = (
    "# **Nimman Mini Hackathon #1**\n"
    "​**Theme: Innovation for a Livable Chiang Mai**\n\n"
    "## **​AI Practice Sharing**\n\n"
    "​It is a one-day event for students and anyone curious about AI. "
    "You will learn from industry experts and build a prototype.\n\n---\n"
)


def test_clean_content_strips_markdown_and_zero_width():
    """The no-LLM path sends this text straight to 776 people — raw Markdown
    like '# **Title** ​**Theme:' must never reach the group."""
    from bot.services.digest_writer import clean_content
    out = clean_content(MD)
    for junk in ("# ", "## ", "**", "---", "\u200b", "\n"):
        assert junk not in out, f"{junk!r} survived cleaning"
    # '#' inside "Hackathon #1" is content, not syntax — it must survive.
    assert "Hackathon #1" in out


def test_clean_content_handles_none_and_empty():
    from bot.services.digest_writer import clean_content
    assert clean_content(None) == "" and clean_content("   \n\n ") == ""


def test_fallback_skips_the_title_restated_as_a_heading():
    ev = make_event(content=MD, title="Nimman Mini Hackathon #1")
    line = _fallback_line(ev)
    assert not line.startswith("#") and "**" not in line
    assert "Nimman Mini Hackathon #1 Theme" not in line, "heading glued onto the prose"
    assert line.startswith("It is a one-day event")


def test_fallback_output_is_plain_prose():
    ev = make_event(content=MD)
    assert _fallback_line(ev) == " ".join(_fallback_line(ev).split())
