"""FAQ retrieval: tokenisation and hidden multilingual aliases.

The bot answers in English but members ask in Chinese and Thai. BM25 is purely
lexical, so a Chinese question cannot match English prose on its own — the
`<!-- also: ... -->` aliases are what bridge that gap. These tests pin that down.
"""

import pytest

from bot.services.kb import KnowledgeBase, tokenize

FAQ = """# FAQ

## How do I join the community
<!-- also: 怎么加入 如何加入 报名 เข้าร่วม -->

Events are open — pick one on Social Layer and show up.

## Where do events take place
<!-- also: 地点 场地 สถานที่ -->

Mostly around Nimman in Chiang Mai.

## No aliases here

Plain entry with no alias line.
"""


@pytest.fixture
def kb(tmp_path):
    path = tmp_path / "faq.md"
    path.write_text(FAQ, encoding="utf-8")
    return KnowledgeBase(path)


# ── tokenisation ──────────────────────────────────────────────────────


def test_latin_tokenised_as_words():
    assert tokenize("How do I JOIN") == ["how", "do", "i", "join"]


def test_cjk_tokenised_per_character():
    assert tokenize("怎么加入") == ["怎", "么", "加", "入"]


def test_thai_is_tokenised():
    """Missing the Thai range yields zero tokens and silently breaks all Thai search."""
    assert len(tokenize("เข้าร่วม")) > 0


def test_mixed_script_query():
    assert tokenize("4Seas 是什么 คืออะไร")[:2] == ["4seas", "是"]


def test_punctuation_dropped():
    assert tokenize("怎么加入社区？！") == list("怎么加入社区")


# ── parsing ───────────────────────────────────────────────────────────


def test_all_entries_loaded(kb):
    assert len(kb.passages) == 3
    assert kb.titles()[0] == "How do I join the community"


def test_aliases_indexed_but_not_in_prompt(kb):
    p = kb.passages[0]
    assert "怎么加入" in p.aliases
    assert "怎么加入" in p.text          # BM25 sees it
    assert "怎么加入" not in p.prompt_text  # the model does not
    assert "<!--" not in p.prompt_text


def test_entry_without_aliases_still_parses(kb):
    plain = kb.passages[2]
    assert plain.aliases == ""
    assert "Plain entry" in plain.prompt_text


# ── retrieval ─────────────────────────────────────────────────────────


def test_chinese_query_finds_english_entry(kb):
    hits = kb.search("怎么加入社区", top_k=3)
    assert hits and hits[0].title == "How do I join the community"


def test_thai_query_finds_english_entry(kb):
    hits = kb.search("เข้าร่วม", top_k=3)
    assert hits and hits[0].title == "How do I join the community"


def test_english_query_still_works(kb):
    hits = kb.search("how do I join", top_k=3)
    assert hits and hits[0].title == "How do I join the community"


def test_unrelated_query_returns_nothing(kb):
    """Zero-score hits must be dropped — feeding noise to the model invites invention."""
    assert kb.search("quantum chromodynamics", top_k=3) == []


def test_reload_picks_up_edits(kb, tmp_path):
    (tmp_path / "faq.md").write_text(FAQ + "\n## Visas\n<!-- also: 签证 -->\n\nAsk in the group.\n")
    assert kb.load() == 4
    assert kb.search("签证", top_k=1)[0].title == "Visas"


def test_missing_file_is_not_fatal(tmp_path):
    kb = KnowledgeBase(tmp_path / "nope.md")
    assert kb.passages == [] and kb.search("anything") == []
