-- Remote graders: automatic new attempts after infrastructure failures, at most K per fixture
-- (grader.max_attempts). A manual retry resets the counter.
ALTER TABLE competition_fixtures ADD COLUMN auto_retries INTEGER NOT NULL DEFAULT 0;
