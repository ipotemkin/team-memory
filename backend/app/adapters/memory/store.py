"""Shared in-memory state (one per backend instance)."""

import copy
from dataclasses import dataclass, field

from app.domain.models import AuditRecord, Document, EmbeddingModelInfo, QaRun, Snippet


@dataclass
class MemoryState:
    documents: dict[int, Document] = field(default_factory=dict)
    snippets: dict[int, Snippet] = field(default_factory=dict)
    embeddings: dict[tuple[int, str], tuple[str, list[float]]] = field(default_factory=dict)
    qa_runs: dict[int, QaRun] = field(default_factory=dict)
    audit: list[AuditRecord] = field(default_factory=list)
    embedding_models: dict[str, EmbeddingModelInfo] = field(default_factory=dict)
    seq: dict[str, int] = field(default_factory=dict)

    def next_id(self, name: str) -> int:
        self.seq[name] = self.seq.get(name, 0) + 1
        return self.seq[name]

    def clone(self) -> "MemoryState":
        return copy.deepcopy(self)


class MemoryStore:
    """Holder of committed state; UoW works on a copy and swaps it in on commit."""

    def __init__(self) -> None:
        self.state = MemoryState()
