"""Адаптеры для OpenAI-совместимых API (OpenAI через ProxyAPI, Ollama, vLLM): Embedder и ChatLLM.

Включаются LLM_PROVIDER/EMBEDDINGS_PROVIDER = openai | openai_compat; сборка — в ``composition.py``.
"""

from app.adapters.openai_compat.chat import AI_ANSWER_SCHEMA, OpenAICompatChatLLM, parse_answer
from app.adapters.openai_compat.client import OpenAICompatClient
from app.adapters.openai_compat.embedder import OpenAICompatEmbedder

__all__ = [
    "AI_ANSWER_SCHEMA",
    "OpenAICompatChatLLM",
    "OpenAICompatClient",
    "OpenAICompatEmbedder",
    "parse_answer",
]
