# Team Memory — заметки по этапам разработки

> Пошаговый журнал этапов 1–8. Актуальное полное описание проекта — в [README.md](README.md).


Сервис вопросов-ответов по базе знаний команды. Архитектура: гексагональная (ports & adapters).
На этапе 1 бизнес-логики нет — только запускаемый каркас.

## Что входит в этап 1
- Структура `backend/app/{domain,services,api,adapters/*,composition.py,config.py,main.py}` с docstring-описаниями слоёв.
- `backend/pyproject.toml` (uv), ruff, mypy (strict), import-linter.
- `config.py` на pydantic-settings, `.env.example`.
- FastAPI: `GET /health/live` и `GET /health` (проверка Postgres, MinIO, Redis; общий статус `ok`/`degraded`).
- Alembic (async env), миграция `0001`: `CREATE EXTENSION vector`, схема `app`.
- `docker-compose.yml`: postgres (pgvector), minio + minio-init (создание бакета), redis, api, worker (Celery, задача `ping`), web (Vite, вкладки «Документы», «Вопросы», «История»). Все образы поддерживают arm64.
- Makefile, GitHub Actions CI, unit-тесты.

## Правила зависимостей (import-linter)
- `domain` не импортирует `services`/`api`/`adapters`.
- `services` не импортируют `adapters`/`api`.
- Адаптеры подключаются только в `composition.py`.

## Запуск
```bash
cp .env.example .env      # make up сделает это сам
make up                   # поднять все сервисы
make migrate              # применить миграции
curl localhost:8000/health/live
curl localhost:8000/health
open http://localhost:5173  # веб-заглушка
```
Локальные проверки: `make lint`, `make typecheck`, `make test`, `make fmt`.

