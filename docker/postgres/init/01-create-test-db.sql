-- Выполняется только на новом томе pgdata; `make test-integration` тоже создаёт kb_test, если её нет.
CREATE DATABASE kb_test OWNER kb;
\connect kb_test
CREATE EXTENSION IF NOT EXISTS vector;
