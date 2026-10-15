-- Re-rank stored, valid results without modifying any fixture or bot artifact.
INSERT INTO audit(actor,action,target,created_ms)
SELECT 'system','competition.points_ranking_migrated',id,CAST(strftime('%s','now') AS INTEGER)*1000
FROM competitions WHERE json_extract(config,'$.ranking')='wins_shared_rank';

UPDATE competitions
SET config=json_set(config,'$.ranking','points_shared_rank','$.scoring',json('{"win":3,"draw":1,"loss":0}')),
    revision=revision+1
WHERE json_extract(config,'$.ranking')='wins_shared_rank';
