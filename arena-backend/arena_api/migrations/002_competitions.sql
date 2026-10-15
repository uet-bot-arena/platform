CREATE TABLE competitions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','open','locked','running','paused','completed')),
    config TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    locked_ms INTEGER,
    completed_ms INTEGER
);
CREATE TABLE competition_entries (
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    team_id TEXT NOT NULL REFERENCES teams(id),
    submission_id TEXT NOT NULL REFERENCES documents(id),
    benchmark_id TEXT NOT NULL REFERENCES documents(id),
    snapshot TEXT NOT NULL,
    accepted_ms INTEGER NOT NULL,
    PRIMARY KEY(competition_id,team_id)
);
CREATE TABLE competition_fixtures (
    id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    ordinal INTEGER NOT NULL,
    red_team_id TEXT NOT NULL,
    blue_team_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','queued','completed','attention')),
    current_job_id TEXT REFERENCES documents(id),
    result TEXT,
    UNIQUE(competition_id,ordinal),
    UNIQUE(competition_id,red_team_id,blue_team_id),
    CHECK(red_team_id <> blue_team_id),
    FOREIGN KEY(competition_id,red_team_id) REFERENCES competition_entries(competition_id,team_id),
    FOREIGN KEY(competition_id,blue_team_id) REFERENCES competition_entries(competition_id,team_id)
);
CREATE TABLE competition_attempts (
    fixture_id TEXT NOT NULL REFERENCES competition_fixtures(id),
    number INTEGER NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES documents(id),
    status TEXT NOT NULL CHECK(status IN ('queued','completed','attention')),
    error_code TEXT,
    created_ms INTEGER NOT NULL,
    PRIMARY KEY(fixture_id,number)
);
CREATE INDEX competition_dispatch ON competition_fixtures(status,competition_id,ordinal);
CREATE TABLE competition_commands (
    actor TEXT NOT NULL,
    operation TEXT NOT NULL,
    key TEXT NOT NULL,
    digest TEXT NOT NULL,
    response TEXT NOT NULL,
    PRIMARY KEY(actor,operation,key)
);
