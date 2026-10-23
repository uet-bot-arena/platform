"""Bounded hot data. Results, SHA references and official replays survive cleanup."""
from collections import defaultdict
from dataclasses import asdict
import gzip
import json
import logging
from pathlib import Path
import shutil
import threading
import time

from arena.job_queue import official_priority
from arena.runner import digest
from arena.service import SHA256
from arena.store import REPLAY_REPORT_FIELDS
from arena_api.settings import RetentionSettings


PROOF_FIELDS = ('passed', 'turn_wall_ms', 'profile', 'runtime_id', 'image_id',
                'engine_sha256', 'suite_id', 'suite_sha256', 'artifacts', 'ruleset',
                'basic_rules', 'error', 'aborted', 'clock_scope')


def compact_check(job):
    report = job.get('report') or {}
    summary = dict(passed=report.get('passed') is True,
                   samples=len(report.get('samples', [])),
                   reason=str((job.get('error') or report.get('error') or {}).get('code', ''))[:160])
    proof = None
    if summary['passed'] and summary['samples'] == 72:
        proof = {k: report[k] for k in PROOF_FIELDS if k in report}
        proof.update(sample_count=72, report_sha256=digest(report))
    return summary, proof


class Retention:
    def __init__(self, service, settings=None):
        self.service, self.store = service, service.store
        self.settings = settings or RetentionSettings()
        self.stop = threading.Event()
        self.run_lock = threading.Lock()
        self.thread = threading.Thread(target=self.loop, name='retention', daemon=True)
        self.last = None
        self.disk_checked = 0
        self.disk_value = None
        service.cache_restored_source = self.cache_restored_source

    def cache_restored_source(self, item, raw):
        with self.store.lock:
            if item['id'] not in self.roles(item['team_id'])[0]:
                return
            folder = self.source_file(item)
            if folder is not None:
                target = folder.with_suffix('.zip')
                partial = folder.with_suffix('.zip.part')
                self.store.update(item['id'],cache_pruned=False)
                partial.write_bytes(raw);partial.replace(target)

    def status(self):
        if self.disk_value is None or time.monotonic()-self.disk_checked >= 60:
            self.disk_value = self.disk()
            self.disk_checked = time.monotonic()
        return dict(last_cleanup=self.last, disk=self.disk_value)

    def loop(self):
        # Wait for startup reconciliation before touching terminal attempts.
        while not self.stop.wait(self.settings.interval_seconds):
            try:
                self.run()
            except Exception:
                logging.getLogger('arena.retention').exception('Cleanup failed; data left in place')

    def roles(self, team, include_active=False):
        """Re-read before deleting, under the controller lock."""
        db = self.store.db
        sources = self.store.list('submission', team)
        passed_row = db.execute("SELECT json_extract(data,'$.submission_ids[0]') FROM documents WHERE kind='job' AND team=? AND json_extract(data,'$.kind')='benchmark' AND COALESCE(json_extract(data,'$.report.passed'),json_extract(data,'$.check_summary.passed'))=1 ORDER BY rowid DESC LIMIT 1", (team,)).fetchone()
        # Completion order, including bot/ content reused by a later commit.
        row = db.execute("SELECT g.submission_id FROM github_checks c JOIN github_commits g USING(team_id,sha) JOIN github_observations o USING(team_id,sha) WHERE c.team_id=? AND c.state='pass' ORDER BY o.id DESC,c.finished_ms DESC LIMIT 1", (team,)).fetchone()
        last_pass = row[0] if row else (passed_row[0] if passed_row else None)
        official = [r[0] for r in db.execute("SELECT e.submission_id FROM competition_entries e JOIN competitions c ON c.id=e.competition_id WHERE e.team_id=? AND (c.status NOT IN ('draft','open') OR EXISTS(SELECT 1 FROM github_deadlines gd WHERE gd.competition_id=c.id AND gd.team_id=e.team_id AND gd.state='locked')) ORDER BY COALESCE(c.locked_ms,c.created_ms) DESC", (team,))]
        # A season uses one frozen bot; preserve older active cups rather than deleting their binary.
        active_official = {r[0] for r in db.execute("SELECT e.submission_id FROM competition_entries e JOIN competitions c ON c.id=e.competition_id WHERE e.team_id=? AND c.status IN ('locked','running','paused')", (team,))}
        tournament = active_official | set(official[:1])
        observed = db.execute('SELECT g.submission_id FROM github_observations o JOIN github_commits g USING(team_id,sha) WHERE g.team_id=? AND g.submission_id IS NOT NULL ORDER BY o.id DESC LIMIT 1', (team,)).fetchone()
        latest = {observed[0]} if observed else ({sources[0]['id']} if sources else set())
        pinned = {last_pass} if last_pass else set()
        own_active = set()
        if include_active:
            for (data,) in db.execute("SELECT data FROM documents WHERE kind='job' AND team=? AND json_extract(data,'$.status') NOT IN ('completed','failed','aborted')", (team,)):
                own_active.update(json.loads(data).get('submission_ids', []))
            own_active &= {s['id'] for s in sources}
        return latest | pinned | tournament, pinned | tournament | own_active

    def certificate_needed(self, job):
        team = job['team_id']
        _, pinned = self.roles(team)
        if not set(job.get('submission_ids', [])) & pinned:
            return False
        db = self.store.db
        frozen = db.execute('SELECT 1 FROM competition_entries WHERE benchmark_id=?', (job['id'],)).fetchone()
        latest = db.execute("SELECT c.job_id FROM github_checks c JOIN github_commits g USING(team_id,sha) JOIN github_observations o USING(team_id,sha) WHERE c.team_id=? AND c.state='pass' ORDER BY o.id DESC,c.finished_ms DESC LIMIT 1", (team,)).fetchone()
        if not latest:
            latest = db.execute("SELECT id FROM documents WHERE kind='job' AND team=? AND json_extract(data,'$.kind')='benchmark' AND COALESCE(json_extract(data,'$.report.passed'),json_extract(data,'$.check_summary.passed'))=1 ORDER BY rowid DESC LIMIT 1", (team,)).fetchone()
        return bool(frozen) or bool(latest and latest[0]==job['id'])

    def disk(self):
        total = shutil.disk_usage(self.store.root).total
        size = sum(p.stat().st_size for p in self.store.root.rglob('*') if p.is_file() and not p.is_symlink())
        with self.store.lock:
            reusable = self.store.db.execute('PRAGMA freelist_count').fetchone()[0]*self.store.db.execute('PRAGMA page_size').fetchone()[0]
            incremental = self.store.db.execute('PRAGMA auto_vacuum').fetchone()[0]==2
        percent = 100 * max(0,size-reusable) / total if total else 100
        return dict(data_bytes=size, disk_bytes=total, percent=round(percent, 3),
                    reusable_bytes=reusable, offline_compaction_required=not incremental and reusable>0,
                    warning=100*size/total >= self.settings.disk_warning_percent if total else True)

    def reclaim_pages(self):
        # Small incremental steps release disk space without a long writer lock.
        with self.store.lock:
            self.store.db.execute('PRAGMA incremental_vacuum(256)').fetchall()
            self.store.db.commit()
            self.store.db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchall()

    def source_file(self, item):
        raw = Path(item.get('source_path', ''))
        folder = raw.resolve()
        root = (self.store.root/'sources').resolve()
        if folder.parent != root or raw.is_symlink():
            return None
        return folder

    def sources_and_binaries(self):
        counts = dict(sources_removed=0, binaries_removed=0, blocked_teams=[])
        with self.store.lock:
            sources = [json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM documents WHERE kind='submission' AND COALESCE(json_extract(data,'$.cache_pruned'),0)=0")]
        by_team = defaultdict(list)
        for source in sources:
            by_team[source['team_id']].append(source)
        for team, items in by_team.items():
            for item in items:
                folder = self.source_file(item)
                if folder is None:
                    continue
                # ZIP work runs outside the shared DB lock; the final role check is fresh.
                compressed = folder.with_suffix('.zip')
                if folder.is_dir() and not compressed.exists():
                    raw = self.service.submission_archive(item)
                    partial = compressed.with_suffix('.zip.part')
                    partial.write_bytes(raw)
                    partial.replace(compressed)
                with self.store.lock:
                    keep, binaries = self.roles(team)
                    if folder.is_dir():
                        shutil.rmtree(folder)
                    if item['id'] not in keep and not item.get('builtin') and not str(team).startswith('sample:'):
                        if compressed.exists():
                            compressed.unlink()
                            counts['sources_removed'] += 1
                        self.store.update(item['id'],cache_pruned=True)
            with self.store.lock:
                keep, binaries = self.roles(team)
                if not str(team).startswith('sample:') and (len(keep)>self.settings.source_versions or len(binaries)>self.settings.binary_versions):
                    counts['blocked_teams'].append(team)
        # A blob may be shared by many versions. Keep it if any protected version uses it.
        for path in self.service.artifacts.glob('*/*'):
            sha = path.name.removesuffix('.gz')
            if not path.is_file() or path.is_symlink() or not SHA256.fullmatch(sha) or path.suffix not in ('', '.gz'):
                continue
            partial = path.with_suffix('.gz.part')
            if path.suffix != '.gz':
                partial.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            with self.store.lock:
                references = self.store.db.execute('SELECT b.submission_id,d.team FROM builds b JOIN documents d ON d.id=b.submission_id WHERE b.platform=? AND b.artifact_sha256=?', (path.parent.name,sha)).fetchall()
                # In-flight binaries remain working data until the grader reports.
                protected = any(str(team).startswith('sample:') or ident in self.roles(team,include_active=True)[1] for ident,team in references)
                pinned = self.store.db.execute("SELECT e.submission_id,e.team_id FROM competition_entries e JOIN competitions c ON c.id=e.competition_id WHERE json_extract(e.snapshot,'$.artifact_sha256')=? AND json_extract(c.config,'$.grader_platform')=?", (sha,path.parent.name)).fetchall()
                protected = protected or any(ident in self.roles(team)[1] for ident,team in pinned)
                # An upload writes its blob before committing the build reference.
                if not protected and (references or time.time()-path.stat().st_mtime > 3600):
                    path.unlink()
                    counts['binaries_removed'] += 1
                elif path.suffix != '.gz':
                    partial.replace(path.with_suffix('.gz'))
                    path.unlink()
                if partial.exists():
                    partial.unlink()
        return counts

    def prune_replay(self, ident):
        with self.store.transaction():
            report = self.store.get(ident,hydrate=False).get('report') or {}
            self.store.db.execute('DELETE FROM replay_archives WHERE job=?', (ident,))
            self.store.update(ident, replay_pruned=True,
                              report={'result':report['result']} if report.get('result') else None)

    def run(self):
        with self.run_lock:
            cursor = 0
            while True:
                with self.store.lock:
                    batch = self.store.db.execute("SELECT rowid,id FROM documents WHERE rowid>? AND kind='job' AND json_extract(data,'$.kind')='match' AND json_extract(data,'$.status') IN ('completed','failed','aborted') AND COALESCE(json_extract(data,'$.retention_done'),0)=0 ORDER BY rowid LIMIT 100", (cursor,)).fetchall()
                if not batch:
                    break
                for cursor, ident in batch:
                    job = self.store.get(ident)
                    self.store.archive_events(ident)
                    with self.store.transaction():
                        if not official_priority(job):
                            teams = {e['team_id'] for e in (job.get('official') or {}).get('entries', [])} or {job['team_id']}
                            self.store.db.executemany('INSERT OR IGNORE INTO replay_owners VALUES(?,?,?,?)',
                                ((ident,t,job['created_unix_ms'],int(bool(job.get('official')))) for t in teams))
                        report = job.get('report')
                        if report:
                            report = {k:v for k,v in report.items() if k not in REPLAY_REPORT_FIELDS}
                            if (job.get('report') or {}).get('final_state'):
                                report['final_state'] = {'result':job['report']['final_state'].get('result')}
                        self.store.update(ident, report=report, report_compacted=True, retention_done=True)
            with self.store.lock:
                retained = {r[0] for r in self.store.db.execute('WITH ranked AS (SELECT o.job,ROW_NUMBER() OVER(PARTITION BY o.team ORDER BY o.created_ms DESC,d.rowid DESC) AS n FROM replay_owners o JOIN documents d ON d.id=o.job) SELECT DISTINCT job FROM ranked WHERE n<=?', (self.settings.replay_games,))}
                candidates = self.store.db.execute('SELECT a.job,MIN(o.created_ms),MAX(o.ladder) FROM replay_archives a JOIN replay_owners o ON o.job=a.job JOIN documents d ON d.id=a.job GROUP BY a.job ORDER BY MAX(o.ladder),MIN(o.created_ms),MIN(d.rowid)').fetchall()
                check_ids = [r[0] for r in self.store.db.execute("WITH ranked AS (SELECT id,data,ROW_NUMBER() OVER(PARTITION BY team ORDER BY rowid DESC) AS n FROM documents WHERE kind='job' AND json_extract(data,'$.kind')='benchmark' AND json_extract(data,'$.status') IN ('completed','failed','aborted')) SELECT id FROM ranked WHERE n>? AND json_type(data,'$.report')='object'", (self.settings.check_reports,))]
            purged = []
            for ident,_,_ in candidates:
                if ident not in retained:
                    self.prune_replay(ident)
                    purged.append(ident)
            for ident in check_ids:
                job = self.store.get(ident)
                summary, proof = compact_check(job)
                with self.store.transaction():
                    if not self.certificate_needed(job):
                        proof = None
                    self.store.update(ident, report=None, check_summary=summary, qualification=proof)
                    self.store.db.execute('DELETE FROM events WHERE job=?', (ident,))
                    self.store.db.execute('DELETE FROM replay_archives WHERE job=?', (ident,))
            with self.store.lock:
                certificates = [r[0] for r in self.store.db.execute("SELECT id FROM documents WHERE kind='job' AND json_type(data,'$.qualification')='object'")]
            for ident in certificates:
                with self.store.transaction():
                    if not self.certificate_needed(self.store.get(ident)):
                        self.store.update(ident, qualification=None)
            counts = self.sources_and_binaries()
            self.reclaim_pages()
            disk = self.disk()
            emergency = []
            if disk['percent'] >= self.settings.disk_cleanup_percent:
                for ident,_,_ in candidates:
                    if ident not in retained:
                        continue
                    self.prune_replay(ident)
                    emergency.append(ident)
                    self.reclaim_pages()
                    if self.disk()['percent'] < self.settings.disk_cleanup_percent:
                        break
            self.last = dict(completed_ms=time.time_ns()//1_000_000, policy=asdict(self.settings),
                             replays_removed=purged, emergency_removed=emergency, disk=self.disk(), **counts)
            self.store.root.joinpath('retention-status.json').write_text(json.dumps(self.last), encoding='utf-8')
            self.disk_value, self.disk_checked = self.last['disk'], time.monotonic()
            return self.last
