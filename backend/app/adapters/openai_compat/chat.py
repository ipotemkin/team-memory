"""ChatLLM поверх ``/chat/completions`` со Structured Outputs (строгая ``json_schema``).

Если сервер/модель не принимает json_schema (HTTP 400 с упоминанием response_format), один раз
переходим на режим ``json_object`` и запоминаем это; ответ в любом случае проверяется строгой
моделью AiAnswer, так что формат ответа гарантирован не сервером, а нами.
"""

import json
import logging
from typing import Any, Literal

from pydantic import ValidationError

from app.adapters.openai_compat.client import ApiStatusError, OpenAICompatClient
from app.domain.errors import LLMError, LLMInvalidJSON
from app.domain.models import AiAnswer
from app.domain.prompts import SYSTEM_PROMPT

log = logging.getLogger("app.openai_compat")

# JSON-схема ответа для Structured Outputs (strict): все поля обязательны, лишние запрещены.
AI_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "sources", "confidence", "needs_review"],
    "properties": {
        "answer": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["quote"],
                "properties": {"quote": {"type": "string", "description": "дословная цитата из контекста"}},
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "needs_review": {"type": "boolean"},
    },
}

# Подсказка о формате для режима json_object (там схема серверу не передаётся).
JSON_OBJECT_HINT = (
    '\nФормат ответа — один JSON-объект: {"answer": str, "sources": [{"quote": str}], '
    '"confidence": "high"|"medium"|"low", "needs_review": bool}. Никаких других полей.'
)

Mode = Literal["auto", "json_schema", "json_object"]


def user_prompt(question: str, context: str) -> str:
    """Пользовательское сообщение: контекст в «рамке» <<< >>> и вопрос."""
    return f"Контекст:\n<<<\n{context}\n>>>\n\nВопрос: {question}"


class OpenAICompatChatLLM:
    """Порт ChatLLM для OpenAI-совместимого сервера.

    Args:
        client: HTTP-клиент с ретраями и таймаутами.
        model: имя чат-модели (CHAT_MODEL).
        mode: auto — json_schema с откатом на json_object; либо принудительно один из режимов
            (OPENAI_STRUCTURED_OUTPUTS, для Ollama — json_object).
        temperature: 0 — максимально детерминированные ответы.
    """

    def __init__(
        self, client: OpenAICompatClient, model: str, mode: Mode = "auto", temperature: float = 0.0
    ) -> None:
        self._c = client
        self.model_name = model
        self._mode: Mode = mode
        self._temperature = temperature

    def _payload(
        self, question: str, context: str, fmt: Literal["json_schema", "json_object"]
    ) -> dict[str, Any]:
        """Тело запроса chat/completions для выбранного формата ответа."""
        system = SYSTEM_PROMPT + (JSON_OBJECT_HINT if fmt == "json_object" else "")
        rf: dict[str, Any] = (
            {
                "type": "json_schema",
                "json_schema": {"name": "ai_answer", "strict": True, "schema": AI_ANSWER_SCHEMA},
            }
            if fmt == "json_schema"
            else {"type": "json_object"}
        )
        return {
            "model": self.model_name,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt(question, context)},
            ],
            "response_format": rf,
        }

    async def answer_with_sources(self, question: str, context: str) -> AiAnswer:
        """Задаёт вопрос модели и возвращает проверенный ``AiAnswer``.

        Raises:
            LLMError: сеть/таймаут/HTTP-ошибка (после ретраев клиента).
            LLMInvalidJSON: ответ не соответствует схеме или модель отказалась отвечать.
        """
        fmt: Literal["json_schema", "json_object"] = (
            "json_object" if self._mode == "json_object" else "json_schema"
        )
        try:
            data = await self._c.post("chat/completions", self._payload(question, context, fmt))
        except ApiStatusError as e:
            # Откатываемся на json_object только в режиме auto и только если ошибка про response_format.
            unsupported = e.status == 400 and ("response_format" in e.body or "json_schema" in e.body)
            if not (unsupported and fmt == "json_schema" and self._mode == "auto"):
                raise
            log.warning("json_schema not supported by %s, falling back to json_object", self.model_name)
            self._mode = "json_object"
            data = await self._c.post("chat/completions", self._payload(question, context, "json_object"))
        return parse_answer(data)


def parse_answer(data: dict[str, Any]) -> AiAnswer:
    """Достаёт JSON из ответа chat/completions и валидирует его моделью ``AiAnswer``.

    Raises:
        LLMError: структура ответа не похожа на chat completion.
        LLMInvalidJSON: отказ модели (refusal), пустой контент, невалидный JSON или схема.
    """
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError("malformed chat completion response") from e
    if msg.get("refusal"):
        raise LLMInvalidJSON(f"model refused: {msg['refusal']}")
    content = msg.get("content")
    if not isinstance(content, str):
        raise LLMInvalidJSON("empty content")
    try:
        return AiAnswer.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMInvalidJSON(str(e)[:300]) from e
