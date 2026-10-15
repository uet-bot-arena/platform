ALTER TABLE competition_fixtures ADD COLUMN round_number INTEGER;
CREATE TABLE competition_byes (
 competition_id TEXT NOT NULL REFERENCES competitions(id),
 round_number INTEGER NOT NULL,
 team_id TEXT NOT NULL,
 PRIMARY KEY(competition_id,round_number),
 UNIQUE(competition_id,team_id),
 FOREIGN KEY(competition_id,team_id) REFERENCES competition_entries(competition_id,team_id)
);
