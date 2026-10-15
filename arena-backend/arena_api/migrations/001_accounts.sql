CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    members TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','team')),
    team_id TEXT UNIQUE REFERENCES teams(id),
    must_change INTEGER NOT NULL DEFAULT 1 CHECK(must_change IN (0,1)),
    locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
    delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1)),
    created_ms INTEGER NOT NULL,
    CHECK((role='admin' AND team_id IS NULL) OR (role='team' AND team_id IS NOT NULL))
);
CREATE TABLE registrations (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    team_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
    team_id TEXT REFERENCES teams(id),
    created_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX pending_team_name ON registrations(team_key) WHERE status='pending';
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    csrf TEXT NOT NULL,
    expires_ms INTEGER NOT NULL
);
CREATE INDEX sessions_account ON sessions(account_id);
CREATE TABLE recoveries (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    status TEXT NOT NULL CHECK(status IN ('pending','resolved')),
    created_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX pending_recovery ON recoveries(account_id) WHERE status='pending';
CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);
CREATE TABLE rate_limits (
    bucket TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL
);
