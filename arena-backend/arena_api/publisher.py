from collections import OrderedDict
"""Separate read-only SQLite process; public, atomic gzip files are the guest API."""
import argparse
import csv
import gzip
import io
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

from arena.eta import Timing
from arena.service import Service
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.competitions import Competitions
from arena_api.elo_scheduler import EloScheduler

IDENT = re.compile(r'[A-Za-z0-9_-]{1,100}')
ROW_FIELDS = ('team_id','name','version','accepted_ms','played','wins','draws','losses',
              'points','head_to_head','rating','sprint_gain','rank','is_guest','excluded',
              'buchholz','bye_round','bye_points','point_breakdown')
CONFIG_FIELDS = ('format','ranking','scoring','max_teams','adjudication','profile',
                 'protocol_version','engine_id','suite_id','ruleset','basic_rules',
                 'rounds','current_round','ladder','official','finalists')


def pick(value, fields):
    return {k:value[k] for k in fields if k in value}


def public_competition(value):
    result = pick(value, ('id','title','status','created_ms','locked_ms','completed_ms',
                          'submission_deadline','revision','archived_ms','entries',
                          'fixture_counts','elo'))
    result['config'] = pick(value['config'], CONFIG_FIELDS)
    for field in ('ruleset','basic_rules'):
        if result['config'].get(field):
            result['config'][field] = pick(result['config'][field], ('id','name','version','hash','definition','protocol_version'))
    if result['config'].get('finalists'):
        result['config']['finalists'] = [pick(r, ROW_FIELDS) for r in result['config']['finalists']]
    return result


def atomic_gzip(path, value):
    """The name and bytes change together; ETag is derived from the served bytes."""
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    data = gzip.compress(raw, compresslevel=1, mtime=0)
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix+'.tmp')
    partial.write_bytes(data)
    partial.replace(path)
    return True


