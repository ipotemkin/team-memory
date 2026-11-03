-- Runs only on a fresh pgdata volume; `make test-integration` also creates kb_test if missing.
CREATE DATABASE kb_test OWNER kb;
\connect kb_test
CREATE EXTENSION IF NOT EXISTS vector;
