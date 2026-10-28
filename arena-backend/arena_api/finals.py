"""Durable orchestration only: every board/pair remains an ordinary RR2 cup."""
import copy
import json
import uuid
from arena.protocol import Fault
from arena_api.accounts import now_ms
from arena_api.ranking import group_order, pair_winner

SNAKE={'A':[1,8,9,16],'B':[2,7,10,15],'C':[3,6,11,14],'D':[4,5,12,13]}
STAGES={'groups':list(SNAKE),'quarter':['TK1','TK2','TK3','TK4'],'semi':['BK1','BK2'],'medals':['FINAL','BRONZE']}
LABELS={**{k:'Bảng '+k for k in SNAKE},**{f'TK{i}':'Tứ kết '+str(i) for i in range(1,5)},'BK1':'Bán kết 1','BK2':'Bán kết 2','FINAL':'Chung kết','BRONZE':'Tranh hạng ba'}

def snake(teams):
    by_rank={t['online_rank']:t['team_id'] for t in teams}
    return {key:[by_rank[r] for r in ranks] for key,ranks in SNAKE.items()}

class Finals:
    def __init__(self, comp):
        self.comp,self.store,self.db,self.a=comp,comp.store,comp.db,comp.accounts

    def get(self, ident):
        row=self.a.one('SELECT * FROM finals WHERE id=?',(ident,))
        if not row:raise Fault('not_found','Không tìm thấy chung kết')
        for key in ('config','state'):row[key]=json.loads(row[key])
        return row

    def save(self, item, action, actor=None):
        old=self.get(item['id'])
        if old==item:return
        with self.db:
            self.db.execute('UPDATE finals SET status=?,config=?,state=? WHERE id=?',(item['status'],json.dumps(item['config']),json.dumps(item['state']),item['id']))
            self.a.audit(actor or item['config']['actor_id'],'finals.'+action,item['id'])

    def eligible(self, config, teams):
        selected={}
        for entry in teams:
            team=self.a.team(entry['team_id'])
            if team['is_guest']:raise Fault('competition_conflict','Chung kết không nhận đội khách: '+team['name'])
            if hasattr(self.comp,'teams'):self.comp.teams.eligible(team['id'])
            if not self.a.one('SELECT id FROM accounts WHERE team_id=? AND locked=0 AND must_change=0',(team['id'],)):
                raise Fault('competition_conflict','Đội chưa có tài khoản hoạt động: '+team['name'])
            try:
                choice=self.comp.latest_qualified(config,team['id'])
                selected[team['id']]={**choice,'snapshot':self.comp.qualification(config,team['id'],**choice)}
            except Fault as exc:raise Fault('competition_conflict',team['name']+': '+exc.detail) from None
        return selected

    def create(self, actor, body, key):
        def prepare():
            config=self.comp.config_for(dict(body,max_teams=16,format='double_round_robin',scoring=dict(win=1,draw=.5,loss=0)))
            self.eligible(config,body['teams'])
            return config
        def action(common):
            ident=uuid.uuid4().hex
            teams=[dict(t,name=self.a.team(t['team_id'])['name']) for t in body['teams']]
            config=dict(teams=teams,common=common,pause_between_rounds=body.get('pause_between_rounds',False),actor_id=actor)
            state=dict(slots={},bots={},groups={},results={},started_rounds=['groups'])
            self.db.execute("INSERT INTO finals VALUES(?,?,'draft',?,?,?)",(ident,body['name'],now_ms(),json.dumps(config),json.dumps(state)))
            self.a.audit(actor,'finals.create',ident)
            return self.view(ident,True)
        return self.comp.command(actor,'finals-create',key,body,action,prepare)

    def start_groups(self, actor, ident):
        with self.store.lock:
            item=self.get(ident)
            if item['status']=='stopped':raise Fault('competition_conflict','Tự động đã dừng')
            if item['status']=='draft':
                self.comp.check_runtime(item['config']['common'])
                item['state']['bots']=self.eligible(item['config']['common'],item['config']['teams'])
                item['status']='groups';self.save(item,'start_groups',actor)
            self.advance(self.get(ident))
            return self.view(ident,True)

    def start_round(self, actor, ident):
        with self.store.lock:
            item=self.get(ident)
            if item['status'] not in ('groups','knockout'):raise Fault('competition_conflict','Chung kết không chờ vòng mới')
            state=item['state']
            for stage,keys in list(STAGES.items())[1:]:
                if stage not in state['started_rounds'] and all(k in state['slots'] and self.comp.get(state['slots'][k]['competition_id'])['status']=='locked' for k in keys):
                    state['started_rounds'].append(stage);self.save(item,'start_'+stage,actor);self.advance(item);return self.view(ident,True)
            raise Fault('competition_conflict','Chưa đủ cặp đã chốt để bắt đầu vòng tiếp theo')

    def stop(self, actor, ident):
        with self.store.lock:
            item=self.get(ident)
            if item['status']!='completed':item['status']='stopped';self.save(item,'stop',actor)
            return self.view(ident,True)

    def ensure_cup(self, item, slot, teams, stage):
        state=item['state'];actor=item['config']['actor_id'];prefix='final:'+item['id']+':'+slot+':'
        names={t['team_id']:t['name'] for t in item['config']['teams']}
        suffix=' · '+LABELS[slot]+('' if slot in SNAKE else ': '+names[teams[0]]+'–'+names[teams[1]])
        title=item['name'][:100-len(suffix)]+suffix
        body=dict(title=title,max_teams=len(teams),format='double_round_robin')
        cup=self.comp.create(actor,body,prefix+'create',pinned_config=copy.deepcopy(item['config']['common']))
        cid=cup['id']
        if slot not in state['slots']:
            state['slots'][slot]=dict(competition_id=cid,team_ids=teams,stage=stage)
            self.save(item,'create_'+slot)
        cup=self.comp.get(cid)
        if cup['status']=='draft':
            for team in teams:
                pinned=state['bots'][team]
                selected={k:pinned[k] for k in ('submission_id','benchmark_id')}
                current=self.comp.qualification(cup['config'],team,**selected)
                if current!=pinned['snapshot']:raise Fault('provenance_mismatch','Bản bot đã ghim thay đổi')
                self.comp.enter(actor,team,cid,selected,prefix+'enter:'+team,admin=True,pinned=True)
            self.comp.transition(actor,cid,'open',prefix+'open')
        cup=self.comp.get(cid)
        if cup['status']=='open':self.comp.lock(actor,cid,prefix+'lock')
        cup=self.comp.get(cid)
        allowed=stage in state['started_rounds'] or not item['config']['pause_between_rounds']
        if cup['status']=='locked' and allowed:self.comp.transition(actor,cid,'running',prefix+'start')

    def result(self, item, slot):
        state=item['state'];entry=state['slots'].get(slot)
        if not entry or self.comp.get(entry['competition_id'])['status']!='completed':return None
        if slot not in state['results']:
            rows=self.comp.standings(entry['competition_id'],True)['items']
            games=[]
            for fixture in self.comp.rows('SELECT * FROM competition_fixtures WHERE competition_id=? ORDER BY ordinal',(entry['competition_id'],)):
                after=0;last={}
                while fixture['current_job_id']:
                    events,cursor=self.comp.safe_events(fixture['current_job_id'],after)
                    for event in events:
                        if event['type']=='state':last=event['data']
                    if cursor==after:break
                    after=cursor
                result=json.loads(fixture['result']);counts=last.get('counts',{})
                games.append(dict(fixture_id=fixture['id'],ordinal=fixture['ordinal'],red_team_id=fixture['red_team_id'],blue_team_id=fixture['blue_team_id'],
                                  winner=result['winner'] or 'draw',reason=result['reason'],plies=last.get('state',{}).get('ply',0),
                                  pieces={side:sum(counts.get(side,{}).values()) for side in ('red','blue')}))
            state['results'][slot]=dict(pair_winner(rows,games,entry['competition_id']),games=games);self.save(item,'result_'+slot)
        return state['results'][slot]

    def advance(self, item):
        if item['status'] not in ('groups','knockout'):return
        state=item['state'];online={t['team_id']:t['online_rank'] for t in item['config']['teams']}
        groups=snake(item['config']['teams'])
        for slot,teams in groups.items():self.ensure_cup(item,slot,teams,'groups')
        for slot in groups:
            cid=state['slots'][slot]['competition_id']
            if self.comp.get(cid)['status']!='completed':return
        if not state['groups']:
            state['groups']={key:group_order(self.comp.standings(state['slots'][key]['competition_id'],True)['items'],online) for key in groups}
            item['status']='knockout';self.save(item,'rank_groups')
        ranked=state['groups'];top=lambda group,place:ranked[group][place-1]['team_id']
        quarter={'TK1':[top('A',1),top('B',2)],'TK2':[top('C',1),top('D',2)],'TK3':[top('B',1),top('A',2)],'TK4':[top('D',1),top('C',2)]}
        for slot,teams in quarter.items():self.ensure_cup(item,slot,teams,'quarter')
        for slot,source in [('BK1',['TK1','TK2']),('BK2',['TK3','TK4'])]:
            values=[self.result(item,key) for key in source]
            if all(values):self.ensure_cup(item,slot,[v['winner'] for v in values],'semi')
        semis=[self.result(item,key) for key in ('BK1','BK2')]
        if all(semis):
            self.ensure_cup(item,'FINAL',[v['winner'] for v in semis],'medals')
            self.ensure_cup(item,'BRONZE',[v['loser'] for v in semis],'medals')
            final,bronze=[self.result(item,key) for key in ('FINAL','BRONZE')]
            if final and bronze:
                state['podium']=[final['winner'],final['loser'],bronze['winner']]
                item['status']='completed';state['completed_ms']=now_ms();self.save(item,'completed')

    def tick(self):
        with self.store.lock:
            for row in self.a.rows("SELECT id FROM finals WHERE status IN ('groups','knockout') ORDER BY created_ms,id"):
                item=self.get(row['id'])
                try:
                    self.advance(item)
                    if item['state'].pop('error',None):self.save(item,'recovered')
                except Fault as exc:
                    item['state']['error']=exc.detail;self.save(item,'waiting')

    def listing(self, admin=False):
        where='' if admin else " WHERE status<>'draft'"
        return {'items':[self.view(row['id'],admin) for row in self.a.rows('SELECT id FROM finals'+where+' ORDER BY created_ms DESC,id')]}

    def view(self, ident, admin=False):
        with self.store.lock:
            item=self.get(ident)
            if item['status']=='draft' and not admin:raise Fault('not_found','Không tìm thấy chung kết')
            state=item['state'];online={t['team_id']:t['online_rank'] for t in item['config']['teams']}
            slots={}
            for key,slot in state['slots'].items():
                cid=slot['competition_id'];cup=self.comp.get(cid)
                rows=self.comp.standings(cid,True)['items']
                if key in SNAKE:rows=group_order(rows,online)
                result=state['results'].get(key)
                source={'BK1':['TK1','TK2'],'BK2':['TK3','TK4'],'FINAL':['BK1','BK2'],'BRONZE':['BK1','BK2']}.get(key,[])
                slots[key]=dict(slot,label=LABELS[key],status=cup['status'],rows=[{k:r[k] for k in ('team_id','name','points','head_to_head','played','wins','draws','losses','rank')} for r in rows],result=result,counts=self.comp.view(cid,True)['fixture_counts'],games=result.get('games',[]) if result else [],source=source)
            common=item['config']['common']
            return dict(id=item['id'],name=item['name'],status=item['status'],created_ms=item['created_ms'],completed_ms=state.get('completed_ms'),teams=item['config']['teams'],
                        profile=common['profile'],grader_platform=common['grader_platform'],rules=self.comp.public_config(common),pause_between_rounds=item['config']['pause_between_rounds'],
                        slots=slots,podium=state.get('podium',[]),started_rounds=state['started_rounds'],error=state.get('error'))
