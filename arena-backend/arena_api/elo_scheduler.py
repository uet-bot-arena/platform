"""Elo calendar and dispatch; SQL snapshots survive hub/grader restarts."""
import json
import time
from datetime import datetime,timedelta

from arena.config import match_profile
from arena.elo import INITIAL, official_phase, top_group, round_robin_rank, select
from arena.ladder_clock import boundary,VN
from arena.protocol import Fault
from arena.service import TERMINAL
from arena_api.elo_rounds import RoundBook


class EloScheduler:
    def __init__(self,comp):
        self.comp=comp
        self.book=RoundBook(comp)
        self.store,self.db=comp.store,comp.db
        self.calendar_second=None

    def entries(self,ident):
        return {r['team_id']:dict(json.loads(r['snapshot']),bot_hash=json.loads(r['snapshot'])['source']['zip_sha256'])
            for r in self.comp.rows("SELECT e.* FROM competition_entries e JOIN teams t ON t.id=e.team_id WHERE competition_id=? AND t.is_guest=0 ORDER BY e.team_id",(ident,))}

    def tick(self):
        self.reconcile()
        now=time.time_ns()//1_000_000
        # Calendar work is shared by the scheduler thread and every grader poll.
        if self.calendar_second!=now//1000:
            self.calendar_second=now//1000
            for row in self.comp.rows("SELECT id FROM competitions WHERE archived_ms IS NULL AND json_extract(config,'$.format')='elo' AND status IN ('open','running')"):
                item=self.comp.get(row['id'])
                try:
                    self.advance(item,now)
                except Fault as exc:
                    if not item['config'].get('elo_error'):
                        with self.store.transaction():
                            config=self.comp.get(item['id'])['config'];config['elo_error']=exc.detail
                            self.db.execute("UPDATE competitions SET config=?,status=CASE WHEN status='running' THEN 'paused' ELSE status END,revision=revision+1 WHERE id=?",(json.dumps(config),item['id']))
                            self.comp.accounts.audit('system','elo.attention',item['id'])
        self.dispatch()

    def advance(self,item,now):
        ident=item['id'];config=item['config']
        if item['status'] not in ('open','running'):return
        if config.get('elo_error'):return
        if item['status']=='open':
            if now>=item['submission_deadline']:
                # GitHub must finish authoritative deadline resolution before lock.
                remaining=self.comp.one("SELECT COUNT(*) AS n FROM github_repositories r JOIN teams t ON t.id=r.team_id LEFT JOIN github_deadlines d ON d.team_id=r.team_id AND d.competition_id=? WHERE t.archived_ms IS NULL AND t.season_id=? AND (d.state IS NULL OR d.state NOT IN ('locked','no_entry'))",(ident,self.comp.teams.season))['n']
                if remaining:return
                entries=self.entries(ident)
                if len(entries)<2:return
                self.comp.lock('system',ident,'elo-auto-lock:'+ident)
                self.comp.transition('system',ident,'running','elo-auto-start:'+ident)
                return
            due=boundary(now,config['ladder']['round_hours'])
            with self.store.transaction():
                old=self.comp.one('SELECT last_due_ms FROM elo_calendar WHERE competition_id=?',(ident,))
                self.db.execute('INSERT OR IGNORE INTO elo_calendar VALUES(?,?)',(ident,due))
                if old is None or old['last_due_ms']>=due:return
                self.db.execute('UPDATE elo_calendar SET last_due_ms=? WHERE competition_id=?',(due,ident))
                active=self.comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND status<>'completed'",(ident,))
            if active:return  # Skip this fixed boundary; do not drift to finish time.
            entries=self.book.latest_passes(ident,due)
            for team in list(entries):
                try:self.comp.teams.eligible(team)
                except Fault:del entries[team]
            if len(entries)<2:return
            number=self.comp.one("SELECT COALESCE(MAX(number),0)+1 AS n FROM elo_rounds WHERE competition_id=? AND phase='ladder'",(ident,))['n']
            cfg=dict(config,profile=match_profile(10000).public())
            self.book.start(ident,'ladder',number,due,entries,config=cfg)
            return
        active=self.comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND status<>'completed'",(ident,))
        if active:return
        entries=self.entries(ident)
        self.book.seed_official(ident,entries,item['submission_deadline'])
        done=self.comp.one("SELECT COUNT(*) AS n FROM elo_rounds WHERE competition_id=? AND phase='general' AND status='completed'",(ident,))['n']
        rules=config['official']
        rr=self.comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND phase='round_robin'",(ident,))
        if rr:
            finalists=select(self.eligible_rows(ident,self.book.get(rr['id'])['after_state']['standings']),rules['finalists'])
            with self.store.transaction():
                cfg=self.comp.get(ident)['config'];cfg['finalists']=finalists
                self.db.execute("UPDATE competitions SET status='completed',completed_ms=?,config=?,revision=revision+1 WHERE id=?",(now,json.dumps(cfg),ident))
                self.comp.accounts.audit('system','elo.completed',ident)
            return
        phase=official_phase(len(entries),done,rules['group_size'],rules['general_rounds'])
        if phase=='round_robin':
            ratings={r['team_id']:r['rating'] for r in self.comp.rows("SELECT * FROM elo_ratings WHERE competition_id=? AND stage='official'",(ident,))}
            entries={t:entries[t] for t in top_group(ratings,rules['group_size'])}
        self.book.start(ident,phase,done+1 if phase=='general' else 1,now,entries)

    def reconcile(self):
        from arena_api.competitions import INFRASTRUCTURE
        with self.store.transaction():
            rows=self.comp.rows("SELECT g.*,r.competition_id,r.config FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id JOIN competitions c ON c.id=r.competition_id WHERE json_extract(c.config,'$.format')='elo' AND g.status='queued'")
            for row in rows:
                job=self.store.get(row['current_job_id'])
                if job['status'] not in TERMINAL:continue
                valid=self.comp.valid_report(job,json.loads(row['config']))
                report=job.get('report') or {}
                error=None if valid else (job.get('error') or report.get('error') or {}).get('code','unscored_attempt')
                retry=not valid and error in INFRASTRUCTURE and row['auto_retries']<self.comp.max_attempts
                status='completed' if valid else 'pending' if retry else 'attention'
                self.db.execute('UPDATE elo_games SET status=?,result=?,auto_retries=auto_retries+? WHERE id=?',
                    (status,json.dumps(report['result']) if valid else None,int(retry),row['id']))
                self.db.execute('UPDATE elo_attempts SET status=?,error_code=? WHERE job_id=?',('completed' if valid else 'attention',error,job['id']))
                if status=='attention':
                    self.db.execute("UPDATE elo_rounds SET status='attention' WHERE id=?",(row['round_id'],))
                self.comp.accounts.audit('system','elo.result' if valid else 'elo.retry' if retry else 'elo.attention',row['id'])
        for row in self.comp.rows("SELECT r.id FROM elo_rounds r JOIN competitions c ON c.id=r.competition_id WHERE json_extract(c.config,'$.format')='elo' AND r.status='running' AND NOT EXISTS(SELECT 1 FROM elo_games g WHERE g.round_id=r.id AND g.status<>'completed')"):
            self.book.finish(row['id'],time.time_ns()//1_000_000)

    def dispatch(self):
        with self.store.transaction():
            if self.comp.stop.is_set() or self.comp.service.stop.is_set() or self.comp.service.pending.full():return
            queued=self.comp.one("SELECT (SELECT COUNT(*) FROM elo_games WHERE status='queued')+(SELECT COUNT(*) FROM competition_fixtures WHERE status='queued') AS n")['n']
            if queued>=self.comp.service.worker_count:return
            row=self.comp.one("SELECT g.*,r.competition_id,r.phase,r.config FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id JOIN competitions c ON c.id=r.competition_id WHERE json_extract(c.config,'$.format')='elo' AND g.status='pending' AND r.status='running' AND (c.status='running' OR (r.phase='ladder' AND c.status IN ('open','locked'))) ORDER BY CASE r.phase WHEN 'ladder' THEN 1 ELSE 0 END,r.started_ms,g.ordinal LIMIT 1")
            if row is None:return
            item=self.comp.get(row['competition_id']);cfg=json.loads(row['config'])
            self.comp.check_runtime(cfg)
            entries=[json.loads(row[side+'_entry']) for side in ('red','blue')]
            number=self.comp.one('SELECT COUNT(*)+1 AS n FROM elo_attempts WHERE game_id=?',(row['id'],))['n']
            job=self.comp.match_job(item,entries,row['id'],number,config=cfg)
            job['official'].update(phase=row['phase'],round_id=row['round_id'])
            self.db.execute('INSERT INTO documents VALUES(?,?,?,?)',(job['id'],'job',job['team_id'],json.dumps(job)))
            self.db.execute("UPDATE elo_games SET status='queued',current_job_id=? WHERE id=?",(job['id'],row['id']))
            self.db.execute("INSERT INTO elo_attempts VALUES(?,?,?,'queued',NULL,?)",(row['id'],number,job['id'],time.time_ns()//1_000_000))
            self.db.execute('INSERT INTO events VALUES(?,?,?)',(job['id'],1,json.dumps(dict(seq=1,type='queued',server_unix_ms=time.time_ns()//1_000_000,data=dict(kind='match')))))
            self.comp.service.pending.put(job['id'],official=row['phase']!='ladder',kind='match',platform=cfg['grader_platform'],runtime_id=cfg['runtime_id'])
    def public(self,ident):
        """Shared public progress only; private entries and worker reports stay in SQL."""
        item=self.comp.get(ident);config=item['config']
        rounds=self.comp.rows('SELECT id,phase,number,started_ms,completed_ms,status,config,snapshot FROM elo_rounds WHERE competition_id=? ORDER BY started_ms,phase,number',(ident,))
        current=rounds[-1] if rounds else None
        result=dict(rounds=[],current=None,next_round_ms=None,server_ms=time.time_ns()//1_000_000)
        for row in rounds:
            counts={r['status']:r['n'] for r in self.comp.rows('SELECT status,COUNT(*) AS n FROM elo_games WHERE round_id=? GROUP BY status',(row['id'],))}
            result['rounds'].append({k:row[k] for k in ('id','phase','number','started_ms','completed_ms','status')}|dict(completed=counts.get('completed',0),total=sum(counts.values())))
        if current:
            summary=dict(result['rounds'][-1]);cfg=json.loads(current['config'])
            started=self.comp.one('SELECT 1 AS yes FROM grader_leases l JOIN elo_attempts a ON a.job_id=l.job_id JOIN elo_games g ON g.id=a.game_id WHERE g.round_id=? LIMIT 1',(current['id'],)) is not None
            slots=sum(g['slots'] or 0 for g in self.comp.service.graders(online_only=True) if g['platform']==cfg['grader_platform'] and g['runtime_id']==cfg['runtime_id'])
            summary['state']='completed' if summary['status']=='completed' else 'attention' if summary['status']=='attention' else 'running' if started else 'queued'
            summary['eta']=self.comp.service.timing.estimate('match',cfg['profile']['id'],cfg['grader_platform'],cfg['runtime_id'],summary['total']-summary['completed'],slots,started=started,now_ms=self.comp.service.lease_now_ms())
            result['current']=summary
            if current['phase']!='ladder':
                team_count=self.comp.one("SELECT COUNT(*) AS n FROM elo_ratings WHERE competition_id=? AND stage='official'",(ident,))['n']
                rules=config['official'];group=min(team_count,rules['group_size'])
                general_total=rules['general_rounds'] if team_count>rules['group_size'] else 0
                finished=self.comp.one("SELECT COUNT(*) AS n FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? AND r.phase<>'ladder' AND g.status='completed'",(ident,))['n']
                # Odd general rounds have one bye and N-1 games.
                total=general_total*(team_count-team_count%2)+group*(group-1)
                result['official_total_games']=total
                result['official_completed_games']=finished
                result['general_total_rounds']=general_total
                result['announcement_eta']=self.comp.service.timing.estimate('match',cfg['profile']['id'],cfg['grader_platform'],cfg['runtime_id'],total-finished,slots,started=started,now_ms=self.comp.service.lease_now_ms())
        if item['status']=='open' and result['server_ms']<item['submission_deadline']:
            result['next_round_ms']=boundary(result['server_ms'],config['ladder']['round_hours'],next=True)
            if result['next_round_ms']>=item['submission_deadline']:result['next_round_ms']=None
        return result

    def standings(self,ident):
        item=self.comp.get(ident)
        rr=self.comp.one("SELECT id,snapshot,status FROM elo_rounds WHERE competition_id=? AND phase='round_robin'",(ident,))
        matrix=[]
        if rr:
            ratings=json.loads(rr['snapshot'])['ratings']
            completed=[g for g in self.book.games(rr['id']) if g['status']=='completed']
            scores=self.book.scores(completed)
            rows=round_robin_rank(ratings,scores)
            for game in self.book.games(rr['id']):
                matrix.append(dict(id=game['id'],competition_id=ident,red_team_id=game['red_team_id'],blue_team_id=game['blue_team_id'],status=game['status'],result=json.loads(game['result']) if game['result'] else None))
            ranking='round_robin_points'
        else:
            stage='ladder' if item['status']=='open' else 'official'
            values=self.comp.rows('SELECT * FROM elo_ratings WHERE competition_id=? AND stage=? ORDER BY rating DESC,team_id',(ident,stage))
            rows=[];previous=None;rank=0
            for index,r in enumerate(values,1):
                if r['rating']!=previous:rank=index
                rows.append(dict(team_id=r['team_id'],rating=r['rating'],played=r['games'],rank=rank));previous=r['rating']
            ranking='elo'
        local=datetime.fromtimestamp(time.time(),VN)
        monday=local.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=local.weekday())
        baseline=self.book.ladder_at(ident,int(monday.timestamp()*1000))
        if ranking=='elo':
            for row in rows:row['sprint_gain']=row['rating']-baseline.get(row['team_id'],INITIAL)
        exclusions=self.exclusions(ident)
        names={r['id']:r for r in self.comp.rows('SELECT id,name,is_guest FROM teams')}
        for row in rows:
            team=names[row['team_id']];row.update(name=team['name'],is_guest=bool(team['is_guest']),excluded=row['team_id'] in exclusions)
        # Guest display never awards an official rank.
        index=0;previous=None;rank=0
        for row in rows:
            if row['is_guest'] or row['excluded']:row['rank']=None;continue
            index+=1
            key=(row.get('points'),row.get('head_to_head'),row.get('wins'),row['rating']) if rr else (row['rating'],)
            if key!=previous:rank=index
            row['rank']=rank;previous=key
        return dict(competition_id=ident,ranking=ranking,scoring=dict(win=1,draw=.5,loss=0),provisional=item['status']!='completed',items=rows,matrix=matrix,progress=self.public(ident))

    def team_view(self,team):
        result=[];now=time.time_ns()//1_000_000
        for item in self.comp.rows("SELECT id,title,status,submission_deadline FROM competitions WHERE archived_ms IS NULL AND status<>'draft' AND json_extract(config,'$.format')='elo' ORDER BY created_ms DESC LIMIT 20"):
            ident=item['id'];current=None
            rows=self.comp.rows('SELECT id FROM elo_rounds r WHERE competition_id=? AND EXISTS(SELECT 1 FROM elo_games g WHERE g.round_id=r.id AND (g.red_team_id=? OR g.blue_team_id=?)) ORDER BY started_ms DESC LIMIT 1',(ident,team,team))
            if rows:
                row=self.book.get(rows[0]['id']);before=row['snapshot'];after=row['after_state']
                current=dict(id=row['id'],phase=row['phase'],number=row['number'],status=row['status'],sha=before['entries'][team]['github_sha'],
                    elo_before=before['ratings'][team],elo_after=after['ratings'][team] if after else None,games=[])
                for game in self.book.games(row['id']):
                    if team not in (game['red_team_id'],game['blue_team_id']):continue
                    opponent=game['blue_team_id'] if game['red_team_id']==team else game['red_team_id']
                    current['games'].append(dict(id=game['id'],competition_id=ident,status=game['status'],opponent=opponent,
                        opponent_name=self.comp.one('SELECT name FROM teams WHERE id=?',(opponent,))['name'],
                        red_team_id=game['red_team_id'],result=json.loads(game['result']) if game['result'] else None))
            ladder_open=item['status']=='open' and now<item['submission_deadline']
            passed=self.book.latest_passes(ident,now,team).get(team) if ladder_open else None
            newest=self.comp.one('SELECT o.sha,c.state FROM github_observations o LEFT JOIN github_checks c ON c.team_id=o.team_id AND c.sha=o.sha AND c.purpose=? WHERE o.team_id=? ORDER BY o.id DESC LIMIT 1',(ident,team))
            history=[]
            for row in self.comp.rows("SELECT number,completed_ms,after_state FROM elo_rounds WHERE competition_id=? AND phase='ladder' AND status='completed' ORDER BY number",(ident,)):
                value=json.loads(row['after_state'])['ratings'].get(team)
                if value is not None:history.append(dict(round=row['number'],at_ms=row['completed_ms'],rating=value))
            result.append(dict(ladder_open=ladder_open,history=history,competition_id=ident,title=item['title'],current=current,next_sha=passed['github_sha'] if passed else None,
                latest_sha=newest['sha'] if newest else None,check_state=newest['state'] if newest else None))
        return dict(items=result)

    def exclusions(self,ident):
        return {r['team_id'] for r in self.comp.rows('SELECT e.team_id,e.excluded FROM elo_exclusions e WHERE competition_id=? AND e.id=(SELECT MAX(x.id) FROM elo_exclusions x WHERE x.competition_id=e.competition_id AND x.team_id=e.team_id)',(ident,)) if r['excluded']}

    def eligible_rows(self,ident,rows):
        excluded=self.exclusions(ident)
        result=[];previous=None;rank=0
        for row in rows:
            if row['team_id'] in excluded:continue
            if row['rank']!=previous:rank=len(result)+1
            previous=row['rank'];result.append(dict(row,rank=rank))
        return result

    def exclude(self,actor,ident,team,body,key):
        def action():
            item=self.comp.get(ident)
            self.comp.require_status(item,'draft','open','locked','running','paused','completed')
            if item['config']['format']!='elo':raise Fault('competition_conflict','Chỉ áp dụng cho giải Elo')
            self.comp.accounts.team(team)
            if len(body['reason'].strip())<3:raise Fault('invalid_request','Cần lý do ít nhất ba ký tự')
            self.db.execute('INSERT INTO elo_exclusions(competition_id,team_id,excluded,reason,actor,created_ms) VALUES(?,?,?,?,?,?)',
                (ident,team,int(body['excluded']),body['reason'],actor,time.time_ns()//1_000_000))
            if item['status']=='completed':
                rr=self.comp.one("SELECT id FROM elo_rounds WHERE competition_id=? AND phase='round_robin'",(ident,))
                config=item['config'];rows=self.book.get(rr['id'])['after_state']['standings']
                config['finalists']=select(self.eligible_rows(ident,rows),config['official']['finalists'])
                self.db.execute('UPDATE competitions SET config=?,revision=revision+1 WHERE id=?',(json.dumps(config),ident))
            return dict(id=ident,team_id=team,excluded=body['excluded'])
        return self.comp.command(actor,'elo_exclude:'+ident+':'+team,key,body,action)
