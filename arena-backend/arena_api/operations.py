"""Organizer operations; soft deletion preserves immutable jobs and results."""
from datetime import datetime, timedelta
import json
import secrets
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.accounts import name_key, now_ms


class Operations:
    def __init__(self, store, accounts, service, competitions):
        self.store, self.accounts, self.service, self.competitions = store, accounts, service, competitions
        self.db = store.db
        self.started = time.monotonic()
        self.health_at, self.health_value = 0, None

    @staticmethod
    def revision(item, expected):
        if item['revision'] != expected:
            raise Fault('admin_conflict', 'Record changed in another session; refresh before editing')

    def create_team(self, profile, username, password, actor):
        password = password or 'Uet-' + secrets.token_urlsafe(18)
        encoded = self.accounts.hash_password(password)
        ident, account = uuid.uuid4().hex, uuid.uuid4().hex
        try:
            with self.store.lock, self.db:
                key = name_key(profile['team'])
                if self.accounts.one("SELECT id FROM registrations WHERE team_key=? AND status='pending'", (key,)):
                    raise Fault('admin_conflict', 'A registration for this team is pending; approve it instead')
                self.db.execute('INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)',
                    (ident, profile['team'], key, profile['email'], profile['phone'], json.dumps(profile['members']), now_ms()))
                self.db.execute("INSERT INTO accounts(id,username,password_hash,role,team_id,created_ms) VALUES(?,?,?,'team',?,?)",
                    (account, username.casefold(), encoded, ident, now_ms()))
                self.accounts.audit(actor, 'team.created', ident)
                result = self.accounts.public(self.accounts.one('SELECT * FROM accounts WHERE id=?', (account,)))
            return {'account': result, 'temporary_password': password, 'credential_visibility': 'this_response_only'}
        except sqlite3.IntegrityError:
            raise Fault('account_conflict', 'Username or team name already exists; creation was rolled back') from None

    def edit_team(self, ident, data, actor):
        try:
            with self.store.lock, self.db:
                team = self.accounts.team(ident)
                self.revision(team, data['expected_revision'])
                if team['archived_ms']:
                    raise Fault('admin_conflict', 'Restore the team before editing it')
                key = name_key(data['team'])
                if self.accounts.one("SELECT id FROM registrations WHERE team_key=? AND status='pending'", (key,)):
                    raise Fault('admin_conflict', 'A registration with this name is pending')
                self.db.execute('UPDATE teams SET name=?,name_key=?,email=?,phone=?,members=?,revision=revision+1 WHERE id=?',
                    (data['team'], key, data['email'], data['phone'], json.dumps(data['members']), ident))
                self.accounts.audit(actor, 'team.updated', ident)
                return self.accounts.team(ident)
        except sqlite3.IntegrityError:
            raise Fault('account_conflict', 'Team name is already in use') from None

    def archive_team(self, ident, expected, archived, actor):
        with self.store.lock, self.db:
            team = self.accounts.team(ident)
            self.revision(team, expected)
            if bool(team['archived_ms']) == archived:
                return team
            if archived:
                if self.accounts.one("SELECT 1 FROM competition_entries e JOIN competitions c ON c.id=e.competition_id WHERE e.team_id=? AND c.status<>'completed' AND c.archived_ms IS NULL", (ident,)):
                    raise Fault('admin_conflict', 'Team is entered in an unfinished competition; remove the open entry or finish the competition first')
                if any(j['status'] not in TERMINAL for j in self.store.list('job', ident)):
                    raise Fault('admin_conflict', 'Wait for this team\'s active jobs to finish')
                self.db.execute('UPDATE accounts SET locked=1 WHERE team_id=?', (ident,))
            # Restore retains the lock; organizer explicitly unlocks afterwards.
            self.db.execute('DELETE FROM sessions WHERE account_id IN (SELECT id FROM accounts WHERE team_id=?)', (ident,))
            self.db.execute('UPDATE teams SET archived_ms=?,revision=revision+1 WHERE id=?', (now_ms() if archived else None, ident))
            self.accounts.audit(actor, 'team.archived' if archived else 'team.restored', ident)
            return self.accounts.team(ident)

    def jobs(self, limit, offset, status=None, kind=None, team_id=None):
        where, args = ["kind='job'"], []
        for field, value in (('status',status),('kind',kind)):
            if value:
                where.append(f"json_extract(data,'$.{field}')=?")
                args.append(value)
        if team_id:
            where.append('team=?'); args.append(team_id)
        clause = ' AND '.join(where)
        with self.store.lock:
            total = self.db.execute('SELECT COUNT(*) FROM documents WHERE '+clause, args).fetchone()[0]
            rows = self.db.execute("SELECT json_remove(data,'$.report') FROM documents WHERE "+clause+' ORDER BY rowid DESC LIMIT ? OFFSET ?', (*args,limit,offset)).fetchall()
        return {'items':[json.loads(r[0]) for r in rows], 'total':total, 'limit':limit, 'offset':offset}

    def submissions(self, limit, offset):
        with self.store.lock:
            total = self.db.execute("SELECT COUNT(*) FROM documents WHERE kind='submission'").fetchone()[0]
            rows = self.db.execute("SELECT json_remove(data,'$.source_path') FROM documents WHERE kind='submission' ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit,offset)).fetchall()
        return {'items':[json.loads(r[0]) for r in rows], 'total':total, 'limit':limit, 'offset':offset}

    def metrics(self):
        # Docker probes run outside the DB lock; cached for the dashboard poll.
        if self.health_value is None or time.monotonic()-self.health_at>10:
            self.health_value = self.service.health()
            self.health_at = time.monotonic()
        tz = ZoneInfo('Asia/Bangkok')
        today = datetime.now(tz).date()
        daily = {(today-timedelta(days=i)).isoformat(): {'day':(today-timedelta(days=i)).isoformat(),'jobs':0,'failed':0,'benchmarks':0,'matches':0} for i in range(6,-1,-1)}
        with self.store.lock:
            def count(table, where=''):
                return self.db.execute('SELECT COUNT(*) FROM '+table+where).fetchone()[0]
            jobs = self.accounts.rows("""SELECT id,team, json_extract(data,'$.kind') AS kind,
                json_extract(data,'$.status') AS status,json_extract(data,'$.created_unix_ms') AS created_ms,
                json_extract(data,'$.error.code') AS error,json_extract(data,'$.report.passed') AS passed,
                json_extract(data,'$.report.turn_wall_ms.p95_ms') AS p95
                FROM documents WHERE kind='job'""")
            totals = {status:sum(j['status']==status for j in jobs) for status in ('queued','building','validating','benchmarking','playing','completed','failed','aborted')}
            errors = {}
            for j in jobs:
                label = datetime.fromtimestamp(j['created_ms']/1000,tz).date().isoformat()
                if label in daily:
                    daily[label]['jobs']+=1
                    daily[label]['failed']+=int(j['status']=='failed')
                    daily[label]['benchmarks' if j['kind']=='benchmark' else 'matches']+=1
                if j['error']: errors[j['error']]=errors.get(j['error'],0)+1
            recent = [j for j in jobs if j['created_ms']>=now_ms()-86400000]
            completed_benchmarks = [j for j in recent if j['kind']=='benchmark' and j['status'] in TERMINAL]
            p95s = [j['p95'] for j in recent if j['kind']=='benchmark' and j['status']=='completed' and isinstance(j['p95'],(int,float))]
            counts = {'teams':count('teams',' WHERE archived_ms IS NULL'), 'archived_teams':count('teams',' WHERE archived_ms IS NOT NULL'),
                'locked_accounts':count('accounts'," WHERE role='team' AND locked=1"),
                'pending_registrations':count('registrations'," WHERE status='pending'"),
                'pending_recoveries':count('recoveries'," WHERE status='pending'"),
                'competitions':count('competitions',' WHERE archived_ms IS NULL'),
                'attention_fixtures':count('competition_fixtures'," WHERE status='attention'"),
                'submissions':count('documents'," WHERE kind='submission'")}
        return {'counts':counts,'jobs':{'total':len(jobs),'by_status':totals},
            'last_24h':{'jobs':len(recent),'benchmark_pass_rate':round(100*sum(j['status']=='completed' and j['passed']==1 for j in completed_benchmarks)/len(completed_benchmarks),1) if completed_benchmarks else None,
                'benchmark_p95_max_ms':max(p95s) if p95s else None},
            'daily':list(daily.values()),'timezone':'Asia/Bangkok',
            'errors':[{'code':code,'count':value} for code,value in sorted(errors.items(),key=lambda x:-x[1])],
            'worker':{**self.health_value,'scheduler_error':self.competitions.scheduler_error,
                'queue_depth':self.service.pending.qsize(),'queue_capacity':self.service.pending.maxsize,
                'queue_by_scope':self.service.pending.counts(), 'queue_policy':'official_first_fifo', 'reserved_official_slots':1,
                'active_job_id':self.service.active,'uptime_seconds':int(time.monotonic()-self.started)},
            'generated_ms':now_ms()}
