"""进程级单例。集中在这里，方便 /reload 热更新和测试替换。"""

from __future__ import annotations

from .config import settings
from .services.events import event_service
from .services.kb import KnowledgeBase
from .services.keywords import KeywordRules
from .services.llm import llm_service
from .storage import Storage

storage = Storage(settings.db_path)
kb = KnowledgeBase()
keyword_rules = KeywordRules()

__all__ = ["storage", "kb", "keyword_rules", "event_service", "llm_service", "settings"]
