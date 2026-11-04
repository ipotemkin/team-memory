"""Сборка контейнера (composition root): memory end-to-end, подключение адаптеров по настройкам."""

import pytest

from app.adapters.celery_queue.queue import CeleryTaskQueue
from app.adapters.openai_compat import OpenAICompatChatLLM, OpenAICompatEmbedder
from app.adapters.postgres import PostgresFtsRetriever, SqlAlchemyUnitOfWork
from app.adapters.s3 import S3FileStorage
from app.composition import build_container
from app.config import Settings


def mem(**kw: str) -> Settings:
    base = dict(
        DB_BACKEND="memory",
        STORAGE_BACKEND="memory",
        QUEUE_BACKEND="inline",
        LLM_PROVIDER="stub",
        EMBEDDINGS_PROVIDER="stub",
        AUDIT_SINKS="db,memory",
    )
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


async def test_memory_container_end_to_end() -> None:
    c = build_container(mem())
    await c.document_service().add("Правила", "Ежедневный стендап проходит в 11:00.")
    res = await c.ask_service().ask("Во сколько проходит стендап?")
    assert res.needs_review is False and res.sources[0].document_id == 1
    assert (await c.document_service().get(1)).status == "ready"
    assert len(c.extra_audit_sinks) == 1


def test_openai_compat_wiring() -> None:
    c = build_container(
        mem(LLM_PROVIDER="openai_compat", EMBEDDINGS_PROVIDER="openai_compat", CHAT_MODEL="gpt-4o-mini")
    )
    assert isinstance(c.chat, OpenAICompatChatLLM) and c.chat.model_name == "gpt-4o-mini"
    assert isinstance(c.embedder, OpenAICompatEmbedder) and c.embedder.dim == 1536
    assert c.embedder.model_name == "text-embedding-3-small"


def test_legacy_llm_model_env_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-mini")
    assert Settings(_env_file=None).CHAT_MODEL == "gpt-4.1-mini"  # type: ignore[call-arg]


def test_unknown_audit_sink() -> None:
    with pytest.raises(NotImplementedError):
        build_container(mem(AUDIT_SINKS="kafka"))


def test_postgres_and_s3_wiring_without_connecting() -> None:
    c = build_container(mem(DB_BACKEND="postgres_sqlalchemy", STORAGE_BACKEND="s3", QUEUE_BACKEND="celery"))
    assert isinstance(c.uow_factory(), SqlAlchemyUnitOfWork)
    assert isinstance(c.retriever, PostgresFtsRetriever)
    assert isinstance(c.storage, S3FileStorage)
    assert isinstance(c.queue, CeleryTaskQueue)
    assert set(c.maintenance) == {"ensure_audit_partitions", "bootstrap_embedding_model", "reindex_all"}
    assert len(c.startup) == 1


def test_settings_are_hermetic_in_tests() -> None:
    s = Settings()
    assert s.LLM_PROVIDER == "stub" and s.EMBEDDINGS_PROVIDER == "stub" and s.OPENAI_API_KEY == ""
