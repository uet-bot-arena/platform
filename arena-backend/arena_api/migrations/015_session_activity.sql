ALTER TABLE sessions ADD COLUMN last_seen_ms INTEGER NOT NULL DEFAULT 0;
-- Previously issued cookies have a fixed fourteen-day lifetime.
DELETE FROM sessions;
