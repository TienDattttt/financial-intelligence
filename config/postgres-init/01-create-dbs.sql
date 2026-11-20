-- Initialize isolated Tier 1 PostgreSQL databases for FinIntel V2:
--   1. ducklake_catalog: DuckLake catalog metadata (replaces single-writer SQLite)
--   2. dagster_state:    Dagster run/event/schedule storage (replaces SQLite Dagster home)
SELECT 'CREATE DATABASE ducklake_catalog'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake_catalog')\gexec

SELECT 'CREATE DATABASE dagster_state'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster_state')\gexec
