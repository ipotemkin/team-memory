"""Полная схема приложения: documents, snippets, реестр эмбеддингов, qa_runs, audit_runs.

SQL целиком в UPGRADE/DOWNGRADE (CHECK-инварианты, партиции аудита, функции HNSW-индекса и партиций).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = r"""
-- gen_random_uuid() is built in since PG13 (no pgcrypto needed)

CREATE TYPE app.document_status AS ENUM ('uploaded','indexing','ready','failed');
CREATE TYPE app.embedding_model_status AS ENUM ('indexing','ready','active','retired');

CREATE TABLE app.documents (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  title VARCHAR(200) NOT NULL CHECK (length(btrim(title)) > 0),
  text TEXT NULL CHECK (text IS NULL OR length(text) <= 100000),
  status app.document_status NOT NULL DEFAULT 'uploaded',
  status_error TEXT NULL,
  storage_bucket TEXT NULL,
  object_key TEXT NULL,
  sha256 CHAR(64) NULL,
  mime_type VARCHAR(127) NULL,
  size_bytes BIGINT NULL CHECK (size_bytes IS NULL OR size_bytes >= 0),
  content_sha256 CHAR(64) NOT NULL,
  indexed_at TIMESTAMPTZ NULL
);
CREATE INDEX ix_documents_created_at ON app.documents (created_at DESC);
CREATE INDEX ix_documents_status ON app.documents (status) WHERE status <> 'ready';
CREATE UNIQUE INDEX ux_documents_object_key ON app.documents (object_key) WHERE object_key IS NOT NULL;
CREATE INDEX ix_documents_content_sha ON app.documents (content_sha256);

CREATE TABLE app.snippets (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  document_id BIGINT NOT NULL REFERENCES app.documents(id) ON DELETE CASCADE,
  ord INT NOT NULL CHECK (ord >= 0),
  snippet_text TEXT NOT NULL,
  text_sha256 CHAR(64) NOT NULL,
  char_start INT NOT NULL CHECK (char_start >= 0),
  char_end INT NOT NULL CHECK (char_end >= char_start),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('russian', snippet_text)) STORED,
  UNIQUE (document_id, ord)
);
CREATE INDEX ix_snippets_document_id ON app.snippets (document_id);
CREATE INDEX ix_snippets_tsv ON app.snippets USING GIN (tsv);
CREATE INDEX ix_snippets_hash ON app.snippets (text_sha256);

CREATE TABLE app.embedding_models (
  name VARCHAR(200) PRIMARY KEY,
  provider VARCHAR(50) NOT NULL,
  dim INT NOT NULL CHECK (dim > 0 AND dim <= 4000),
  status app.embedding_model_status NOT NULL DEFAULT 'indexing',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ NULL,
  retired_at TIMESTAMPTZ NULL,
  progress_done BIGINT NOT NULL DEFAULT 0,
  progress_total BIGINT NOT NULL DEFAULT 0,
  est_tokens BIGINT NULL,
  eval_report JSONB NULL
);
CREATE UNIQUE INDEX ux_embedding_models_one_active ON app.embedding_models ((true)) WHERE status = 'active';

-- vector without fixed dimension; partial HNSW per model via app.hnsw_index_ddl()
CREATE TABLE app.snippet_embeddings (
  snippet_id BIGINT NOT NULL REFERENCES app.snippets(id) ON DELETE CASCADE,
  embedding_model VARCHAR(200) NOT NULL REFERENCES app.embedding_models(name),
  text_sha256 CHAR(64) NOT NULL,
  embedding vector NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snippet_id, embedding_model)
);
CREATE INDEX ix_snip_emb_model ON app.snippet_embeddings (embedding_model);

-- Returns the CREATE INDEX CONCURRENTLY statement (must run outside a transaction).
CREATE FUNCTION app.hnsw_index_ddl(p_model text) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v_dim int;
BEGIN
  SELECT dim INTO v_dim FROM app.embedding_models WHERE name = p_model;
  IF v_dim IS NULL THEN RAISE EXCEPTION 'unknown embedding model %', p_model; END IF;
  RETURN format(
    'CREATE INDEX CONCURRENTLY IF NOT EXISTS %I ON app.snippet_embeddings '
    'USING hnsw ((embedding::vector(%s)) vector_cosine_ops) WITH (m = 16, ef_construction = 64) '
    'WHERE embedding_model = %L',
    'ix_snip_emb_hnsw_' || substr(md5(p_model), 1, 12), v_dim, p_model);
END $$;

