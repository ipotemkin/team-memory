"""OpenAI-compatible adapters (OpenAI / ProxyAPI): Embedder and ChatLLM."""

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
