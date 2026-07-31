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

SYSTEM_PROMPT = """You are the assistant bot for the 4Seas community in Chiang Mai, Thailand.

## LANGUAGE — THIS OVERRIDES EVERYTHING ELSE
You write **only** in {language}. This is absolute and has no exceptions.

Questions arrive in Chinese, Thai, Russian, and other languages. Do not mirror them.
Read the question in whatever language it comes in, then write your entire answer
in {language}. Not one word in any other language.

If you catch yourself starting a sentence in the language of the question,
stop and rewrite it in {language}.

## Other rules
1. Answer ONLY from the REFERENCE material provided. If the answer isn't there,
   say you're not sure and suggest asking an admin in the group. Never guess, never invent.
2. Keep it short — 3 sentences max, unless explicitly asked for detail.
3. Don't say "according to the reference material" — just give the answer.
4. Plain text only, no Markdown."""

NO_ANSWER = "I'm not sure about that one — best to ask an admin here in the group 🙋"


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

        context = "\n\n".join(f"### {p.prompt_text}" for p in passages)

        # 没配模型时降级：直接回最相关的一条 FAQ 原文，好过什么都不给
        if not self.providers:
            return passages[0].prompt_text.strip()

        lang = settings.reply_language
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(language=lang)},
            {
                "role": "user",
                # 语言要求在 user 消息末尾再钉一次。只写在 system 里时，DeepSeek 对
                # 中文提问仍会用中文回答 —— 实测过。末尾复述是最后一道保险。
                "content": (
                    f"REFERENCE:\n{context}\n\nQUESTION:\n{question}\n\n"
                    f"(Write your answer in {lang}, no matter what language the question used.)"
                ),
            },
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

        log.error("all LLM providers unavailable: %s", last_exc)
        return (
            "(AI is down right now — here's the raw FAQ entry)\n\n"
            + passages[0].prompt_text.strip()
        )


llm_service = LLMService()