CREATE TABLE app.qa_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  question VARCHAR(2000) NOT NULL,
  answer TEXT NOT NULL,
  sources_json JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources_json) = 'array'),
  needs_review BOOLEAN NOT NULL,
  error TEXT NULL CHECK (error IS NULL OR error IN
    ('no_sources','low_confidence','unverified_quote','llm_invalid_json','llm_error')),
  confidence VARCHAR(10) NULL,
  chat_model VARCHAR(200) NULL,
  embedding_model VARCHAR(200) NULL,
  retrieval_mode VARCHAR(20) NOT NULL DEFAULT 'hybrid' CHECK (retrieval_mode IN ('hybrid','fts_only')),
  retrieved_ids BIGINT[] NOT NULL DEFAULT '{}',
  CONSTRAINT ck_reviewed_has_sources CHECK (needs_review OR jsonb_array_length(sources_json) > 0),
  CONSTRAINT ck_review_has_reason CHECK (NOT needs_review OR error IS NOT NULL)
);
CREATE INDEX ix_qa_runs_created_at ON app.qa_runs (created_at DESC);
CREATE INDEX ix_qa_runs_review ON app.qa_runs (created_at DESC) WHERE needs_review;
CREATE INDEX ix_qa_runs_emb_model ON app.qa_runs (embedding_model, created_at DESC);

CREATE TABLE app.audit_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  action VARCHAR(100) NOT NULL,
  input JSONB NULL,
  output JSONB NULL,
  status VARCHAR(20) NOT NULL CHECK (status IN ('ok','needs_review','client_error','error')),
  error TEXT NULL,
  duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
  request_id UUID NOT NULL DEFAULT gen_random_uuid(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_audit_created_at ON app.audit_runs (created_at DESC);
CREATE INDEX ix_audit_action ON app.audit_runs (action, created_at DESC);
CREATE INDEX ix_audit_status ON app.audit_runs (status) WHERE status <> 'ok';
CREATE TABLE app.audit_runs_default PARTITION OF app.audit_runs DEFAULT;

-- Creates monthly partitions from the current month up to p_months_ahead months ahead.
-- Rows already in the default partition for that range are moved into the new partition.
CREATE FUNCTION app.ensure_audit_partitions(p_months_ahead int DEFAULT 2) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
  v_start date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
  v_from date; v_to date; v_name text; v_created int := 0;
BEGIN
  FOR i IN 0..p_months_ahead LOOP
    v_from := (v_start + make_interval(months => i))::date;
    v_to := (v_from + interval '1 month')::date;
    v_name := 'audit_runs_' || to_char(v_from, 'YYYY_MM');
    IF to_regclass('app.' || v_name) IS NULL THEN
      EXECUTE format('CREATE TEMP TABLE _audit_move ON COMMIT DROP AS '
                     'SELECT * FROM app.audit_runs_default WHERE created_at >= %L AND created_at < %L',
                     v_from, v_to);
      EXECUTE format('DELETE FROM app.audit_runs_default WHERE created_at >= %L AND created_at < %L',
                     v_from, v_to);
      EXECUTE format('CREATE TABLE app.%I PARTITION OF app.audit_runs FOR VALUES FROM (%L) TO (%L)',
                     v_name, v_from, v_to);
      EXECUTE 'INSERT INTO app.audit_runs OVERRIDING SYSTEM VALUE SELECT * FROM _audit_move';
      DROP TABLE _audit_move;
      v_created := v_created + 1;
    END IF;
  END LOOP;
  RETURN v_created;
END $$;

SELECT app.ensure_audit_partitions(2);
"""

DOWNGRADE = r"""
DROP FUNCTION IF EXISTS app.ensure_audit_partitions(int);
DROP TABLE IF EXISTS app.audit_runs CASCADE;
DROP TABLE IF EXISTS app.qa_runs;
DROP FUNCTION IF EXISTS app.hnsw_index_ddl(text);
DROP TABLE IF EXISTS app.snippet_embeddings;
DROP TABLE IF EXISTS app.embedding_models;
DROP TABLE IF EXISTS app.snippets;
DROP TABLE IF EXISTS app.documents;
DROP TYPE IF EXISTS app.embedding_model_status;
DROP TYPE IF EXISTS app.document_status;
"""


def _statements(script: str) -> list[str]:
    """Делит скрипт по ';' в конце строки, не разрывая тела в $$...$$ (asyncpg выполняет по одному
    оператору).

    Строки-комментарии ``--`` вне тел функций отбрасываются.
    """
    out: list[str] = []
    buf: list[str] = []
    in_body = False
    for line in script.splitlines():
        if line.strip().startswith("--") and not in_body:
            continue
        buf.append(line)
        if line.count("$$") % 2:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.strip(";"):
                out.append(stmt)
            buf = []
    return out


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.execute(stmt)
