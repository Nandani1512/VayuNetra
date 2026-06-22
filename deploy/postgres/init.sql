-- Enables PostGIS + TimescaleDB on the main DB and creates a sister
-- 'prefect' DB used by the prefect-server container.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- Separate DB for Prefect server state.
SELECT 'CREATE DATABASE prefect'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prefect')\gexec
