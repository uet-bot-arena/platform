"""Synthetic display fixture only; never a source of real competition results."""
import json
import uuid

from arena import engine
from arena_api.accounts import now_ms


def seed(store, competitions):
    teams = [row[0] for row in store.db.execute('SELECT id FROM teams ORDER BY created_ms LIMIT 2')]
    for reason in ('threefold_repetition', 'double_startup_failure'):
        comp = competitions.create('browser-fixture', {'title':'Fixture UI · '+('Hòa lặp thế' if reason=='threefold_repetition' else 'Hai bot lỗi startup'), 'turn_ms':15000,'max_teams':2},uuid.uuid4().hex)
        cid = comp['id']
        with store.db:
            for team in teams:
                source = store.create('submission',team,{'version':'v0.0.0-ui-fixture','runtime':'cpp17','source_path':'synthetic-only'})
                benchmark = store.create('job',team,{'kind':'benchmark','status':'completed','submission_ids':[source['id']],'report':{'fixture_only':True},'error':None})
                store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(cid,team,source['id'],benchmark['id'],json.dumps({'version':'v0.0.0-ui-fixture'}),now_ms()))
            for number in (1,2):
                fid = uuid.uuid4().hex
                red,blue = teams if number==1 else teams[::-1]
                result = {'winner':None,'reason':reason}
                job = store.create('job','competition:'+cid,{'kind':'match','status':'completed','submission_ids':[], 'profile':comp['config']['profile'],
                    'report':{'fixture_only':True,'result':result},'error':None})
                store.db.execute('INSERT INTO competition_fixtures VALUES(?,?,?,?,?,?,?,?)',(fid,cid,number,red,blue,'completed',job['id'],json.dumps(result)))
                store.db.execute('INSERT INTO competition_attempts VALUES(?,?,?,\'completed\',NULL,?)',(fid,1,job['id'],now_ms()))
                state = engine.initial_state()
                store.event(job['id'],'state',{'state':state,'counts':engine.counts(state)})
                if reason=='threefold_repetition':
                    for move in [{'from':'a2','to':'b3'},{'from':'b9','to':'a9'},{'from':'b3','to':'a2'},{'from':'a9','to':'b9'}]*2:
                        state = engine.apply(state,move)
                        store.event(job['id'],'state',{'state':state,'counts':engine.counts(state)})
                state = engine.finish(state,None,reason)
                store.event(job['id'],'state',{'state':state,'counts':engine.counts(state)})
                store.event(job['id'],'completed',{'result':result})
            store.db.execute("UPDATE competitions SET status='completed',completed_ms=? WHERE id=?",(now_ms(),cid))
        print('ADJUDICATION_FIXTURE='+json.dumps({'competition_id':cid,'fixture_id':fid,'reason':reason}),flush=True)
