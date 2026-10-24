"""SDK-based season disk budget from native season measurements and a read-only DB copy."""
import argparse
import gzip
import json
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from arena_api.retention import compact_check
from arena.store import Store


def estimate(season, snapshot):
    run=json.loads(season.read_text());native=run['storage_measurements']
    with sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        jobs=[json.loads(r[0]) for r in db.execute("SELECT data FROM documents WHERE kind='job' AND json_extract(data,'$.status')='completed'")]
        checks=[j for j in jobs if j['kind']=='benchmark'];matches=[j for j in jobs if j['kind']=='match']
        assert len(checks)==15 and len(matches)>=20
        compacted=[];proofs=[]
        for job in checks:
            summary,proof=compact_check(job)
            job.update(report=None,check_summary=summary,qualification=None)
            compacted.append(len(json.dumps(job)));proofs.append(len(json.dumps(proof)))
        pruned=[]
        for job in matches:
            report=job.get('report') or {}
            job.update(report={'result':report['result']} if report.get('result') else None,replay_pruned=True)
            pruned.append(len(json.dumps(job)))
        event_bytes=db.execute("SELECT AVG(bytes) FROM (SELECT SUM(length(e.data)) AS bytes FROM events e JOIN documents d ON d.id=e.job WHERE json_extract(d.data,'$.kind')='benchmark' AND json_extract(d.data,'$.status')='completed' GROUP BY e.job)").fetchone()[0]
        detailed_summaries={kind:db.execute("SELECT AVG(length("+Store._job_summary_column()+")) FROM documents WHERE kind='job' AND json_extract(data,'$.kind')=?",(kind,)).fetchone()[0] for kind in ('benchmark','match')}
        source_json=statistics.mean(len(r[0]) for r in db.execute("SELECT data FROM documents WHERE kind='submission' AND team IN (SELECT id FROM teams)"))
        github_json=sum(statistics.mean(len(json.dumps(dict(r))) for r in db.execute('SELECT * FROM '+table))
            for table in ('github_commits','github_checks','github_observations','builds'))
    document={r['kind']:r['mean_bytes'] for r in native['job_documents']}
    measured=dict(source_zip=statistics.mean(native['source_zip_bytes']),binary_gzip=statistics.mean(native['binary_gzip_bytes']),
        detailed_check=document['benchmark'],check_events=event_bytes,old_check_summary=statistics.mean(compacted),
        qualification=statistics.mean(proofs),source_metadata=source_json,github_and_build_metadata=github_json,
        full_match_job=document['match'],pruned_match_job=statistics.mean(pruned),
        detailed_check_cached_summary=detailed_summaries['benchmark'],full_match_cached_summary=detailed_summaries['match'],
        replay_gzip=native['replay_archives'][0]['mean_bytes'],elo_game_payload=native['elo_games'][0]['mean_entry_result_bytes'],
        ladder_round_per_team=statistics.mean(r['bytes']/r['teams'] for r in native['elo_rounds'] if r['phase']=='ladder'),
        general_round_per_team=statistics.mean(r['bytes']/r['teams'] for r in native['elo_rounds'] if r['phase']=='general'),
        round_robin_snapshot=next(r['bytes']/r['teams'] for r in native['elo_rounds'] if r['phase']=='round_robin'))
    rows=[]
    for teams in (200,1000):
        for days in (14,30):
            rounds=12*days;pushes=10*days*teams
            official=20*teams+64*63;ladder=rounds*teams;games=ladder+official
            recent=min(ladder,50*teams) # upper bound: own practice need not share a replay with another team
            files=teams*(3*measured['source_zip']+2*measured['binary_gzip'])
            database=dict(checks=20*teams*(document['benchmark']+event_bytes)+max(0,pushes-20*teams)*measured['old_check_summary']+2*teams*measured['qualification'],
                push_metadata=pushes*(source_json+github_json),
                match_jobs=games*measured['pruned_match_job']+(official+recent)*(document['match']-measured['pruned_match_job']),
                materialized_job_summaries=20*teams*measured['detailed_check_cached_summary']+max(0,pushes-20*teams)*measured['old_check_summary']+games*measured['pruned_match_job']+(official+recent)*(measured['full_match_cached_summary']-measured['pruned_match_job']),
                elo_games=games*(measured['elo_game_payload']+400),
                round_snapshots=teams*(rounds*measured['ladder_round_per_team']+20*measured['general_round_per_team'])+64*measured['round_robin_snapshot'],
                replays=(official+recent)*measured['replay_gzip'])
            # Page/index/audit/email/lease overhead is a stated budget, not a measured scaling law.
            data_budget=files+2*sum(database.values())
            backup_budget=files+(data_budget-files)*.5
            rows.append(dict(teams=teams,days=days,ladder_rounds=rounds,official_games=official,total_games=games,
                pushes=pushes,compressed_source_binary_bytes=round(files),database_components_bytes={k:round(v) for k,v in database.items()},
                data_budget_gib=round(data_budget/1024**3,2),data_plus_two_uncompressed_backup_budgets_gib=round(3*data_budget/1024**3,2),
                compressed_backup_budget_gib=round(backup_budget/1024**3,2),data_plus_two_compressed_backups_and_2gib_os_budget=round((data_budget+2*backup_budget)/1024**3+2,2)))
    return dict(model='SDK sample; not an upper bound for arbitrary student sources/binaries/game lengths',
        assumptions=dict(pushes_per_team_per_day=10,ladder_round_hours=2,check_reports=20,source_versions=3,binary_versions=2,
            recent_replays_upper_bound_per_team=50,elo_game_reference_budget_bytes=400,sqlite_page_index_operations_budget_multiplier=2,
            historical_benchmark_timings='Real reports copied for size estimation; do not count these as more native executions',
            compressed_backup_budget='Keep source/binary bytes at 100%; budget DB/metadata gzip at 50%; add 2 GiB OS separately',
            measured_fixture_sqlite_gzip_ratio=round(len(gzip.compress(snapshot.read_bytes(),mtime=0))/snapshot.stat().st_size,4)),
        measured_bytes={k:round(v,2) for k,v in measured.items()},scenarios=rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season',type=Path,required=True);parser.add_argument('--snapshot',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(estimate(args.season.resolve(),args.snapshot.resolve()),ensure_ascii=False,indent=2))
