CREATE INDEX github_checks_unsent ON github_checks(team_id,sha) WHERE status_sent=0;
CREATE INDEX github_checks_due ON github_checks(retry_ms) WHERE state IN ('waiting','checking');
CREATE INDEX github_observations_latest ON github_observations(team_id,id DESC);
CREATE INDEX competition_entry_public ON competition_entries(competition_id,team_id,accepted_ms,json_extract(snapshot,'$.version'));
INSERT INTO github_checks(team_id,sha,purpose,state)
 SELECT g.team_id,g.sha,'archive',g.state FROM github_commits g
 WHERE g.state IN ('pass','fail') AND NOT EXISTS(
 SELECT 1 FROM github_checks c WHERE c.team_id=g.team_id AND c.sha=g.sha);
CREATE TRIGGER github_terminal_update AFTER UPDATE OF state ON github_commits
WHEN NEW.state IN ('pass','fail') AND NOT EXISTS(
 SELECT 1 FROM github_checks WHERE team_id=NEW.team_id AND sha=NEW.sha)
BEGIN INSERT INTO github_checks(team_id,sha,purpose,state)
 VALUES(NEW.team_id,NEW.sha,'archive',NEW.state); END;
CREATE TABLE elo_rating_history(
 competition_id TEXT NOT NULL,round_id TEXT NOT NULL,phase TEXT NOT NULL,
 number INTEGER NOT NULL,team_id TEXT NOT NULL,rating REAL NOT NULL,completed_ms INTEGER NOT NULL,
 PRIMARY KEY(round_id,team_id));
CREATE INDEX elo_rating_history_team ON elo_rating_history(competition_id,team_id,completed_ms);
INSERT INTO elo_rating_history
 SELECT r.competition_id,r.id,r.phase,r.number,j.key,j.value,r.completed_ms
 FROM elo_rounds r,json_each(r.after_state,'$.ratings') j WHERE r.status='completed';
