-- Publisher reads only changed public records; it never acknowledges changes in DB.
CREATE TABLE public_counter(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
INSERT INTO public_counter VALUES(1,1);
CREATE TABLE public_changes(scope TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(scope,id));
CREATE INDEX public_changes_version ON public_changes(version);
CREATE INDEX competition_fixture_job ON competition_fixtures(current_job_id);
CREATE INDEX elo_game_job ON elo_games(current_job_id);
INSERT INTO public_changes SELECT 'competition',id,1 FROM competitions;
INSERT INTO public_changes SELECT 'fixture',id,1 FROM competition_fixtures;
INSERT OR IGNORE INTO public_changes SELECT 'fixture',id,1 FROM elo_games;

CREATE TRIGGER public_competitions_insert AFTER INSERT ON competitions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competitions_update AFTER UPDATE ON competitions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competitions_delete AFTER DELETE ON competitions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',OLD.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_entries_insert AFTER INSERT ON competition_entries BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_entries_update AFTER UPDATE ON competition_entries BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_entries_delete AFTER DELETE ON competition_entries BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',OLD.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_fixtures_insert AFTER INSERT ON competition_fixtures BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_fixtures_update AFTER UPDATE ON competition_fixtures BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_competition_fixtures_delete AFTER DELETE ON competition_fixtures BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',OLD.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',OLD.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_games_insert AFTER INSERT ON elo_games BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',(SELECT competition_id FROM elo_rounds WHERE id=NEW.round_id),(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_games_update AFTER UPDATE ON elo_games BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',NEW.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',(SELECT competition_id FROM elo_rounds WHERE id=NEW.round_id),(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_games_delete AFTER DELETE ON elo_games BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('fixture',OLD.id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
  INSERT INTO public_changes VALUES('competition',(SELECT competition_id FROM elo_rounds WHERE id=OLD.round_id),(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_rounds_insert AFTER INSERT ON elo_rounds BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_rounds_update AFTER UPDATE ON elo_rounds BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_rounds_delete AFTER DELETE ON elo_rounds BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',OLD.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_ratings_insert AFTER INSERT ON elo_ratings BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_ratings_update AFTER UPDATE ON elo_ratings BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_ratings_delete AFTER DELETE ON elo_ratings BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',OLD.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_exclusions_insert AFTER INSERT ON elo_exclusions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_exclusions_update AFTER UPDATE ON elo_exclusions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',NEW.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_elo_exclusions_delete AFTER DELETE ON elo_exclusions BEGIN
  UPDATE public_counter SET value=value+1 WHERE id=1;
  INSERT INTO public_changes VALUES('competition',OLD.competition_id,(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;

CREATE TRIGGER public_team_name AFTER UPDATE OF name,is_guest ON teams BEGIN
 UPDATE public_counter SET value=value+1 WHERE id=1;
 INSERT INTO public_changes VALUES('teams','all',(SELECT value FROM public_counter)) ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;
CREATE TRIGGER public_replay_pruned AFTER UPDATE OF data ON documents
WHEN COALESCE(json_extract(NEW.data,'$.replay_pruned'),0)=1 AND COALESCE(json_extract(OLD.data,'$.replay_pruned'),0)=0 BEGIN
 UPDATE public_counter SET value=value+1 WHERE id=1;
 INSERT INTO public_changes SELECT 'fixture',id,(SELECT value FROM public_counter) FROM elo_games WHERE current_job_id=NEW.id
 ON CONFLICT(scope,id) DO UPDATE SET version=excluded.version;
END;
