"""关键词规则：data/keywords.yaml → 编译后的正则规则。

每条规则强制带冷却。776 人的群里,一个 administrator 身份的 bot 刷屏
是最容易翻车的失败模式,所以 cooldown 不做成可选项。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class KeywordRule:
    id: str
    pattern: re.Pattern[str]
    reply: str
    cooldown: int
    match_terms: list[str]


class KeywordRules:
    def __init__(self, path: str | Path = "data/keywords.yaml") -> None:
        self.path = Path(path)
        self.rules: list[KeywordRule] = []
        self.load()

    def load(self) -> int:
        if not self.path.exists():
            log.warning("关键词文件不存在：%s", self.path)
            self.rules = []
            return 0

        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
        rules: list[KeywordRule] = []
        for i, item in enumerate(raw):
            rule_id = str(item.get("id") or f"rule-{i}")
            terms = [t for t in (item.get("match") or []) if t]
            reply = (item.get("reply") or "").strip()
            if not terms or not reply:
                log.warning("关键词规则 %s 缺少 match 或 reply，跳过", rule_id)
                continue
            # 英文按单词边界匹配，避免 "visa" 命中 "visable"；中文没有词边界概念
            parts = []
            for t in terms:
                escaped = re.escape(t)
                parts.append(rf"\b{escaped}\b" if t.isascii() else escaped)
            try:
                pattern = re.compile("|".join(parts), re.IGNORECASE)
            except re.error as exc:
                log.warning("关键词规则 %s 正则编译失败：%s", rule_id, exc)
                continue
            rules.append(
                KeywordRule(
                    id=rule_id,
                    pattern=pattern,
                    reply=reply,
                    cooldown=int(item.get("cooldown", settings.keyword_default_cooldown)),
                    match_terms=terms,
                )
            )

        self.rules = rules
        log.info("关键词规则加载完成：%d 条", len(rules))
        return len(rules)

    def match(self, text: str) -> KeywordRule | None:
        """返回第一条命中的规则。一条消息最多触发一次,不叠加。"""
        for rule in self.rules:
            if rule.pattern.search(text):
                return rule
        return None
