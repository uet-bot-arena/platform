"""Durable Elo snapshots and whole-round accounting, independent of dispatch speed."""
from collections import Counter,deque
import json
import uuid

from arena.elo import INITIAL,OFFICIAL_K,apply_round,general_pair,pair,round_robin_schedule,round_robin_rank
from arena.protocol import Fault


class RoundBook:
    def __init__(self,competitions):
        self.comp=competitions
        self.store,self.db=competitions.store,competitions.db

    def latest_passes(self,ident,as_of,team_id=None):
        config=self.comp.get(ident)['config']
        team_filter=' AND g.team_id=?' if team_id is not None else ''
        rows=self.comp.rows("""SELECT g.*,c.job_id,c.finished_ms,o.id AS observation_id
          FROM github_observations o JOIN github_commits g USING(team_id,sha)
          JOIN github_checks c USING(team_id,sha) JOIN teams t ON t.id=g.team_id
          WHERE c.purpose=? AND c.state='pass' AND c.finished_ms<=? AND o.observed_ms<=? AND g.received_ms<=?
            AND t.archived_ms IS NULL AND t.season_id=?
          """+team_filter+' ORDER BY o.id DESC',(ident,as_of,as_of,as_of,getattr(self.comp.accounts,'season_id','default'))+((team_id,) if team_id is not None else ()))
        entries={}
        for row in rows:
            team=row['team_id']
            if team in entries:continue
            try:
                snapshot=self.comp.qualification(config,team,row['submission_id'],row['job_id'])
            except Fault as exc:
                if exc.code not in ('qualification_required','not_found'):raise
                continue
            entries[team]=dict(snapshot,github_sha=row['sha'],bot_hash=row['bot_hash'])
        return entries

    def ladder_at(self,ident,cutoff):
        values={}
        for row in self.comp.rows("SELECT after_state FROM elo_rounds WHERE competition_id=? AND phase='ladder' AND status='completed' AND completed_ms<=? ORDER BY number",(ident,cutoff)):
            values.update(json.loads(row['after_state'])['ratings'])
        return values

    def seed_official(self,ident,teams,cutoff):
        values=self.ladder_at(ident,cutoff)
        with self.store.transaction():
            for team in teams:
                self.db.execute("INSERT OR IGNORE INTO elo_ratings(competition_id,team_id,stage,rating) VALUES(?,?,'official',?)",(ident,team,values.get(team,INITIAL)))
        return {team:values.get(team,INITIAL) for team in teams}

    def history(self,ident,phase,teams):
        recent={t:deque(maxlen=3) for t in teams};meetings=Counter();byes={}
        rows=self.comp.rows("SELECT r.number,g.red_team_id,g.blue_team_id FROM elo_games g JOIN elo_rounds r ON r.id=g.round_id WHERE r.competition_id=? AND r.phase=? AND g.ordinal%2=1 ORDER BY r.number,g.ordinal",(ident,phase))
        for row in rows:
            a,b=row['red_team_id'],row['blue_team_id']
            meetings[tuple(sorted((a,b)))]+=1
            if a in recent:recent[a].append(b)
            if b in recent:recent[b].append(a)
        for row in self.comp.rows("SELECT bye_team_id AS team,COUNT(*) AS count,MAX(number) AS last FROM elo_rounds WHERE competition_id=? AND phase=? AND bye_team_id IS NOT NULL GROUP BY bye_team_id",(ident,phase)):
            byes[row['team']]=(row['count'],row['last'])
        return meetings,recent,byes

    def start(self,ident,phase,number,started_ms,entries,config=None):
        if phase not in ('ladder','general','top','round_robin') or len(entries)<2:
            raise Fault('competition_conflict','Đợt Elo cần ít nhất hai đội và giai đoạn hợp lệ')
        stage='ladder' if phase=='ladder' else 'official'
        config=config or self.comp.get(ident)['config']
        with self.store.lock:
            state={r['team_id']:r for r in self.comp.rows('SELECT * FROM elo_ratings WHERE competition_id=? AND stage=?',(ident,stage))}
            if stage=='official' and any(t not in state for t in entries):
                raise Fault('competition_conflict','Chưa chốt Elo ladder làm đầu vào chính thức')
            before={t:state.get(t,dict(rating=INITIAL,games=0,change_remaining=0,bot_hash=None)) for t in entries}
            ratings={t:r['rating'] for t,r in before.items()};played={t:r['games'] for t,r in before.items()}
            changed=[t for t in entries if entries[t].get('bot_hash')!=before[t]['bot_hash']]
            changes={t:10 if t in changed and phase=='ladder' else before[t]['change_remaining'] for t in entries}
            meetings,recent,byes=self.history(ident,phase,entries)
        # Blossom may be slow. Keep it outside the shared SQLite lock.
        if phase=='round_robin':
            schedule=round_robin_schedule(entries)
            pairs=None;bye=None;radius=0
        elif phase=='general':
            pairs,bye,radius=general_pair(ratings,meetings,byes)
        else:
            pairs,bye=pair(ratings,recent=recent,byes=byes);radius=10
        if phase!='round_robin':
            schedule=[colors for a,b in pairs for colors in ((a,b),(b,a))]
        snapshot=dict(ratings=ratings,played=played,changes=changes,changed_teams=changed,entries=entries)
        with self.store.transaction():
            if self.db.execute("SELECT 1 FROM elo_rounds WHERE competition_id=? AND status<>'completed'",(ident,)).fetchone():
                raise Fault('competition_conflict','Đợt trước chưa xong')
            # A competing start/finish cannot overwrite the snapshot prepared above.
            latest={r['team_id']:r for r in self.comp.rows('SELECT * FROM elo_ratings WHERE competition_id=? AND stage=?',(ident,stage))}
            if any(latest.get(t,dict(rating=INITIAL,games=0,change_remaining=0,bot_hash=None))!=before[t] for t in entries):
                raise Fault('competition_conflict','Elo đã thay đổi; cần chụp lại đợt')
            rid=uuid.uuid4().hex
            self.db.execute("INSERT INTO elo_rounds(id,competition_id,phase,number,started_ms,status,neighbor_ranks,bye_team_id,config,snapshot) VALUES(?,?,?,?,?,'running',?,?,?,?)",
                (rid,ident,phase,number,started_ms,radius,bye,json.dumps(config),json.dumps(snapshot)))
            for ordinal,(red,blue) in enumerate(schedule,1):
                self.db.execute("INSERT INTO elo_games(id,round_id,ordinal,red_team_id,blue_team_id,red_sha,blue_sha,red_entry,blue_entry,status) VALUES(?,?,?,?,?,?,?,?,?,'pending')",
                    (f'{rid}-{ordinal:06d}',rid,ordinal,red,blue,entries[red]['github_sha'],entries[blue]['github_sha'],json.dumps(entries[red]),json.dumps(entries[blue])))
            self.comp.accounts.audit('system','elo.round_started',rid)
        return rid

    def get(self,rid):
        row=self.comp.one('SELECT * FROM elo_rounds WHERE id=?',(rid,))
        if row is None:raise Fault('not_found','Không có đợt Elo này')
        for name in ('snapshot','config','after_state'):
            row[name]=json.loads(row[name]) if row[name] else None
        return row

    def games(self,rid):
        return self.comp.rows('SELECT * FROM elo_games WHERE round_id=? ORDER BY ordinal',(rid,))

    def export(self,ident,stage):
        if stage not in ('ladder','official'):
            raise Fault('invalid_request','Chọn ladder hoặc official')
        phases=('ladder',) if stage=='ladder' else ('general','top','round_robin')
        with self.store.lock:
            rows=[self.get(row['id']) for row in self.comp.rows("SELECT id FROM elo_rounds WHERE competition_id=? AND status='completed' ORDER BY CASE phase WHEN 'ladder' THEN 0 WHEN 'general' THEN 1 ELSE 2 END,number",(ident,))]
            rows=[row for row in rows if row['phase'] in phases]
            if not rows:raise Fault('not_found','Chưa có đợt hoàn tất để tính lại')
            initial={t:INITIAL for row in rows for t in row['snapshot']['ratings']} if stage=='ladder' else rows[0]['snapshot']['ratings']
            schedule=rows[0]['config'].get('official',{}).get('k',OFFICIAL_K)
            result=dict(initial_ratings=initial,official_k=schedule,rounds=[])
            excluded=self.comp.elo.exclusions(ident)
            if excluded:result['excluded_teams']=sorted(excluded)
            item=self.comp.get(ident)
            if stage=='official' and item['status']=='completed' and item['config']['format']=='elo':
                result['finalist_count']=item['config']['official']['finalists']
            for row in rows:
                current=row['config'].get('official',{}).get('k',OFFICIAL_K)
                if stage=='official' and json.dumps(current)!=json.dumps(schedule):
                    raise Fault('competition_conflict','Lịch K thay đổi giữa giải; cần kiểm lại cấu hình')
                games=self.games(row['id'])
                scores=self.scores(games)
                for game,score in zip(games,scores):score['fixture_id']=game['id']
                result['rounds'].append(dict(id=row['id'],phase=row['phase'],games=scores,
                    changed_teams=row['snapshot']['changed_teams'] if stage=='ladder' else [],
                    **(dict(teams=list(row['snapshot']['ratings'])) if row['phase']=='round_robin' else {})))
            return result

    @staticmethod
    def scores(games):
        values=[]
        for game in games:
            if game['status']!='completed' or not game['result']:
                raise Fault('competition_conflict','Cả đợt phải xong mới cập nhật Elo')
            result=json.loads(game['result'])
            winner=result['winner'];double=result['reason']=='double_startup_failure'
            red=0 if double or winner=='blue' else .5 if winner is None else 1
            blue=0 if double or winner=='red' else .5 if winner is None else 1
            values.append(dict(id=f"{game['ordinal']:08d}",red=game['red_team_id'],blue=game['blue_team_id'],red_score=red,blue_score=blue,status='completed'))
        return values

    def finish(self,rid,completed_ms):
        with self.store.transaction():
            row=self.get(rid)
            if row['status']=='completed':return row['after_state']
            before=row['snapshot'];games=self.scores(self.games(rid))
            schedule=row['config'].get('official',{}).get('k',OFFICIAL_K)
            standings=None
            if row['phase']=='round_robin':
                standings=round_robin_rank(before['ratings'],games)
                ratings,played,changes=before['ratings'],before['played'],before['changes']
            else:
                ratings,played,changes=apply_round(before['ratings'],games,before['played'],before['changes'],official=None if row['phase']=='ladder' else schedule)
            stage='ladder' if row['phase']=='ladder' else 'official'
            for team,rating in (ratings.items() if row['phase']!='round_robin' else []):
                self.db.execute('INSERT INTO elo_ratings VALUES(?,?,?,?,?,?,?) ON CONFLICT(competition_id,team_id,stage) DO UPDATE SET rating=excluded.rating,games=excluded.games,change_remaining=excluded.change_remaining,bot_hash=excluded.bot_hash',
                    (row['competition_id'],team,stage,rating,played[team],changes[team],before['entries'][team].get('bot_hash')))
            after=dict(ratings=ratings,played=played,changes=changes)
            if standings is not None:after['standings']=standings
            self.db.execute("UPDATE elo_rounds SET status='completed',completed_ms=?,after_state=? WHERE id=?",(completed_ms,json.dumps(after),rid))
            self.comp.accounts.audit('system','elo.round_completed',rid)
            return after
