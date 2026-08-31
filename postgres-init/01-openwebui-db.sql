-- Open-WebUI gets its OWN database on the shared Postgres instance (it owns its
-- own schema + migrations; do not mix it with the `inference` database).
-- This runs only on a fresh data volume. For an existing deployment, run:
--   CREATE DATABASE openwebui OWNER inference;
SELECT 'CREATE DATABASE openwebui OWNER inference'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'openwebui')\gexec
