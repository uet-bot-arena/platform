ALTER TABLE teams ADD COLUMN repo_code TEXT CHECK(repo_code IS NULL OR (length(repo_code)=6 AND repo_code NOT GLOB '*[^a-z0-9]*'));
CREATE UNIQUE INDEX teams_repo_code ON teams(repo_code);
UPDATE teams SET repo_code=(
  WITH RECURSIVE candidates(code) AS (
    SELECT lower(hex(randomblob(3))) UNION ALL
    SELECT lower(hex(randomblob(3))) FROM candidates WHERE EXISTS(SELECT 1 FROM teams WHERE repo_code=code)
  ) SELECT code FROM candidates WHERE NOT EXISTS(SELECT 1 FROM teams WHERE repo_code=code) LIMIT 1
);
CREATE TRIGGER teams_repo_code_create AFTER INSERT ON teams WHEN NEW.repo_code IS NULL BEGIN
  UPDATE teams SET repo_code=(
    WITH RECURSIVE candidates(code) AS (
      SELECT lower(hex(randomblob(3))) UNION ALL
      SELECT lower(hex(randomblob(3))) FROM candidates WHERE EXISTS(SELECT 1 FROM teams WHERE repo_code=code)
    ) SELECT code FROM candidates WHERE NOT EXISTS(SELECT 1 FROM teams WHERE repo_code=code) LIMIT 1
  ) WHERE id=NEW.id;
END;
CREATE TRIGGER teams_repo_code_immutable BEFORE UPDATE OF repo_code ON teams
WHEN OLD.repo_code IS NOT NULL AND NEW.repo_code IS NOT OLD.repo_code BEGIN
  SELECT RAISE(ABORT,'repo_code is immutable');
END;
ALTER TABLE competitions ADD COLUMN submission_deadline INTEGER;
CREATE TABLE github_repositories (
  team_id TEXT PRIMARY KEY REFERENCES teams(id),
  repo_id INTEGER NOT NULL UNIQUE,
  owner TEXT NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL COLLATE NOCASE,
  installation_id INTEGER NOT NULL,
  attached_ms INTEGER NOT NULL,
  last_poll_ms INTEGER,
  next_poll_ms INTEGER NOT NULL DEFAULT 0,
  etag TEXT,
  state TEXT NOT NULL DEFAULT 'connected',
  error TEXT,
  UNIQUE(owner,name)
);
CREATE TABLE github_commits (
  team_id TEXT NOT NULL REFERENCES github_repositories(team_id),
  sha TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  pushed_ms INTEGER,
  received_ms INTEGER NOT NULL,
  bot_hash TEXT,
  submission_id TEXT REFERENCES documents(id),
  state TEXT NOT NULL DEFAULT 'detected',
  error TEXT,
  retry_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(team_id,sha)
);
CREATE INDEX github_commits_work ON github_commits(state,retry_ms);
CREATE TABLE github_observations (
  id INTEGER PRIMARY KEY,
  team_id TEXT NOT NULL,
  sha TEXT NOT NULL,
  observed_ms INTEGER NOT NULL,
  pushed_ms INTEGER,
  FOREIGN KEY(team_id,sha) REFERENCES github_commits(team_id,sha)
);
CREATE INDEX github_observations_team_time ON github_observations(team_id,observed_ms);
CREATE TABLE github_checks (
  team_id TEXT NOT NULL,
  sha TEXT NOT NULL,
  purpose TEXT NOT NULL,
  job_id TEXT REFERENCES documents(id),
  state TEXT NOT NULL DEFAULT 'waiting',
  status_sent INTEGER NOT NULL DEFAULT 0,
  retry_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(team_id,sha,purpose),
  FOREIGN KEY(team_id,sha) REFERENCES github_commits(team_id,sha)
);
CREATE TABLE github_deliveries (id TEXT PRIMARY KEY,received_ms INTEGER NOT NULL);
CREATE TABLE github_deadlines (
  competition_id TEXT NOT NULL REFERENCES competitions(id),
  team_id TEXT NOT NULL REFERENCES github_repositories(team_id),
  source TEXT NOT NULL,
  candidates TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'checking',
  selected_sha TEXT,
  pushed_ms INTEGER,
  error TEXT,
  PRIMARY KEY(competition_id,team_id)
);
