-- Initialize isolated Tier 1 PostgreSQL databases for FinIntel V2 (B-05):
--   1. ducklake_catalog: DuckLake catalog metadata (replaces single-writer SQLite)
--   2. dagster_state:    Dagster run/event/schedule storage (replaces SQLite Dagster home)
--   3. finintel_rag:     Grounded PDF RAG corpus with pgvector + unaccent extensions
--   4. finintel_app:     Identity, RBAC, PDPL consents, workspace, and immutable audit logs
SELECT 'CREATE DATABASE ducklake_catalog'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake_catalog')\gexec

SELECT 'CREATE DATABASE dagster_state'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster_state')\gexec

SELECT 'CREATE DATABASE finintel_rag'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'finintel_rag')\gexec

SELECT 'CREATE DATABASE finintel_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'finintel_app')\gexec

\c finintel_rag
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
