"""Общие фикстуры: датасет базы знаний и герметичные настройки (окружение и .env не влияют на тесты)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import AliasChoices

from app.config import Settings, get_settings

DATA = Path(__file__).resolve().parents[2] / "tests_data"


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.fixture(scope="session")
def kb_documents() -> list[dict[str, Any]]:
    return _jsonl("kb_documents.jsonl")


@pytest.fixture(scope="session")
def kb_questions() -> list[dict[str, Any]]:
    return _jsonl("kb_questions.jsonl")


def _settings_env_names() -> set[str]:
    names: set[str] = set()
    for name, f in Settings.model_fields.items():
        names.add(name)
        if isinstance(f.validation_alias, AliasChoices):
            names.update(a for a in f.validation_alias.choices if isinstance(a, str))
        elif isinstance(f.validation_alias, str):
            names.add(f.validation_alias)
    return names


SETTINGS_ENV = frozenset(_settings_env_names())


@pytest.fixture(autouse=True)
def hermetic_settings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Tests never see the developer's .env or exported app settings (TEST_* vars are kept).

    Live tests (marker ``live``) keep the real environment: they need OPENAI_API_KEY and read .env explicitly.
    """
    if request.node.get_closest_marker("live") is None:
        for name in SETTINGS_ENV:
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)
        monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
