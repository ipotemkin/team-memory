# Токен админки берётся из .env (можно переопределить: make admin-health ADMIN_TOKEN=...).
ADMIN_TOKEN ?= $(shell sed -n 's/^ADMIN_TOKEN=//p' .env 2>/dev/null)
API ?= http://localhost:8000

.PHONY: psql ch-client ch-up ch-migrate ch-test ollama-up ollama-pull ollama-env up-ollama backup restore admin-health admin-models seed acceptance web-install web-test smoke eval test-live up down logs migrate test test-integration test-db lint fmt typecheck env
# Префикс для команд бэкенда (uv-окружение в backend/).
BE = cd backend &&

# --- Стек docker compose: .env из шаблона, запуск/остановка, логи, миграции ---
env:
	@test -f .env || cp .env.example .env
up: env
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f --tail=100
migrate:
	docker compose run --rm api alembic upgrade head
# --- Бэкенд: тесты (герметичные), линтеры + import-linter, автоформат, mypy ---
test:
	$(BE) uv run pytest -q
lint:
	$(BE) uv run ruff check . && uv run ruff format --check . && uv run lint-imports
fmt:
	$(BE) uv run ruff format . && uv run ruff check --fix .
typecheck:
	$(BE) uv run mypy
# --- Интеграционные тесты: Postgres + MinIO из compose, база kb_test, контейнер api-test ---
test-db: env
	docker compose up -d --wait postgres minio
	docker compose up minio-init
	docker compose exec -T postgres psql -U kb -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='kb_test'" | grep -q 1 \
	  || docker compose exec -T postgres createdb -U kb -O kb kb_test
test-integration: test-db
	docker compose --profile test build api-test
	docker compose --profile test run --rm api-test

# --- Проверки против запущенного API: smoke-сценарии ТЗ, загрузка датасета, оценка ответов ---
smoke:
	./scripts/smoke.sh

seed:
	python3 scripts/kb.py seed

eval:
	python3 scripts/kb.py eval

# Очищает данные схемы app (документы, qa_runs, audit_runs)! Нужен CONFIRM=yes.
acceptance:
	python3 scripts/kb.py acceptance $(if $(filter yes,$(CONFIRM)),--yes,)

# Живые тесты против реального провайдера (нужен OPENAI_API_KEY, стоит доли цента).
test-live:
	$(BE) uv run pytest -m live -q

# --- Веб-клиент: установка зависимостей; lint + typecheck + vitest + сборка ---
web-install:
	cd web && npm ci

web-test:
	cd web && npm run lint && npm run typecheck && npm run test && npm run build

# --- Этап 8: приёмник аудита ClickHouse (запуск, создание таблицы, контрактный тест) ---
ch-up: env
	docker compose --profile clickhouse up -d --wait clickhouse
ch-migrate:
	docker compose --profile clickhouse exec -T clickhouse clickhouse-client -u kb --password kb --multiquery < docker/clickhouse/init/001_audit.sql
ch-test: ch-up
	$(BE) TEST_CLICKHOUSE_HOST=localhost TEST_CLICKHOUSE_PORT=8124 TEST_CLICKHOUSE_USER=kb TEST_CLICKHOUSE_PASSWORD=kb \
	  uv run pytest -q tests/contract -k audit_sink

# --- Этап 8: Ollama (LLM и эмбеддинги через OpenAI-совместимый API); ollama-env печатает строки для .env ---
OLLAMA_CHAT_MODEL ?= qwen2.5:7b
OLLAMA_EMBED_MODEL ?= bge-m3
ollama-up: env
	docker compose --profile ollama up -d --wait ollama
ollama-pull: ollama-up
	docker compose --profile ollama exec -T ollama ollama pull $(OLLAMA_CHAT_MODEL)
	docker compose --profile ollama exec -T ollama ollama pull $(OLLAMA_EMBED_MODEL)
ollama-env:
	@echo "LLM_PROVIDER=openai_compat"; echo "EMBEDDINGS_PROVIDER=openai_compat"
	@echo "LLM_BASE_URL=http://ollama:11434/v1"; echo "EMBED_BASE_URL=http://ollama:11434/v1"
	@echo "LLM_API_KEY=ollama"; echo "EMBED_API_KEY=ollama"
	@echo "CHAT_MODEL=$(OLLAMA_CHAT_MODEL)"; echo "OPENAI_STRUCTURED_OUTPUTS=json_object"
	@echo "# новая модель эмбеддингов регистрируется через /admin (см. README-steps.md, этап 8)"
up-ollama: ollama-pull
	docker compose --profile ollama up -d --build

# --- Этап 8: админка и эксплуатация (health, реестр моделей, бэкап/восстановление) ---
admin-health:
	curl -s -H "X-Admin-Token: $(ADMIN_TOKEN)" $(API)/admin/health; echo
admin-models:
	curl -s -H "X-Admin-Token: $(ADMIN_TOKEN)" $(API)/admin/embedding-models; echo
# Дамп Postgres + зеркало MinIO → backups/<timestamp>/ (BACKUP_DIR=... — другой каталог)
backup:
	./scripts/backup.sh
# Восстановление из BACKUP=backups/<timestamp>; разрушительно → нужен CONFIRM=yes
restore:
	@test "$(CONFIRM)" = "yes" || (echo "restore overwrites the database and bucket: run with CONFIRM=yes BACKUP=backups/<ts>"; exit 1)
	./scripts/restore.sh "$(BACKUP)"

# Интерактивная консоль Postgres (схема app)
psql:
	docker compose exec -e PGOPTIONS="-c search_path=app,public" postgres psql -U kb -d kb

# Интерактивная консоль ClickHouse (база kb)
ch-client:
	docker compose --profile clickhouse exec clickhouse clickhouse-client -u kb --password kb -d kb
