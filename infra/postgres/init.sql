-- Runs once on first container start. Migrations also create these idempotently.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS vault;

-- Separate database for the test suite.
CREATE DATABASE rci_test OWNER rci;
\connect rci_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS vault;
