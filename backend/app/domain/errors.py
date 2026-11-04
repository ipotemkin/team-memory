"""Доменные исключения.

Сервисы и адаптеры бросают их, а слой API (``api/errors.py``) переводит в HTTP-коды:
NotFound → 404, Conflict → 409, UnsupportedFile → 415, ExtractionFailed → 422,
LLMError / LLMInvalidJSON → ответ с ``needs_review=true`` (или 502 у ``/ai``).
"""


class DomainError(Exception):
    """Базовый класс всех доменных ошибок."""


class NotFound(DomainError):
    """Сущность (документ, запуск Q&A, модель эмбеддингов) не найдена."""

    pass


class LLMError(DomainError):
    """LLM недоступна: таймаут, ответ 5xx, нет ключа API."""


class LLMInvalidJSON(DomainError):
    """Ответ LLM не прошёл проверку JSON-схемы / Pydantic-модели ``AiAnswer``."""


class Conflict(DomainError):
    """Операция противоречит текущему состоянию (например, активация не до конца проиндексированной
    модели).
    """


class UnsupportedFile(DomainError):
    """Тип загруженного файла не поддерживается/не разрешён или содержимое не совпадает с заявленным типом."""


class ExtractionFailed(DomainError):
    """Тип файла верный, но текст извлечь не удалось (битый файл, скан PDF без текстового слоя и т.п.)."""