## Чек-лист приёмки этапа 1
- [ ] `make up` поднимает все контейнеры, healthcheck-и зелёные (`docker compose ps`).
- [ ] Бакет `documents` создан в MinIO (консоль http://localhost:9101).
- [ ] `make migrate` проходит; в БД есть расширение `vector` и схема `app`.
- [ ] `GET /health/live` → `{"status":"ok"}`.
- [ ] `GET /health` → `status: ok`, все компоненты `ok: true`; при остановке redis (`docker compose stop redis`) → `degraded`.
- [ ] Worker отвечает на `celery inspect ping`, задача `ping` возвращает `pong`.
- [ ] Веб-страница открывается, видны 3 вкладки.
- [ ] `make lint`, `make typecheck`, `make test` проходят; CI зелёный.


---

# Этап 2 — домен, порты, in-memory/stub адаптеры (без БД)

## Что входит в этап 2
- `app/domain/models.py` — модели Pydantic v2: `Document`, `NewDocument`, `Snippet`, `ScoredSnippet`, `Source{document_id, quote}`,
  `AskResult{answer, sources, needs_review}`, строгая `AiAnswer{answer, sources[{quote}], confidence high|medium|low, needs_review}`
  (`strict`, `extra=forbid`), `QaRun`, `AuditRecord`, `Page`, `StoredObject`, коды причин `ReviewReason`
  (`no_sources`, `low_confidence`, `unverified_quote`, `llm_invalid_json`, `llm_error`), текст `INSUFFICIENT_DATA`
  («Данных недостаточно для ответа. Вопрос передан на ручную проверку.»).
- Лимиты: title 1..200, text 1..100000 (strip), question 3..2000, context 1..20000, контекст для LLM ≈ 8000 символов, limit 1..200.
- `domain/chunking.py` — разбиение по абзацам (пустая строка), длинные абзацы — по предложениям до 1200 символов; текст дословный, `char_start/char_end`, `text_sha256`.
- `domain/guardrails.py` — `normalize` (регистр, ё→е, кавычки, тире, пробелы), `verify_quotes` (дословная подстрока после нормализации, ≥ 8 символов → `document_id`), `decide_review` + инварианты:
  `needs_review=false ⇒ sources непусты`, `пустые sources ⇒ needs_review=true`, `low confidence / неподтверждённая цитата ⇒ review`.
- `domain/ports.py` — `typing.Protocol`: `DocumentRepository`, `SnippetRepository`, `QaRunRepository`, `AuditSink`, `Retriever`, `FileStorage`, `Embedder`, `ChatLLM`, `TaskQueue`, `UnitOfWork`. `domain/errors.py`, `domain/prompts.py`.
- `services/`: `DocumentService` (add/list/get + chunking), `AskService` (retrieve → порог → ChatLLM → валидация → проверка цитат → решение → `qa_run` + аудит в одной UoW; при ошибке Embedder — `fts_only`), `AiAnswerService` (для `/ai/answer_with_sources`), `audit.py` (`AuditTimer` с `duration_ms`, fan-out во внешние sinks).
- `adapters/memory`: репозитории, UoW (снимок + commit/rollback), `AuditSink`, `FileStorage`, синхронная `InlineTaskQueue`, keyword-`Retriever` (оценки 0..1, только `ready`-документы).
- `adapters/stubs`: детерминированные `StubEmbedder` (hashing, L2) и `StubChatLLM` (выбирает лучшие предложения контекста как цитаты; режимы `normal`, `low_confidence`, `invalid_json`, `error`, `hallucinate`).
- `composition.py` — `build_container()` собирает memory/stub адаптеры при `DB_BACKEND=memory`, `STORAGE_BACKEND=memory`, `QUEUE_BACKEND=inline|memory`, `LLM_PROVIDER=stub`, `EMBEDDINGS_PROVIDER=stub`; остальные значения → `NotImplementedError` (этап 3). API-эндпоинты пока только health.
- `tests_data/kb_documents.jsonl`, `tests_data/kb_questions.jsonl` — тестовые данные.
- import-linter: добавлен контракт «domain и services не импортируют sqlalchemy/fastapi/boto3/redis/celery/haystack».

## Тесты
- `tests/unit` — chunking, guardrails, модели/лимиты, сервисы (3 сценария ТЗ и все причины review), composition.
- `tests/contract` — контрактные наборы для каждого порта, параметризованные по реализациям (`BACKENDS = ["memory"]` в `tests/contract/conftest.py`); на этапе 3 туда добавляется `"postgres"`.

Запуск:
```bash
make test                 # = cd backend && uv run pytest -q
make lint typecheck       # ruff + import-linter, mypy --strict
# без make:
cd backend
uv sync
uv run pytest -q
uv run pytest tests/contract -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
```

## Чек-лист приёмки этапа 2
- [ ] `uv run pytest -q` — все тесты зелёные, сеть и БД не нужны.
- [ ] `ruff`, `mypy --strict`, `lint-imports` (4 контракта) — без ошибок.
- [ ] Сценарий 1: добавленные документы видны в списке (новые первыми), разбиты на фрагменты, статус `ready`.
- [ ] Сценарий 2: вопрос из базы → ответ с `sources` (`document_id` + дословная цитата), `needs_review=false`.
- [ ] Сценарий 3: вопрос вне базы / low confidence / выдуманная цитата / невалидный JSON / ошибка LLM → «Данных недостаточно…», `needs_review=true`, причина в `qa_run.error` и `audit.error`.
- [ ] Инварианты `needs_review` соблюдаются (assert в `Decision`).
- [ ] Домен не зависит от адаптеров и инфраструктурных библиотек.

---

# Этап 3 — адаптеры хранения (PostgreSQL + pgvector, MinIO)

## Что входит в этап 3
- **Миграции Alembic (async)**: `0002_schema` создаёт в схеме `app` по DDL архитектуры:
  `documents` (enum `document_status`, поля MinIO `storage_bucket/object_key/sha256/mime_type/size_bytes`, `content_sha256`, `indexed_at`),
  `snippets` (generated `tsv` `to_tsvector('russian', …)` + GIN, `text_sha256`),
  `embedding_models` (реестр, уникальный индекс «только одна active»),
  `snippet_embeddings` (`vector` без фиксированной размерности, `embedding_model`, `text_sha256`),
  `qa_runs` (JSONB `sources_json`, CHECK-инварианты `ck_reviewed_has_sources` / `ck_review_has_reason`, коды причин, `embedding_model`, `retrieval_mode`),
  `audit_runs` — секционирование по месяцам (`created_at`) + `audit_runs_default`.
  Функции: `app.ensure_audit_partitions(months_ahead)` (создаёт секции на текущий месяц и вперёд, переносит строки из default-секции)
  и `app.hnsw_index_ddl(model)` (генерирует `CREATE INDEX CONCURRENTLY … USING hnsw ((embedding::vector(dim)))` для модели).
  `downgrade` полностью откатывает схему.
- **adapters/postgres**: ORM-модели SQLAlchemy 2.0 (`orm.py`), мапперы в домен (`mappers.py`), репозитории Document/QaRun (ORM) и Snippet (Core bulk insert),
  `PostgresAuditSink` (в транзакции UoW), `SqlAlchemyUnitOfWork` (qa_run + audit — одна транзакция),
  `PostgresFtsRetriever` (FTS `russian`; score = доля лексем вопроса в фрагменте, 0..1; только `ready`-документы), `admin.py` (HNSW-индекс, секции аудита).
- **adapters/s3**: `S3FileStorage` (boto3 в отдельном потоке, path-style для MinIO, presigned URL, `NotFound`).
- **adapters/celery_queue**: `CeleryTaskQueue` (`send_task`).
- **composition**: `DB_BACKEND=postgres_sqlalchemy` (или `postgres`) → Postgres, `STORAGE_BACKEND=s3` → MinIO, `QUEUE_BACKEND=celery` → Celery;
  unit-тесты по-прежнему используют memory/stub.
- **Тесты**: контрактные наборы параметризованы `memory` + `postgres` / `s3` (маркер `integration`, пропускаются без `TEST_DATABASE_URL` / `TEST_S3_ENDPOINT_URL`);
  изоляция — `TRUNCATE … RESTART IDENTITY` до и после каждого теста, для S3 — отдельный бакет на тест.
  `tests/integration/test_postgres_specific.py`: CHECK-инварианты БД, лимит текста, атомарность UoW, секции аудита, HNSW-хелпер, сквозной сценарий сервисов на Postgres.
- **Docker / Makefile**: сервис `api-test` (profile `test`, образ с dev-зависимостями), init-скрипт `docker/postgres/init/01-create-test-db.sql`, цель `make test-integration`
  (создаёт БД `kb_test`, если её нет, применяет миграции, запускает pytest внутри контейнера против compose-postgres/minio).
  MinIO в compose — `pgsty/minio` на портах хоста 9100/9101 (порт 9000 на Mac занят ClickHouse).
- CI: добавлена задача `integration-postgres` (pgvector/pgvector:pg16, миграции up/down/up, `pytest -m integration`).

## Как проверить
```bash
make test                 # unit + контракты memory (integration пропускаются)
make lint typecheck
make test-integration     # всё, включая postgres/s3, внутри контейнера api-test

# миграции вручную
make up && make migrate
docker compose run --rm api alembic downgrade base && make migrate

# локально без Docker-контейнера тестов (postgres и minio из compose на хосте):
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://kb:kb@localhost:5432/kb_test \
TEST_S3_ENDPOINT_URL=http://localhost:9100 uv run pytest -q
```

## Чек-лист приёмки этапа 3
- [ ] `alembic upgrade head` → `downgrade base` → `upgrade head` без ошибок; в `app` 6 таблиц + секции `audit_runs_YYYY_MM` и `audit_runs_default`.
- [ ] Вставка `qa_runs` с `needs_review=false` и пустыми `sources` отклоняется CHECK-ограничением.
- [ ] `make test` — зелёный без внешних сервисов; `make test-integration` — зелёный (postgres + s3 контракты и интеграционные тесты).
- [ ] Контракты одинаково проходят на memory и postgres (документы, фрагменты, эмбеддинги, qa_runs, аудит, UoW rollback, retriever).
- [ ] `S3FileStorage`: put/get/presign/delete на MinIO, `NotFound` для отсутствующего объекта.
- [ ] `DB_BACKEND=postgres_sqlalchemy`, `STORAGE_BACKEND=s3` собирают контейнер зависимостей; домен и сервисы не импортируют инфраструктуру (`lint-imports`).

## Этап 4 — HTTP API

Все эндпоинты описаны в OpenAPI: **http://localhost:8000/docs** (Swagger UI) и `/openapi.json`.
CORS открыт для `http://localhost:5173` (переменная `CORS_ORIGINS`).
По умолчанию работают `StubChatLLM`, `StubEmbedder` и FTS-ретривер на Postgres.

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/kb/documents` | JSON `{title,text}` → `{status:"ok",document_id}`; либо multipart `file` (.txt/.md, UTF-8) + `title` (необязательно) |
| GET | `/kb/documents?limit=&offset=` | список `{id,title,created_at,status,status_error}` (`status_error` — причина ошибки индексации или `null`) |
| GET | `/kb/documents/{id}?lines=N` | текст (или первые N строк), метаданные, `download_url` (presigned) для загруженного файла |
| POST | `/kb/ask` | `{question}` → `{answer,sources:[{document_id,quote}],needs_review}`; **всегда 200**, ошибки LLM → `needs_review=true` |
| POST | `/ai/answer_with_sources` | `{question,context}` → строго `{answer,sources:[{quote}],confidence,needs_review}`; ошибки LLM → **502** |
| GET | `/kb/qa_runs?needs_review=&limit=&offset=` | история вопросов (limit по умолчанию 50, максимум 100) |
| GET | `/kb/qa_runs/{id}` | один запуск, включая причину `error` (`no_sources`, `low_confidence`, `unverified_quote`, `llm_error`, `llm_invalid_json`) |

**Единый формат ошибок:** `{"error": "<code>", "detail": ...}`

| HTTP | error | когда |
|---|---|---|
| 400 | `bad_request` | битый JSON или JSON не-объект |
| 422 | `validation_error` | пустые или слишком длинные поля, лишние поля; `detail=[{field,msg}]` |
| 404 | `not_found` | нет документа или qa_run |
| 413 | `file_too_large` | файл больше `MAX_UPLOAD_MB` |
| 415 | `unsupported_media_type` | не txt/md, не UTF-8 или неподходящий Content-Type |
| 502 | `llm_error` / `llm_invalid_json` | только `/ai/answer_with_sources` |

Лимиты: title 1..200, text 1..100000, question 3..2000, context 1..20000.

**Аудит.** Каждая команда (POST) пишет ровно одну строку в `app.audit_runs` (`action`, `input`, `output`, `status`, `error`, `duration_ms`):
- успешные, `needs_review` и LLM-ошибки записывает сервис в той же транзакции;
- ошибки валидации, 404, 413/415 и 500 записывает ASGI-middleware `app/api/audit.py` (status `client_error` или `error`; тело запроса обрезается до `AUDIT_MAX_BODY_KB`, для multipart сохраняется только размер).

GET-запросы не аудируются.

### Примеры curl

```bash
# добавить документ
curl -s -X POST localhost:8000/kb/documents -H 'content-type: application/json' \
  -d '{"title":"Правила","text":"Ежедневный стендап проходит в 11:00."}'
# загрузить файл (оригинал сохраняется в MinIO, бакет documents)
curl -s -X POST localhost:8000/kb/documents -F title=FAQ -F file=@faq.md
# список и документ (первые 5 строк)
curl -s localhost:8000/kb/documents
curl -s 'localhost:8000/kb/documents/1?lines=5'
# вопрос
curl -s -X POST localhost:8000/kb/ask -H 'content-type: application/json' \
  -d '{"question":"Во сколько проходит стендап?"}'
# прямой вызов LLM
curl -s -X POST localhost:8000/ai/answer_with_sources -H 'content-type: application/json' \
  -d '{"question":"Когда стендап?","context":"Ежедневный стендап проходит в 11:00."}'
# очередь ручной проверки
curl -s 'localhost:8000/kb/qa_runs?needs_review=true&limit=20'
curl -s localhost:8000/kb/qa_runs/1
# ошибка валидации -> 422 и строка аудита
curl -s -X POST localhost:8000/kb/documents -H 'content-type: application/json' -d '{"title":"","text":""}'
docker compose exec -T postgres psql -U kb -d kb -c \
  "select action,status,error,duration_ms from app.audit_runs order by id desc limit 5"
```

### Smoke-тест

```bash
make up && make migrate
make smoke            # = ./scripts/smoke.sh (API_URL=http://localhost:8000 по умолчанию)
```

### Новые переменные `.env` (уже есть в `.env.example`)

- `CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173`
- `MAX_UPLOAD_MB=20`
- `AUDIT_MAX_BODY_KB=10`
- `DOWNLOAD_URL_TTL_S=900`
- `S3_PUBLIC_ENDPOINT_URL=http://localhost:9100` — теперь раскомментирована, чтобы ссылки `download_url` открывались с хоста.

Если `.env` у вас старый, допишите эти строки или пересоздайте его: `cp .env.example .env`.

### Чек-лист приёмки этапа 4

- [ ] `make lint typecheck test` зелёные: ruff, mypy --strict, lint-imports (4 контракта KEPT), pytest.
- [ ] `make test-integration` зелёный: включает `tests/acceptance` на Postgres и загрузку в MinIO.
- [ ] http://localhost:8000/docs открывается, в нём видны все 7 эндпоинтов.
- [ ] `make smoke` → `ALL SMOKE CHECKS PASSED`.
- [ ] Сценарий 1: документ добавлен и появился в `GET /kb/documents`.
- [ ] Сценарий 2: «Во сколько проходит стендап?» → `needs_review=false`, в `sources` есть цитата.
- [ ] Сценарий 3: «Какая зарплата у тимлида?» → `needs_review=true`; в `/kb/qa_runs/{id}` поле `error=no_sources`; в `audit_runs` есть строка.
- [ ] Пустой или слишком длинный `title`/`text` → 422 `validation_error`, а в `audit_runs` появляется строка `client_error`.
- [ ] Загрузка `.md` работает, а `download_url` скачивает оригинал. `.pdf` → 415, файл больше лимита → 413.
- [ ] Запрос из браузера с http://localhost:5173 проходит CORS.

## Этап 5 — OpenAI через ProxyAPI, гибридный поиск, фоновая индексация

### 1. Ключ и провайдер

1. Получите ключ на https://proxyapi.ru: «Ключи API» → «Создать», затем пополните баланс в рублях.
2. Пропишите ключ в `.env` в корне проекта. Файл `.env` в `.gitignore`, поэтому ключ не попадёт в git и в архивы:
   ```bash
   cp -n .env.example .env        # если .env ещё нет; если есть — допишите новые переменные из списка ниже
   # в .env:
   OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
   OPENAI_API_KEY=sk-...ваш-ключ...
   LLM_PROVIDER=openai_compat
   EMBEDDINGS_PROVIDER=openai_compat
   ```
3. Перезапустите стек: `make down && make up`.

Без ключа ставьте `LLM_PROVIDER=stub` и `EMBEDDINGS_PROVIDER=stub`: всё работает офлайн на заглушках. Тесты всегда используют заглушки. Если ключ не задан, а провайдер `openai_compat`, `/kb/ask` вернёт `needs_review=true` с причиной `llm_error`, а `/ai/answer_with_sources` — 502.

### 2. Модели и стоимость

| Назначение | Переменная | По умолчанию |
|---|---|---|
| Ответы | `CHAT_MODEL` (старое имя `LLM_MODEL` тоже читается) | `gpt-4o-mini` |
| Эмбеддинги | `EMBEDDINGS_MODEL`, `EMBEDDINGS_DIM` | `text-embedding-3-small`, `1536` |

- **ChatLLM** использует Structured Outputs: `response_format=json_schema`, `strict=true`, схема `AiAnswer`. Если модель или прокси не поддерживает `json_schema` и отвечает 400, адаптер один раз переключается на `json_object` и запоминает это (`OPENAI_STRUCTURED_OUTPUTS=auto`). Ответ в любом режиме проверяется строгой Pydantic-моделью; невалидный JSON даёт причину `llm_invalid_json`.
- **Ошибки:** таймауты, 429 и 5xx повторяются `OPENAI_MAX_RETRIES` раз с экспоненциальной паузой. Затем — `llm_error`. 401/400 не повторяются.
- **Промпт** на русском: отвечать только по контексту, цитировать дословно, при нехватке данных писать «Данных недостаточно…». Цитаты дополнительно сверяются с найденными фрагментами (guardrails): невыдуманность цитат не зависит от модели.
- **Стоимость** (ориентир; точные цены в рублях — в тарифах ProxyAPI, у них есть наценка к ценам OpenAI):
  - вопрос к `gpt-4o-mini` — примерно 1–2 тыс. токенов на вход и до 300 на выход, то есть доли копейки–копейки за вопрос;
  - индексация `text-embedding-3-small` — копейки за мегабайт текста.
  - Индексация 1000 документов среднего размера стоит рубли. Живые тесты (`make test-live`) тратят доли копейки.

### 3. Гибридный поиск (Postgres)

- **FTS** (`russian`): доля слов запроса, найденных во фрагменте.
- **Вектор:** pgvector, косинусная близость `1 - (embedding <=> q)`. Ищется только по **активной** модели из `app.embedding_models` и только по документам со `status='ready'`, через частичный HNSW-индекс модели.
- **Слияние:** Reciprocal Rank Fusion (`RRF_K=60`) задаёт порядок. Итоговый `score = max(fts, cosine)` лежит в 0..1, и к нему применяется `MIN_SCORE`.
- **Деградация:** если эмбеддер упал, поиск идёт только по FTS, а в `qa_runs.retrieval_mode` записывается `fts_only`.
- **Активная модель:** при старте API и worker модель из `EMBEDDINGS_MODEL` регистрируется, становится активной (если активной ещё нет), и для неё создаётся HNSW-индекс (`bootstrap_embedding_model`).
- **Смена модели** (например, после этапа со stub-эмбеддингами):
  ```bash
  docker compose exec -T postgres psql -U kb -d kb -c "UPDATE app.embedding_models SET status='retired', retired_at=now() WHERE status='active'"
  docker compose restart api worker
  docker compose exec worker celery -A app.adapters.celery_queue.worker:celery_app call reindex_all
  ```

**Перенастройка `MIN_SCORE`.** С настоящими эмбеддингами косинус релевантных фрагментов у `text-embedding-3-small` обычно 0.45–0.7, нерелевантных — 0.1–0.35. Значение 0.6 подобрано под FTS и заглушки, поэтому прогоните оценку:
```bash
make eval          # = python3 scripts/kb.py eval: загружает tests_data, задаёт вопросы, сравнивает с ожиданиями (см. этап 7)
```
Много ложных `needs_review` у отвечаемых вопросов — уменьшите `MIN_SCORE`, например до 0.45. Ответы на вопросы вне базы (q08) — увеличьте. Перезапустите `api`.

**Почему без Haystack.** Haystack 2.x разрешён архитектурой только внутри `adapters/rag_haystack`, и здесь он ничего не добавляет. Нарезка — 30 строк чистой доменной функции `chunk_paragraphs` с позициями символов и хешами. Поиск — один SQL-запрос по нашей схеме (частичные HNSW-индексы по моделям, фильтр `status='ready'`). `PgvectorDocumentStore` из Haystack навязывает свою таблицу и не знает про реестр моделей. Вызовы LLM — 150 строк на httpx с таймаутами, повторами и strict json_schema. Порты не менялись, так что адаптер `rag_haystack` можно добавить позже, не трогая сервисы.

### 4. Фоновая индексация (Celery)

- `POST /kb/documents` сразу отвечает `{"status":"ok","document_id":"42","document_status":"indexing"}`.
- Worker выполняет задачу `index_document`: нарезка → эмбеддинги пачками по `EMBED_BATCH_SIZE` → upsert → `status=ready`.
- При ошибке задача повторяется `INDEX_MAX_RETRIES` раз с паузой `INDEX_RETRY_BACKOFF_S·2ⁿ`. После последней попытки документ получает `status=failed`, причина видна в `GET /kb/documents/{id}` (`status_error`).
- Если брокер недоступен, документ сразу получает `failed`.
- С `QUEUE_BACKEND=inline` (тесты) индексация идёт синхронно, и ответ сразу содержит `ready`.
- Сервис `beat` раз в сутки запускает `ensure_audit_partitions` (партиции аудита на `AUDIT_PARTITIONS_MONTHS` вперёд).

**`document_id` теперь строка** во всех ответах: `POST /kb/documents`, списки, `sources` в `/kb/ask`, `qa_runs`. Нечисловой id даёт 404. В БД по-прежнему BIGINT; это внутренняя деталь, которую можно поменять на UUID без изменения контракта API.

### 5. Новые переменные `.env` (все есть в `.env.example`)

- `CHAT_MODEL=gpt-4o-mini` — заменяет `LLM_MODEL`; старое имя продолжает читаться.
- `EMBEDDINGS_DIM=1536`
- `OPENAI_TIMEOUT_S=30`
- `OPENAI_MAX_RETRIES=3`
- `OPENAI_STRUCTURED_OUTPUTS=auto`
- `CHAT_TEMPERATURE=0`
- `EMBED_BATCH_SIZE=64`
- `INDEX_MAX_RETRIES=3`
- `INDEX_RETRY_BACKOFF_S=10`
- `AUDIT_PARTITIONS_MONTHS=2`
- `RRF_K=60`

Изменены значения по умолчанию:
- `OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1`
- в `.env.example`: `LLM_PROVIDER=openai_compat`, `EMBEDDINGS_PROVIDER=openai_compat`.

### 6. Команды

```bash
make lint typecheck test        # заглушки, без сети
make test-integration           # Postgres + MinIO, включая гибридный поиск
make test-live                  # реальный ProxyAPI; пропускается, если OPENAI_API_KEY не задан
make smoke && make eval
docker compose logs -f worker   # видно index_document ... succeeded
```

### Чек-лист приёмки этапа 5

- [ ] `make lint typecheck test` и `make test-integration` зелёные.
- [ ] Ключ лежит только в `.env`; `git status` не показывает `.env`.
- [ ] `make test-live` с ключом проходит: эмбеддинги размерности 1536, ответ со сверенной цитатой.
- [ ] `docker compose ps` показывает работающие `worker` и `beat`.
- [ ] `POST /kb/documents` возвращает строковый `document_id` и `document_status=indexing`; через несколько секунд `GET /kb/documents/{id}` показывает `ready`.
- [ ] `select name,status from app.embedding_models` → `text-embedding-3-small | active`. В `pg_indexes` есть `ix_snip_emb_hnsw_*`, а `app.snippet_embeddings` заполнена.
- [ ] `make smoke` → `ALL SMOKE CHECKS PASSED` на реальной модели.
- [ ] `make eval` → все вопросы совпадают с ожиданиями (с этапа 7 — `10/10`). При необходимости подобран `MIN_SCORE`.
- [ ] Если остановить сеть или указать неверный ключ, `/kb/ask` отвечает 200 с `needs_review=true` (`llm_error`, в `qa_runs` `retrieval_mode=fts_only`), а `/ai/answer_with_sources` — 502.

## Этап 6 — веб-панель (web/)

React 18 + Vite 5 + TypeScript, интерфейс на русском, без UI-библиотек (свой небольшой CSS).
Адрес API — `VITE_API_URL` (по умолчанию `http://localhost:8000`); в compose задаётся сервису `web`
(можно переопределить в `.env`). CORS бэкенда по умолчанию разрешает `http://localhost:5173`.

Маршрутизация по hash:

| Адрес | Что открывается |
|---|---|
| `#/documents` | вкладка «Документы» |
| `#/documents/{id}` | карточка документа поверх текущей вкладки (deep link) |
| `#/ask` | вкладка «Вопросы» |
| `#/history`, `#/history/{id}` | «История» и карточка запуска |

Структура: `src/api/` — типизированный вручную клиент (`types.ts` по схемам API, `client.ts` с `ApiError`
/`NetworkError`), `src/components/` — вкладки, модалка документа, тосты, `src/test/` — vitest + RTL с мок-`fetch`.

Возможности:
- **Документы**: таблица (заголовок, дата в локальном формате, ID, бейдж статуса; для `failed` текст
  `status_error` во всплывающей подсказке), автообновление каждые 2 с, пока есть `indexing`; форма
  добавления с клиентской валидацией (заголовок 1..200 без «только пробелов», текст 1..100000) и
  показом ошибок 422 по полям; загрузка .txt/.md до 20 МБ (413/415 показываются у поля); просмотр
  первых 50 строк (`?lines=50`), «Показать всё», ссылка «Скачать оригинал» при наличии `download_url`.
- **Вопросы**: поле вопроса (3..2000), «Спросить» или Ctrl/Cmd+Enter, состояние загрузки, ответ,
  источники с цитатой и ссылкой на документ (открывает карточку документа), бейдж «требует проверки».
- **История**: таблица `qa_runs`, фильтр «требует проверки», лимит 50/100, «Обновить»; клик или Enter
  по строке открывает карточку: ответ, источники со ссылками, причина проверки по-русски
  (`no_sources`, `low_confidence`, `unverified_quote`, `llm_invalid_json`, `llm_error`), уверенность,
  модели, `retrieval_mode`.
- Ошибки сети и API показываются тостами (`role="alert"`); модалки закрываются по Esc; поля связаны с
  подписями и ошибками (`aria-invalid`, `aria-describedby`).

Проверки:

```bash
make web-install   # npm ci
make web-test      # lint + typecheck + test + build
cd web && npm run dev   # http://localhost:5173
```

В CI добавлена задача `web` (Node 20: `npm ci`, lint, typecheck, test, build).
Изменение бэкенда на этапе 6: в элементы `GET /kb/documents` добавлено поле `status_error`
(nullable), чтобы подсказка у бейджа «ошибка» бралась прямо из списка без доп. запросов.

### Чек-лист ручной приёмки этапа 6 (в браузере)

1. `make up`, дождаться healthy у `api` и `web`, открыть http://localhost:5173.
2. **Документы**
   - [ ] нажать «Добавить» с пустыми полями: под полями появляются ошибки, запрос не уходит;
   - [ ] добавить документ (заголовок + текст): тост «Документ #N добавлен», в таблице статус
     «индексируется» сменяется на «готов» без перезагрузки страницы;
   - [ ] загрузить .md-файл: документ появляется в таблице; попытка загрузить .pdf показывает ошибку у поля;
   - [ ] «Открыть документ»: справа панель с первыми 50 строками, «Показать всё» (для длинного
     документа), «Скачать оригинал» для загруженного файла скачивает файл;
   - [ ] скопировать адрес `#/documents/{id}`, открыть в новой вкладке: сразу открыта карточка.
3. **Вопросы**
   - [ ] ввести вопрос по добавленному документу и нажать Ctrl/Cmd+Enter: кнопка «Ищем ответ…»,
     затем ответ и список источников с цитатами;
   - [ ] ссылка «Документ #N» в источнике открывает карточку документа, после закрытия ответ на месте;
   - [ ] вопрос вне базы знаний: бейдж «требует проверки».
4. **История**
   - [ ] заданные вопросы видны в таблице; «только «требует проверки»» фильтрует; лимит 50/100;
     «Обновить» перезагружает;
   - [ ] клик по строке: карточка с ответом, источниками, причиной проверки по-русски,
     уверенностью, моделями и режимом поиска; адрес `#/history/{id}`.
5. [ ] Остановить `api` (`docker compose stop api`) и нажать «Обновить»: красный тост «Сервер недоступен».

## Этап 7 — тестовый датасет, seed/eval/acceptance

### Датасет `tests_data/`

- `kb_documents.jsonl` — 5 документов (`{"title","text"}`, по 30–35 строк, абзацы с подзаголовками —
  хорошо режутся на чанки): «Правила работы команды», «Частые вопросы клиентов», «Шаблоны ответов»,
  «Словарь терминов», «Процесс запуска задачи».
- `kb_questions.jsonl` — 10 вопросов: обязательные поля `question`, `expected_needs_review`, `why`;
  дополнительные для инструментов: `expected_document` и `expected_quote` (для отвечаемых),
  `expected_reason` (для вопросов вне базы; на stub-моделях — `no_sources`).
  Черновой набор (3 документа / 4 вопроса) удалён, тесты переведены на новые поля.

| № | Вопрос | expected_needs_review | Почему | Документ |
|---|---|---|---|---|
| 1 | Во сколько проходит ежедневный стендап? | `false` | Время стендапа прямо указано в правилах команды | Правила работы команды |
| 2 | Сколько ревьюеров проверяют каждый pull request? | `false` | Правило код-ревью: минимум два ревьюера | Правила работы команды |
| 3 | В течение скольких дней оформляется возврат средств? | `false` | В FAQ клиентов указан срок возврата — 14 календарных дней | Частые вопросы клиентов |
| 4 | Сколько действует ссылка для сброса пароля? | `false` | В FAQ указано: ссылка действует 24 часа | Частые вопросы клиентов |
| 5 | Какой шаблон отправить клиенту при задержке решения? | `false` | Есть готовый шаблон задержки | Шаблоны ответов |
| 6 | Для каких инцидентов пишется постмортем? | `false` | В словаре указано: постмортем пишется для инцидентов P1 и P2 | Словарь терминов |
| 7 | В какие дни проходит выкладка в продакшен? | `false` | В процессе запуска указаны дни выкладки: вторник и четверг | Процесс запуска задачи |
| 8 | Какая зарплата у старшего разработчика? | `true` | Зарплаты не описаны ни в одном документе базы | — (нет в базе) |
| 9 | Где можно припарковать машину у офиса? | `true` | Парковка у офиса в базе не упоминается | — (нет в базе) |
| 10 | Положена ли денежная компенсация за неиспользованные дни отпуска? | `true` | Компенсация отпуска в базе не описана | — (нет в базе) |

Ни один документ не упоминает зарплату, оклад, парковку, компенсацию или отпуск — это проверяет тест
`test_dataset_shape`.

### Команды

```bash
make seed                       # загрузить tests_data через API (идемпотентно по title) и дождаться ready
make eval                       # seed + 10 вопросов: needs_review vs ожидание, sources, документ, причина; exit 1 при расхождении
make acceptance CONFIRM=yes     # ОЧИСТИТЬ данные app → seed → ready → eval → проверка audit_runs → итог по 4 критериям
```

Без `CONFIRM=yes` `make acceptance` ничего не удаляет и завершается с кодом 2. Очищаются только таблицы
данных схемы `app` (`documents`, `snippets`, `snippet_embeddings`, `qa_runs`, `audit_runs`, счётчики id
сбрасываются); `embedding_models` и `alembic_version` остаются. Объекты в MinIO не удаляются.
Переменные: `API_URL` (по умолчанию `http://localhost:8000`), `PSQL` (по умолчанию
`docker compose exec -T postgres psql -U kb -d kb`), `SEED_WAIT_S` (180).

Пример вывода `make acceptance CONFIRM=yes` (stub-модели):

```
№   ожид.  needs_review  sources  док.  причина     мс  итог  вопрос
1   False  False         1        да    -           17  OK    Во сколько проходит ежедневный стендап?
...
8   True   True          0        -     no_sources  4   OK    Какая зарплата у старшего разработчика?
10/10 совпадают с ожиданиями

=== Критерии приёмки ===
PASS  1. Документы добавляются и видны в списке: 5/5 ready в списке
PASS  2. Ответ с источниками из нужного документа: 7/7
PASS  3. Вне базы -> needs_review + причина: 3/3; причины: no_sources, no_sources, no_sources
PASS  4. Каждая команда в audit_runs (+ причины): POST /kb/documents=5, POST /kb/ask=10, POST /ai/answer_with_sources=1
```

С реальными моделями причина для вопросов вне базы может быть другой (например, `low_confidence`):
`eval` требует лишь `needs_review=true`, пустые `sources` и непустую причину.

### Примеры curl

Ниже приведены ответы на stub-моделях. С реальными моделями текст `answer` будет другим.

```bash
curl -s -X POST localhost:8000/kb/documents -H 'content-type: application/json' \
  -d '{"title":"Памятка дежурного","text":"Дежурный реагирует на алерты в течение 15 минут.\n\nПередача дежурства — в понедельник в 12:00."}'
# {"status":"ok","document_id":"6","document_status":"indexing"}   (с Celery; inline-очередь сразу даёт "ready")

curl -s -X POST localhost:8000/kb/ask -H 'content-type: application/json' \
  -d '{"question":"Во сколько проходит ежедневный стендап?"}'
# {"answer":"Ежедневный стендап проходит в 11:00 в Google Meet и длится не дольше 15 минут.",
#  "sources":[{"document_id":"1","quote":"Ежедневный стендап проходит в 11:00 в Google Meet и длится не дольше 15 минут."}],
#  "needs_review":false}

curl -s -X POST localhost:8000/kb/ask -H 'content-type: application/json' \
  -d '{"question":"Какая зарплата у старшего разработчика?"}'
# {"answer":"Данных недостаточно для ответа. Вопрос передан на ручную проверку.","sources":[],"needs_review":true}

curl -s -X POST localhost:8000/ai/answer_with_sources -H 'content-type: application/json' \
  -d '{"question":"Когда проходит выкладка в продакшен?","context":"Выкладка в продакшен проходит по вторникам и четвергам в 14:00. Выкладка в пятницу запрещена, кроме исправления инцидентов P1."}'
# {"answer":"Выкладка в продакшен проходит по вторникам и четвергам в 14:00.",
#  "sources":[{"quote":"Выкладка в продакшен проходит по вторникам и четвергам в 14:00."}],
#  "confidence":"high","needs_review":false}
```

### Критерии приёмки и автоматические проверки

| Критерий | pytest (stub, детерминированно) | На живом стеке |
|---|---|---|
| 1. Документы добавляются и видны в списке | `tests/acceptance/test_acceptance_dataset.py::test_criterion1_documents_listed` | `make acceptance` → PASS 1 |
| 2. Вопрос из базы → ответ с источниками из нужного документа | `test_criterion2_answerable` (7 вопросов, цитаты дословны) | `make eval` строки 1–7, PASS 2 |
| 3. Вопрос вне базы → «Данных недостаточно…», `needs_review=true`, причина | `test_criterion3_out_of_base` (3 вопроса, `qa_runs.error=no_sources`) | `make eval` строки 8–10, PASS 3 |
| 4. Каждая команда в `audit_runs`, у отрицательных — причина | `test_criterion4_audit_every_command` | `make acceptance` → PASS 4 (SQL по `app.audit_runs`) |
| Датасет соответствует ТЗ (5 док., 20–50 строк; 7+3 вопроса; нет «запрещённых» тем) | `test_dataset_shape` | — |

Чек-лист:
- [ ] `cd backend && uv run pytest -q tests/acceptance` — зелёный (без БД 5 тестов + Postgres-сценарии пропускаются).
- [ ] `make up && make acceptance CONFIRM=yes` — 4 × PASS, `10/10`, код выхода 0.
- [ ] Повторный `make seed` пишет «добавлено 0».
- [ ] С реальными моделями (`LLM_PROVIDER=openai_compat`) `make eval` даёт `10/10`; если нет — подстроить `MIN_SCORE`.

## Этап 8 — эксплуатация: админ-API, ClickHouse, Ollama, логи/метрики, бэкапы

### 1. Админ-API `/admin`

Включается переменной `ADMIN_TOKEN` (если пусто — все `/admin/*` отвечают 404). Каждый запрос — с заголовком
`X-Admin-Token`; неверный/отсутствующий токен → 401 `{"error":"unauthorized"}`. Успешные команды пишутся в аудит
(`admin.register_model`, `admin.reindex`, `admin.activate`, `admin.delete_model`), ошибки — как и раньше, через middleware.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/admin/health` | проверки postgres, minio, redis, llm, embedder (+ clickhouse, если включён) |
| GET | `/admin/embedding-models` | реестр моделей эмбеддингов (`status`: indexing → ready → active → retired, при ошибке — failed; прогресс, `error`) |
| POST | `/admin/embedding-models` | регистрация `{"provider","name","dimension"}` → 201, статус `indexing` |
| POST | `/admin/embedding-models/{name}/reindex` | blue/green переиндексация задачей Celery `reindex_embedding_model` → 202 `{task_id}` |
| GET | `/admin/embedding-models/{name}/status` | прогресс `progress_done/progress_total`, `progress` 0..1 |
| POST | `/admin/embedding-models/{name}/activate` | доиндексация хвоста + проверка покрытия + атомарное переключение (одна транзакция) |
| DELETE | `/admin/embedding-models/{name}` | удалить эмбеддинги, HNSW-индекс и запись неактивной модели (активную — 409) |

**Ошибки переиндексации сохраняются** (миграция `0003`: колонка `app.embedding_models.error` и статус `failed`).
Если задача `reindex_embedding_model` падает (например, сервер эмбеддингов недоступен), модель получает
`status = failed`, а текст ошибки (`ТипИсключения: сообщение`, обрезается до 2000 символов) виден в
`GET /admin/embedding-models`, `GET …/{name}/status` и на вкладке «Админ» (бейдж `failed`, ошибка красным).
Повторный `reindex` разрешён из `failed` и очищает `error`; `activate` для `failed` → 409. Если падает
переиндексация уже активной модели, она остаётся `active` (поиск не ломается), но `error` заполняется.
При `QUEUE_BACKEND=inline` ошибка не превращается в 500 — `reindex` отвечает 202, статус — `failed`.

Как работает blue/green: старая (active) модель продолжает обслуживать поиск, пока воркер считает векторы новой
модели в `app.snippet_embeddings` (у каждой модели свой частичный HNSW-индекс своей размерности). Новые документы
во время переиндексации индексируются активной моделью, а `activate` перед переключением дозаполняет недостающее.
API и воркер подхватывают новую активную модель сами (кэш ~5 с), перезапуск не нужен. `qa_runs.embedding_model`
показывает, какой моделью получен каждый ответ.

```bash
export T=change-me            # = ADMIN_TOKEN из .env
curl -s -H "X-Admin-Token: $T" localhost:8000/admin/health | jq
# {"status":"ok","components":{"postgres":{"ok":true,...},"minio":{...},"redis":{...},"llm":{...},"embedder":{"ok":true,"detail":"text-embedding-3-small dim=1536"}}}

curl -s -H "X-Admin-Token: $T" localhost:8000/admin/embedding-models | jq

# зарегистрировать bge-m3 (Ollama, 1024)
curl -s -X POST -H "X-Admin-Token: $T" -H 'Content-Type: application/json' \
  -d '{"provider":"ollama","name":"bge-m3","dimension":1024}' localhost:8000/admin/embedding-models | jq

# запустить переиндексацию и следить за прогрессом
curl -s -X POST -H "X-Admin-Token: $T" localhost:8000/admin/embedding-models/bge-m3/reindex | jq
watch -n2 "curl -s -H 'X-Admin-Token: $T' localhost:8000/admin/embedding-models/bge-m3/status"

# когда status = ready — переключить поиск на новую модель
curl -s -X POST -H "X-Admin-Token: $T" localhost:8000/admin/embedding-models/bge-m3/activate | jq

# удалить данные старой модели (она стала retired)
curl -s -X DELETE -H "X-Admin-Token: $T" localhost:8000/admin/embedding-models/text-embedding-3-small | jq

# без токена
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/admin/health   # 401 (404, если ADMIN_TOKEN пуст)
```

`make admin-health`, `make admin-models` — то же с токеном из `.env`.

Веб-панель: вкладка **«Админ»** (`#/admin`) — ввод токена (хранится только в `sessionStorage` вкладки, «Выйти»
удаляет; при 401 токен сбрасывается), состояние сервисов, таблица моделей с прогрессом (опрос раз в 2 с, пока есть
`indexing`), кнопки «Переиндексировать», «Активировать», «Удалить» и форма регистрации модели.

