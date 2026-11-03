"""Domain exceptions."""


class DomainError(Exception):
    """Base domain error."""


class NotFound(DomainError):
    pass


class LLMError(DomainError):
    """LLM unavailable: timeout, 5xx, missing key."""


class LLMInvalidJSON(DomainError):
    """LLM answer failed JSON schema / Pydantic validation."""


class Conflict(DomainError):
    """Operation conflicts with the current state (e.g. activating a model that is not fully indexed)."""
