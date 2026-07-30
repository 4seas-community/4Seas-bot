"""问答：DeepSeek 为主，OpenAI 兜底。两者都走 OpenAI 兼容接口。

约束模型只能基于检索到的 FAQ 片段回答 —— 社区 bot 编造答案的代价比答不上来高得多。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from ..config import settings
from .kb import Passage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 4Seas 社区（泰国清迈）的助手机器人。

规则（严格遵守）：
1. 只能依据【参考资料】回答。资料里没有的，直接说"这个我不确定，建议在群里 @ 管理员问一下"，不要猜、不要编。
2. 回答简短，控制在 3 句话以内，除非用户明确要求详细。
3. 用提问者使用的语言回答（中文提问就用中文，英文就用英文，泰文就用泰文）。
4. 不要说"根据参考资料"这类废话，直接给答案。
5. 不使用 Markdown 语法，输出纯文本。"""

NO_ANSWER = "这个我不确定，建议在群里 @ 管理员问一下 🙋"


@dataclass(slots=True)
class Provider:
    name: str
    client: AsyncOpenAI
    model: str


class LLMService:
    def __init__(self) -> None:
        self.providers: list[Provider] = []
        if settings.deepseek_api_key:
            self.providers.append(
                Provider(
                    "deepseek",
                    AsyncOpenAI(
                        api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        timeout=30.0,
                    ),
                    settings.deepseek_model,
                )
            )
        if settings.openai_api_key:
            self.providers.append(
                Provider(
                    "openai",
                    AsyncOpenAI(
                        api_key=settings.openai_api_key,
                        base_url=settings.openai_base_url,
                        timeout=30.0,
                    ),
                    settings.openai_model,
                )
            )
        if not self.providers:
            log.warning("没有配置任何 LLM 密钥，问答将只返回 FAQ 原文")

    @property
    def available(self) -> bool:
        return bool(self.providers)

    async def answer(self, question: str, passages: list[Passage]) -> str:
        if not passages:
            return NO_ANSWER

        context = "\n\n".join(f"### {p.title}\n{p.body}" for p in passages)

        # 没配模型时降级：直接回最相关的一条 FAQ 原文，好过什么都不给
        if not self.providers:
            top = passages[0]
            return f"{top.title}\n{top.body}".strip()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"【参考资料】\n{context}\n\n【问题】\n{question}"},
        ]

        last_exc: Exception | None = None
        for provider in self.providers:
            try:
                resp = await provider.client.chat.completions.create(
                    model=provider.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.3,
                    max_tokens=400,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    log.info("问答由 %s 应答", provider.name)
                    return text
                last_exc = RuntimeError(f"{provider.name} 返回空内容")
            except Exception as exc:
                log.warning("LLM %s 调用失败：%s", provider.name, exc)
                last_exc = exc

        log.error("所有 LLM 均不可用：%s", last_exc)
        top = passages[0]
        return f"（AI 暂时不可用，先给你 FAQ 原文）\n\n{top.title}\n{top.body}".strip()


llm_service = LLMService()
