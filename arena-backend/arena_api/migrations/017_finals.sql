CREATE TABLE finals (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
 created_ms INTEGER NOT NULL, config TEXT NOT NULL, state TEXT NOT NULL
);
ALTER TABLE imported_events ADD COLUMN finals TEXT NOT NULL DEFAULT '[]';
UPDATE competitions SET config=json_set(config,'$.ranking','points_h2h_wins')
 WHERE json_extract(config,'$.format')='double_round_robin';
