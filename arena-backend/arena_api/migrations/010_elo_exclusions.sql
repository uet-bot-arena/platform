CREATE TABLE elo_exclusions(
 id INTEGER PRIMARY KEY,competition_id TEXT NOT NULL REFERENCES competitions(id),team_id TEXT NOT NULL REFERENCES teams(id),
 excluded INTEGER NOT NULL CHECK(excluded IN (0,1)),reason TEXT NOT NULL,actor TEXT NOT NULL,created_ms INTEGER NOT NULL);
CREATE INDEX elo_exclusion_history ON elo_exclusions(competition_id,team_id,id);