def csv_bytes(rows):
    output = io.StringIO(newline='')
    fields = [k for k in ROW_FIELDS if any(k in row for row in rows) and k!='point_breakdown']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        # CSV spreadsheets must treat public names as text, never formulas.
        writer.writerow({k:("'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@','\t','\r') else v) for k,v in row.items()})
    return output.getvalue().encode('utf-8-sig')


class Publisher:
    def __init__(self, settings):
        self.settings = settings
        self.output = settings.publisher.output_dir or settings.data/'snapshots'
        self.output = self.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        # Do not run Store/Accounts/Service constructors: each can write/recover jobs.
        store = Store.__new__(Store)
        store.root, store.lock = settings.data.resolve(), threading.RLock()
        store.db = sqlite3.connect((store.root/'arena.sqlite').as_uri()+'?mode=ro', uri=True)
        store.db.execute('PRAGMA query_only=ON')
        store.db.execute('PRAGMA busy_timeout=5000')
        self.store, self.db = store, store.db
        self.fixture_cache = OrderedDict()
        self.recent_games = {}
        accounts = Accounts.__new__(Accounts)
        accounts.db, accounts.store = self.db, store
        service = Service.__new__(Service)
        service.store = store
        epoch = self.db.execute('SELECT epoch_ms FROM controller_clock WHERE id=1').fetchone()
        service.lease_epoch_ms = epoch[0] if epoch else time.time_ns()//1_000_000-time.monotonic_ns()//1_000_000
        service.timing = Timing.__new__(Timing)
        service.timing.store = store
        comp = Competitions.__new__(Competitions)
        comp.store, comp.db, comp.accounts, comp.service = store, self.db, accounts, service
        comp.elo = EloScheduler(comp)
        self.comp, self.service = comp, service
        self.cursor_file = self.output/'.cursor'
        self.cursor = int(self.cursor_file.read_text()) if self.cursor_file.exists() else 0
        self.visible = set()
        self.read_index()

    def read_index(self):
        path = self.output/'competitions.json.gz'
        if path.exists():
            self.visible = {c['id'] for c in json.loads(gzip.decompress(path.read_bytes()))['items']}

    def close(self):
        self.db.close()

    def write(self, name, value):
        path = self.output/(name+'.gz')
        if not path.resolve().is_relative_to(self.output):
            raise ValueError('Snapshot path escaped output directory')
        return atomic_gzip(path, value)

    def fixture(self, row):
        ident, cid = row['id'], row['competition_id']
        if not IDENT.fullmatch(ident) or not IDENT.fullmatch(cid):
            raise ValueError('Invalid persisted fixture ID')
        job=row.get('current_job_id')
        metadata=self.store.get(job,hydrate=False) if job else {}
        pruned=bool(metadata.get('replay_pruned'))
        cached=self.fixture_cache.pop(ident,None)
        if not cached or cached['job']!=job or pruned:
            cached=dict(job=job,cursor=0,frames=[],latest={},bytes=0)
        while job and not pruned:
            values,after=self.comp.safe_events(job,cached['cursor'])
            for event in values:
                frame=json.dumps(event,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
                cached['frames'].append(frame);cached['bytes']+=len(frame)
                cached['latest'][event['type']]=event
            if after==cached['cursor']:break
            cached['cursor']=after
        public=self.comp.fixture_view(row)
        if row.get('phase'):public['phase']=row['phase']
        value=dict(fixture=public,attempt_id=job,next_after=cached['cursor'])
        if pruned:value['replay_pruned']=True
        # Encode each event only once; the full immutable prefix is reused.
        header=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        raw=header[:-1]+b',"items":['+b','.join(cached['frames'])+b']}'
        self.write(f'fixtures/{cid}/{ident}.json',raw)
        if job and metadata.get('status') not in ('completed','failed','aborted') and not pruned:
            self.fixture_cache[ident]=cached
            while sum(item['bytes'] for item in self.fixture_cache.values())>192*1024**2:
                self.fixture_cache.popitem(last=False)
        return dict(value,items=sorted(cached['latest'].values(),key=lambda e:e['seq']),summary_only=True)

    def fixture_row(self, ident):
        row = self.comp.one('SELECT * FROM competition_fixtures WHERE id=?', (ident,))
        if not row:
            row = self.comp.one('SELECT g.*,r.competition_id,r.phase FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE g.id=?', (ident,))
        return row

    def standings(self, ident):
        value = self.comp.standings(ident)
        rows = [pick(r, ROW_FIELDS) for r in value['items']]
        value['items'], value['total'] = rows[:100], len(rows)
        self.write(f'standings/{ident}.json', value)
        self.write(f'standings/{ident}.csv', csv_bytes(rows))
        for row in rows:
            if not IDENT.fullmatch(row['team_id']):
                raise ValueError('Invalid persisted team ID')
            self.write(f'standings/{ident}/{row["team_id"]}.json', row)

    def schedule(self, ident):
        # Keep every unfinished game; a large round robin must not hide the
        # early active boards behind thousands of future games.
        if self.comp.get(ident)['config']['format']=='elo':
            query='SELECT g.id,g.ordinal,g.red_team_id,g.blue_team_id,g.status,g.result,g.current_job_id,r.competition_id,r.phase,r.started_ms,r.number FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?'
            rows=self.comp.rows(query+" AND g.status<>'completed'",(ident,))
            rows+=self.comp.rows(query+" AND g.status='completed' ORDER BY r.started_ms DESC,r.phase DESC,r.number DESC,g.ordinal DESC LIMIT 1000",(ident,))
            rows.sort(key=lambda r:(r['started_ms'],r['phase'],r['number'],r['ordinal']))
            total = self.comp.one('SELECT COUNT(*) AS n FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?',(ident,))['n']
        else:
            rows=self.comp.rows("SELECT * FROM competition_fixtures WHERE competition_id=? AND status<>'completed'",(ident,))
            rows+=self.comp.rows("SELECT * FROM competition_fixtures WHERE competition_id=? AND status='completed' ORDER BY ordinal DESC LIMIT 1000",(ident,))
            rows.sort(key=lambda r:r['ordinal'])
            total = self.comp.one('SELECT COUNT(*) AS n FROM competition_fixtures WHERE competition_id=?',(ident,))['n']
        items = [self.comp.fixture_view(r) | ({'phase':r['phase']} if r.get('phase') else {}) for r in rows]
        for row, item in zip(rows, items):
            if row['status'] == 'completed' and row.get('current_job_id'):
                event = self.db.execute('SELECT data FROM events WHERE job=? ORDER BY seq DESC LIMIT 1', (row['current_job_id'],)).fetchone()
                item['completed_ms'] = json.loads(event[0])['server_unix_ms'] if event else 0
        finished = [item for item in items if item['status'] == 'completed']
        self.recent_games[ident] = max(finished, key=lambda f:(f.get('completed_ms',0), f['ordinal']), default=None)
        self.write(f'fixtures/{ident}.json', dict(items=items,total=len(items),history_total=total,recent_completed_limit=1000,offset=0))

    def remove_competition(self, ident):
        import shutil
        if not IDENT.fullmatch(ident):
            raise ValueError('Invalid persisted competition ID')
        for name in (f'competitions/{ident}.json.gz',f'standings/{ident}.json.gz',f'standings/{ident}.csv.gz',f'fixtures/{ident}.json.gz'):
            (self.output/name).unlink(missing_ok=True)
        for path in (self.output/'standings'/ident,self.output/'fixtures'/ident):
            if path.resolve().parent not in (self.output/'standings',self.output/'fixtures'):
                raise ValueError('Invalid competition directory')
            if path.exists():
                shutil.rmtree(path)

    def publish(self):
        started = time.monotonic()
        self.db.execute('BEGIN')
        try:
            epoch = self.db.execute('SELECT epoch_ms FROM controller_clock WHERE id=1').fetchone()
            if epoch:
                self.service.lease_epoch_ms = epoch[0]
            version = self.db.execute('SELECT value FROM public_counter WHERE id=1').fetchone()[0]
            changes = self.comp.rows('SELECT scope,id FROM public_changes WHERE version>? ORDER BY version', (self.cursor,))
            visible = {r['id'] for r in self.comp.rows("SELECT id FROM competitions WHERE status<>'draft' AND archived_ms IS NULL")}
            for ident in self.visible-visible:
                self.remove_competition(ident)
            changed = {r['id'] for r in changes if r['scope']=='competition'}
            if any(r['scope']=='teams' for r in changes):
                changed |= visible
            changed |= visible-self.visible
            for ident in sorted(changed & visible):
                self.standings(ident)
            fixture_ids = {r['id'] for r in changes if r['scope']=='fixture'}
            for ident in visible-self.visible:
                fixture_ids.update(r['id'] for r in self.comp.rows('SELECT id FROM competition_fixtures WHERE competition_id=?',(ident,)))
                fixture_ids.update(r['id'] for r in self.comp.rows('SELECT g.id FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=?',(ident,)))
            active = self.comp.rows("SELECT f.id FROM competition_fixtures f JOIN documents d ON d.id=f.current_job_id WHERE json_extract(d.data,'$.status') IN ('starting','building','playing')")
            active += self.comp.rows("SELECT g.id FROM elo_games g JOIN documents d ON d.id=g.current_job_id WHERE json_extract(d.data,'$.status') IN ('starting','building','playing')")
            fixture_ids.update(r['id'] for r in active)
            boards = []
            for ident in sorted(fixture_ids):
                row = self.fixture_row(ident)
                if row and row['competition_id'] in visible:
                    value = self.fixture(row)
                    boards.append(dict(competition_id=row['competition_id'],fixture_id=row['id'],**value))
            items = []
            for ident in sorted(changed & visible):
                self.schedule(ident)
            for ident in sorted(visible):
                item = public_competition(self.comp.view(ident))
                recent = self.recent_games.get(ident)
                if recent:
                    item['recent_completed'] = {'id':recent['id'], 'completed_ms':recent.get('completed_ms',0)}
                self.write(f'competitions/{ident}.json',item)
                if item['config']['format']=='elo' and ident not in changed:
                    path=self.output/f'standings/{ident}.json.gz'
                    if path.exists():
                        board=json.loads(gzip.decompress(path.read_bytes()))
                        board['progress']=item['elo']
                        self.write(f'standings/{ident}.json',board)
                items.append(item)
            self.write('competitions.json',dict(items=items,total=len(items)))
            queue = {r['priority']:r['n'] for r in self.comp.rows('SELECT priority,COUNT(*) AS n FROM job_queue GROUP BY priority')}
            graders = self.service.graders(online_only=True)
            self.write('queue.json',dict(waiting=dict(official=queue.get(0,0),checks=queue.get(1,0),practice=queue.get(2,0)),
                running=self.comp.one("SELECT COUNT(*) AS n FROM grader_leases WHERE status='active'")['n'],
                slots=sum(g['slots'] or 0 for g in graders), generated_ms=time.time_ns()//1_000_000))
            boards.sort(key=lambda b:(b['fixture']['status']=='completed',b['fixture_id']))
            self.write('featured.json',dict(boards=boards[:4],generated_ms=time.time_ns()//1_000_000))
            from arena_api.announcements import public as public_notice
            notices=[public_notice(json.loads(r[0])) for r in self.db.execute("SELECT data FROM documents WHERE kind='announcement' AND json_extract(data,'$.archived_ms') IS NULL ORDER BY rowid DESC")]
            self.write('announcements.json',dict(items=notices))
            self.db.commit()
            partial = self.cursor_file.with_suffix('.tmp')
            partial.write_text(str(version),encoding='ascii');partial.replace(self.cursor_file)
            self.cursor, self.visible = version, visible
            return dict(version=version, seconds=round(time.monotonic()-started,3), competitions=len(visible), fixtures=len(fixture_ids))
        except BaseException:
            self.db.rollback()
            raise


def run(settings, once=False):
    publisher = Publisher(settings)
    try:
        while True:
            started = time.monotonic()
            try:
                result = publisher.publish()
                if once:
                    print(json.dumps(result));return
            except Exception:
                if once:raise
                logging.getLogger('arena.publisher').exception('Snapshot failed; retry next interval')
            time.sleep(max(.1,settings.publisher.interval_seconds-(time.monotonic()-started)))
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()


if __name__=='__main__':
    from arena_api.settings import Settings
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    run(Settings(),parser.parse_args().once)
