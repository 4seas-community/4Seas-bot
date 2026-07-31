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

# 拉丁字母/数字按词切；中日韩和泰文按字切。
# 中泰都没有词边界，逐字建 unigram 对 FAQ 这个量级的 BM25 够用，
# 也省掉 jieba / pythainlp 这类分词依赖。
# 漏掉泰文范围会让泰文提问切出 0 个 token，检索直接全空 —— 实测踩过。
_TOKEN_RE = re.compile(
    r"[a-zA-Z0-9]+"          # latin words / numbers
    r"|[一-鿿]"      # CJK
    r"|[฀-๿]"      # Thai
)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# 隐藏检索别名：`<!-- also: 怎么加入 如何加入 เข้าร่วม -->`
# 参与 BM25 索引，但不进喂给模型的正文。
_ALIAS_RE = re.compile(r"<!--\s*also:\s*(.*?)\s*-->", re.S | re.I)


@dataclass(slots=True)
class Passage:
    title: str
    body: str
    aliases: str = ""

    @property
    def text(self) -> str:
        """BM25 索引用的全文，含别名。"""
        return f"{self.title}\n{self.body}\n{self.aliases}"

    @property
    def prompt_text(self) -> str:
        """喂给模型的正文，不含别名 —— 别名是检索用的噪音词，会干扰生成。"""
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

        def flush() -> None:
            if title is None:
                return
            chunk = "\n".join(buf)
            aliases = " ".join(_ALIAS_RE.findall(chunk))
            body = _ALIAS_RE.sub("", chunk).strip()
            passages.append(Passage(title, body, aliases))

        for line in raw.splitlines():
            if line.startswith("## "):
                flush()
                title, buf = line[3:].strip(), []
            elif title is not None:
                buf.append(line)
        flush()

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
