CREATE TABLE imported_events (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 imported_ms INTEGER NOT NULL, actor_id TEXT NOT NULL, hidden_ms INTEGER,
 competitions TEXT NOT NULL
);