### 2. Аудит в ClickHouse

`AUDIT_BACKEND=postgres` (по умолчанию) | `clickhouse` | `both`. Адаптер `app/adapters/clickhouse`
(clickhouse-connect, HTTP) реализует порт `AuditSink`; при `clickhouse` транзакционная запись аудита в Postgres
отключается (`NullAuditSink` в UoW), при `both` строки пишутся в обе базы. Сбой ClickHouse не ломает запросы
(внешние sink'и вызываются через `fan_out`), в `/admin/health` появляется компонент `clickhouse`.

```bash
docker compose --profile clickhouse up -d clickhouse   # порты хоста 8124 (HTTP) и 9002 (native); 9000 не занимаем
make ch-migrate      # DDL docker/clickhouse/init/001_audit.sql (выполняется и автоматически при первом старте)
# в .env: AUDIT_BACKEND=both CLICKHOUSE_HOST=clickhouse CLICKHOUSE_PORT=8123 CLICKHOUSE_USER=kb CLICKHOUSE_PASSWORD=kb
curl -s 'http://localhost:8124/?user=kb&password=kb' --data-binary \
  "SELECT action, status, count() FROM kb.audit_runs GROUP BY action, status"
make ch-test         # контрактные тесты sink'а против контейнера (без TEST_CLICKHOUSE_HOST они пропускаются)
```

Таблица `kb.audit_runs`: MergeTree, `PARTITION BY toYYYYMM(created_at)`, `ORDER BY (action, created_at)`, TTL 365 дней;
`input/output` — JSON-строки. API также создаёт таблицу при старте (`CREATE ... IF NOT EXISTS`).

### 3. Профиль Ollama

LLM и эмбеддинги идут через тот же OpenAI-совместимый адаптер, но на свои адреса: `LLM_BASE_URL`/`LLM_API_KEY`
и `EMBED_BASE_URL`/`EMBED_API_KEY` (пусто → `OPENAI_BASE_URL`/`OPENAI_API_KEY`).

```bash
make ollama-pull          # поднять контейнер ollama (профиль ollama) и скачать qwen2.5:7b + bge-m3
make ollama-env           # напечатает строки для .env (LLM_*, EMBED_*, CHAT_MODEL=qwen2.5:7b, ...)
make up-ollama
```

Ollama на хосте вместо контейнера: `LLM_BASE_URL=http://host.docker.internal:11434/v1` (в Linux добавьте
`extra_hosts: ["host.docker.internal:host-gateway"]`). Для ответов LLM рекомендуется `OPENAI_STRUCTURED_OUTPUTS=json_object`.
Ключ Ollama не проверяет, но клиент требует непустой — `LLM_API_KEY=ollama`, `EMBED_API_KEY=ollama`.

Размерность bge-m3 (1024) отличается от text-embedding-3-small (1536) — для существующей базы используйте реестр:
зарегистрируйте `{"provider":"ollama","name":"bge-m3","dimension":1024}`, затем reindex и activate (п. 1).
Для чистой базы можно сразу `EMBEDDINGS_MODEL=bge-m3 EMBEDDINGS_DIM=1024` — модель зарегистрируется при старте.
Тестам Ollama не нужен (проверяется только сборка адаптеров).

### 4. Логи, request id, метрики

* `LOG_FORMAT=json` (по умолчанию) — одна JSON-строка на событие: `ts, level, logger, msg, request_id, ...`;
  `text` — обычный формат. `LOG_LEVEL=INFO`.
* Middleware `X-Request-ID`: берёт входящий заголовок или генерирует, возвращает в ответе, пишет access-лог
  (`status`, `duration_ms`, `route`); если id — UUID, он же попадает в `request_id` строки аудита.
* `METRICS_ENABLED=true` → `GET /metrics` (Prometheus): `kb_http_requests_total{method,route,status}`,
  `kb_http_request_duration_seconds`.

### 5. Бэкап и восстановление

```bash
make backup                                               # backups/<YYYYmmdd-HHMMSS>/kb.dump + minio/documents + SHA256SUMS
make restore BACKUP=backups/20261104-010000 CONFIRM=yes   # стоп api/worker/beat, pg_restore --clean, mc mirror --remove
```

### 6. Новые переменные `.env` (есть в `.env.example`)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `ADMIN_TOKEN` | пусто (выключено) | токен админ-API |
| `AUDIT_BACKEND` | `postgres` | `postgres` / `clickhouse` / `both` |
| `CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE/TABLE/SECURE` | `localhost/8124/default/""/kb/audit_runs/false` | подключение к ClickHouse (HTTP) |
| `LLM_BASE_URL`, `LLM_API_KEY` | = `OPENAI_*` | сервер чата (Ollama: `http://ollama:11434/v1`) |
| `EMBED_BASE_URL`, `EMBED_API_KEY` | = `OPENAI_*` | сервер эмбеддингов |
| `REINDEX_BATCH_SIZE` | 64 | размер пачки при переиндексации |
| `LOG_FORMAT`, `LOG_LEVEL` | `json`, `INFO` | логирование |
| `METRICS_ENABLED` | `false` | `/metrics` |

### Чек-лист приёмки этапа 8

- [ ] без `ADMIN_TOKEN` `/admin/health` → 404; с токеном без заголовка → 401; с заголовком → 200 и все компоненты
- [ ] регистрация модели → reindex → прогресс 100% → activate → `/kb/ask` отвечает, `qa_runs.embedding_model` = новая модель
- [ ] удаление активной модели → 409, неактивной → 200, её векторы и HNSW-индекс удалены
- [ ] `AUDIT_BACKEND=both`: строки есть и в `kb.audit_runs` ClickHouse, и в `app.audit_runs`
- [ ] вкладка «Админ» работает, токен не переживает закрытие вкладки
- [ ] `make backup` / `make restore CONFIRM=yes` восстанавливают документы и файлы
- [ ] `make test lint typecheck web-test` зелёные
