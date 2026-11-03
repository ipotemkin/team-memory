"""ChatLLM over /chat/completions with Structured Outputs (strict json_schema).

If the endpoint/model rejects json_schema (HTTP 400 mentioning response_format), we fall back once to
``json_object`` mode and remember it; the answer is always validated with the strict AiAnswer model.
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

JSON_OBJECT_HINT = (
    '\nФормат ответа — один JSON-объект: {"answer": str, "sources": [{"quote": str}], '
    '"confidence": "high"|"medium"|"low", "needs_review": bool}. Никаких других полей.'
)

Mode = Literal["auto", "json_schema", "json_object"]


def user_prompt(question: str, context: str) -> str:
    return f"Контекст:\n<<<\n{context}\n>>>\n\nВопрос: {question}"


class OpenAICompatChatLLM:
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
        fmt: Literal["json_schema", "json_object"] = (
            "json_object" if self._mode == "json_object" else "json_schema"
        )
        try:
            data = await self._c.post("chat/completions", self._payload(question, context, fmt))
        except ApiStatusError as e:
            unsupported = e.status == 400 and ("response_format" in e.body or "json_schema" in e.body)
            if not (unsupported and fmt == "json_schema" and self._mode == "auto"):
                raise
            log.warning("json_schema not supported by %s, falling back to json_object", self.model_name)
            self._mode = "json_object"
            data = await self._c.post("chat/completions", self._payload(question, context, "json_object"))
        return parse_answer(data)


def parse_answer(data: dict[str, Any]) -> AiAnswer:
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
