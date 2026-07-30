"""FAQ 知识库：Markdown → 分段 → BM25 检索。

不用向量库。社区 FAQ 通常几十条,BM25(纯 Python,无外部服务)的召回质量足够,
省掉 embedding 调用和向量库运维。条目超过 ~200 条再考虑升级。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

log = logging.getLogger(__name__)

# 中文按字切、英文按词切 —— 不引入分词器依赖，对 FAQ 这个量级够用
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[一-鿿]")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True)
class Passage:
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


class KnowledgeBase:
    def __init__(self, path: str | Path = "data/faq.md") -> None:
        self.path = Path(path)
        self.passages: list[Passage] = []
        self._bm25: BM25Okapi | None = None
        self.load()

    def load(self) -> int:
        """加载并切分 FAQ。返回条目数。可被 /reload 重复调用。"""
        if not self.path.exists():
            log.warning("FAQ 文件不存在：%s", self.path)
            self.passages, self._bm25 = [], None
            return 0

        raw = self.path.read_text(encoding="utf-8")
        passages: list[Passage] = []
        title, buf = None, []
        for line in raw.splitlines():
            if line.startswith("## "):
                if title:
                    passages.append(Passage(title, "\n".join(buf).strip()))
                title, buf = line[3:].strip(), []
            elif title:
                buf.append(line)
        if title:
            passages.append(Passage(title, "\n".join(buf).strip()))

        self.passages = [p for p in passages if p.body or p.title]
        self._bm25 = (
            BM25Okapi([tokenize(p.text) for p in self.passages]) if self.passages else None
        )
        log.info("FAQ 加载完成：%d 条", len(self.passages))
        return len(self.passages)

    def search(self, query: str, top_k: int = 3) -> list[Passage]:
        if not self._bm25 or not self.passages:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(scores, self.passages), key=lambda x: x[0], reverse=True)
        # 分数 <= 0 说明一个词都没命中，宁可不给上下文也不要喂噪音
        return [p for score, p in ranked[:top_k] if score > 0]

    def titles(self) -> list[str]:
        return [p.title for p in self.passages]
