-- Rebuild the single account table; keep account/team IDs and sessions intact.
CREATE TEMP TABLE saved_sessions AS SELECT * FROM sessions;
CREATE TEMP TABLE saved_recoveries AS SELECT * FROM recoveries;
DROP TABLE sessions;
DROP TABLE recoveries;
CREATE TABLE people (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','guest','student','staff')),
    team_id TEXT REFERENCES teams(id),
    must_change INTEGER NOT NULL DEFAULT 1 CHECK(must_change IN (0,1)),
    locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
    delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1)),
    created_ms INTEGER NOT NULL,
    email TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    cohort INTEGER,
    is_judge INTEGER NOT NULL DEFAULT 0 CHECK(is_judge IN (0,1)),
    class_name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'cpp17',
    introduction TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    looking INTEGER NOT NULL DEFAULT 0 CHECK(looking IN (0,1)),
    invite_failures INTEGER NOT NULL DEFAULT 0,
    invite_window_ms INTEGER NOT NULL DEFAULT 0,
    invite_blocked_ms INTEGER NOT NULL DEFAULT 0,
    CHECK(is_judge=0 OR role='staff')
);
INSERT INTO people(id,username,password_hash,role,team_id,must_change,locked,delivered,created_ms,display_name)
SELECT id,username,password_hash,CASE role WHEN 'team' THEN 'guest' ELSE role END,team_id,must_change,locked,delivered,created_ms,username FROM accounts;
DROP TABLE accounts;
ALTER TABLE people RENAME TO accounts;
CREATE INDEX accounts_team ON accounts(team_id);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    csrf TEXT NOT NULL,
    expires_ms INTEGER NOT NULL
);
CREATE INDEX sessions_account ON sessions(account_id);
INSERT INTO sessions SELECT * FROM saved_sessions;
CREATE TABLE recoveries (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    status TEXT NOT NULL CHECK(status IN ('pending','resolved')),
    created_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX pending_recovery ON recoveries(account_id) WHERE status='pending';
INSERT INTO recoveries SELECT * FROM saved_recoveries;
DROP TABLE saved_sessions;
DROP TABLE saved_recoveries;

CREATE TABLE seasons (
    id TEXT PRIMARY KEY,
    team_deadline_ms INTEGER,
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT INTO seasons(id) VALUES('default');
-- SQLite cannot add a REFERENCES column with a non-NULL default to populated tables.
-- Membership rows enforce the season FK; all team creation paths assign an existing season.
ALTER TABLE teams ADD COLUMN season_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE teams ADD COLUMN is_guest INTEGER NOT NULL DEFAULT 0 CHECK(is_guest IN (0,1));
ALTER TABLE teams ADD COLUMN locked_ms INTEGER;
ALTER TABLE teams ADD COLUMN recruiting INTEGER NOT NULL DEFAULT 0 CHECK(recruiting IN (0,1));
ALTER TABLE teams ADD COLUMN legacy_roster INTEGER NOT NULL DEFAULT 1 CHECK(legacy_roster IN (0,1));
CREATE TABLE team_members (
    season_id TEXT NOT NULL REFERENCES seasons(id),
    team_id TEXT NOT NULL REFERENCES teams(id),
    user_id TEXT NOT NULL REFERENCES accounts(id),
    captain INTEGER NOT NULL DEFAULT 0 CHECK(captain IN (0,1)),
    joined_ms INTEGER NOT NULL,
    PRIMARY KEY(season_id,user_id)
);
CREATE INDEX team_members_team ON team_members(team_id);
CREATE UNIQUE INDEX team_one_captain ON team_members(team_id) WHERE captain=1;
INSERT INTO team_members SELECT 'default',team_id,id,1,created_ms FROM accounts WHERE team_id IS NOT NULL;
CREATE TABLE team_invites (
    team_id TEXT PRIMARY KEY REFERENCES teams(id),
    code TEXT NOT NULL UNIQUE,
    changed_ms INTEGER NOT NULL
);
