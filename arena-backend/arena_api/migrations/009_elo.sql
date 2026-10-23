ALTER TABLE github_checks ADD COLUMN finished_ms INTEGER;
-- Elo has no earlier rounds to backdate: existing verdicts become available now.
UPDATE github_checks SET finished_ms=CAST(strftime('%s','now') AS INTEGER)*1000 WHERE state IN ('pass','fail');
CREATE TABLE elo_ratings(
 competition_id TEXT NOT NULL REFERENCES competitions(id),team_id TEXT NOT NULL REFERENCES teams(id),
 stage TEXT NOT NULL CHECK(stage IN ('ladder','official')),rating REAL NOT NULL,games INTEGER NOT NULL DEFAULT 0,
 change_remaining INTEGER NOT NULL DEFAULT 0,bot_hash TEXT,
 PRIMARY KEY(competition_id,team_id,stage));
CREATE TABLE elo_rounds(
 id TEXT PRIMARY KEY,competition_id TEXT NOT NULL REFERENCES competitions(id),phase TEXT NOT NULL CHECK(phase IN ('ladder','general','top','round_robin')),
 number INTEGER NOT NULL,started_ms INTEGER NOT NULL,completed_ms INTEGER,
 status TEXT NOT NULL CHECK(status IN ('running','completed','attention')),neighbor_ranks INTEGER NOT NULL,
 bye_team_id TEXT REFERENCES teams(id),config TEXT NOT NULL,snapshot TEXT NOT NULL,after_state TEXT,
 UNIQUE(competition_id,phase,number));
CREATE INDEX elo_round_history ON elo_rounds(competition_id,phase,number);
CREATE TABLE elo_games(
 id TEXT PRIMARY KEY,round_id TEXT NOT NULL REFERENCES elo_rounds(id),ordinal INTEGER NOT NULL,
 red_team_id TEXT NOT NULL REFERENCES teams(id),blue_team_id TEXT NOT NULL REFERENCES teams(id),
 red_sha TEXT NOT NULL,blue_sha TEXT NOT NULL,red_entry TEXT NOT NULL,blue_entry TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','queued','completed','attention')),
 current_job_id TEXT REFERENCES documents(id),result TEXT,auto_retries INTEGER NOT NULL DEFAULT 0,
 CHECK(red_team_id<>blue_team_id),UNIQUE(round_id,ordinal));
CREATE INDEX elo_dispatch ON elo_games(status,round_id,ordinal);
CREATE TABLE elo_attempts(
 game_id TEXT NOT NULL REFERENCES elo_games(id),number INTEGER NOT NULL,job_id TEXT NOT NULL UNIQUE REFERENCES documents(id),
 status TEXT NOT NULL CHECK(status IN ('queued','completed','attention')),error_code TEXT,created_ms INTEGER NOT NULL,
 PRIMARY KEY(game_id,number));
CREATE TABLE elo_calendar(competition_id TEXT PRIMARY KEY REFERENCES competitions(id),last_due_ms INTEGER NOT NULL);
