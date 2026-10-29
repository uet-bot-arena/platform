CREATE TABLE IF NOT EXISTS graders(id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  created_ms INTEGER NOT NULL, created_by TEXT NOT NULL, revoked_ms INTEGER, platform TEXT, backend TEXT,
  runtime_id TEXT, slots INTEGER, version TEXT, hello_ms INTEGER, last_seen_ms INTEGER,
  jobs_done INTEGER NOT NULL DEFAULT 0);
ALTER TABLE graders ADD COLUMN engine_sha256 TEXT;
