"""Published rule definitions are immutable; active jobs keep their snapshots."""
from copy import deepcopy
import json
import uuid
import time
from arena import experimental
from arena.protocol import Fault
from arena.service import digest
from arena_api import addons


class RuleCatalog:
    def __init__(self, store, accounts):
        self.store,self.accounts=store,accounts
    def create_record(self, kind, team, fields):
        record={'id':uuid.uuid4().hex,'team_id':team,'created_unix_ms':time.time_ns()//1000000,**fields}
        self.store.db.execute('INSERT INTO documents VALUES(?,?,?,?)',(record['id'],kind,team,json.dumps(record)))
        return record
    def update_record(self, ident, **fields):
        record=self.store.get(ident);record.update(fields)
        self.store.db.execute('UPDATE documents SET data=? WHERE id=?',(json.dumps(record),ident))
        return record
    def settings(self):
        rows=self.store.list('experimental_settings')
        return rows[0] if rows else {'enabled':False,'revision':0}
    def command(self, actor, operation, key, body, action):
        if not isinstance(key,str) or not 8<=len(key)<=128:raise Fault('invalid_request','Idempotency-Key of 8..128 characters required')
        operation='ruleset:'+operation;hashed=digest(body)
        with self.store.lock,self.store.db:
            row=self.store.db.execute('SELECT digest,response FROM competition_commands WHERE actor=? AND operation=? AND key=?',(actor,operation,key)).fetchone()
            if row:
                if row[0]!=hashed:raise Fault('idempotency_conflict','Key already used for different input')
                return json.loads(row[1])
            result=action();self.accounts.audit(actor,operation,result.get('id','experimental'))
            self.store.db.execute('INSERT INTO competition_commands VALUES(?,?,?,?,?)',(actor,operation,key,hashed,json.dumps(result)))
            return result
    def toggle(self,actor,body,key):
        def act():
            current=self.settings()
            if body.get('expected_revision')!=current['revision']:raise Fault('admin_conflict','Experimental settings changed; refresh')
            data={'enabled':body['enabled'],'revision':current['revision']+1}
            return self.update_record(current['id'],**data) if current.get('id') else self.create_record('experimental_settings','system',data)
        return self.command(actor,'toggle',key,body,act)
    def listing(self,admin=False):
        rows=self.store.list('ruleset');settings=self.settings()
        return {'enabled':settings['enabled'],'revision':settings['revision'],
                'items':rows if admin else [r for r in rows if r['status']=='published'],
                'modules':deepcopy(addons.MODULES),
                **({'presets':deepcopy(experimental.PRESETS)} if admin else {})}
    def detail(self,ident,admin=False):
        with self.store.lock:
            r=self.store.get(ident,kind='ruleset')
            if not admin and (r['status'] not in ('published','archived') or not r.get('hash')):
                raise Fault('not_found','Ruleset chưa được công bố')
            previous=self.store.get(r['parent_id'],kind='ruleset') if r.get('parent_id') else None
            # Never disclose the contents of an unpublished parent to teams.
            if previous and not admin and not previous.get('hash'):previous=None
            return {**r,'contract':addons.contract(r,previous)}
    def import_package(self,actor,body,key):
        def act():
            if len(self.store.list('ruleset'))>=100:raise Fault('quota_exceeded','Tối đa 100 phiên bản ruleset add-on')
            definition,release_notes=addons.unpack(body['package'])
            result=addons.check(definition)
            fields={'definition':definition,'release_notes':release_notes,'status':'validated','version':1,'revision':1,'experimental':True,'validation':result}
            if body['publish']:
                fields.update(status='published',hash=result['hash'],requires=result['requires'],
                              engine_sha256=experimental.ENGINE_SHA256,sdk_sha256=experimental.sdk_hash(),published_unix_ms=time.time_ns()//1000000)
            return self.create_record('ruleset','system',fields)
        return self.command(actor,'import',key,body,act)
    def create(self,actor,body,key):
        def act():
            if len(self.store.list('ruleset'))>=100:raise Fault('quota_exceeded','At most 100 experimental ruleset versions')
            definition=body['definition'];experimental.validate(definition)
            return self.create_record('ruleset','system',{'definition':definition,'release_notes':addons.notes(body.get('release_notes','')),'status':'draft','version':1,'revision':1,'experimental':True})
        return self.command(actor,'create',key,body,act)
    def mutate(self,actor,ident,verb,body,key):
        def act():
            r=self.store.get(ident,kind='ruleset')
            if r['revision']!=body.get('expected_revision'):raise Fault('admin_conflict','Ruleset changed; refresh')
            if verb=='clone':
                if len(self.store.list('ruleset'))>=100:raise Fault('quota_exceeded','At most 100 experimental ruleset versions')
                return self.create_record('ruleset','system',{'definition':r['definition'],'release_notes':'','status':'draft','version':r['version']+1,'parent_id':ident,'revision':1,'experimental':True})
            if verb=='archive':return self.update_record(ident,status='archived',revision=r['revision']+1)
            if r['status'] not in ('draft','validated'):raise Fault('ruleset_conflict','Published rules are immutable; clone a new version')
            if verb=='edit':
                experimental.validate(body['definition'])
                release_notes=r.get('release_notes','') if body.get('release_notes') is None else addons.notes(body['release_notes'])
                return self.update_record(ident,definition=body['definition'],release_notes=release_notes,status='draft',validation=None,revision=r['revision']+1)
            result=addons.check(r['definition'])
            if verb=='validate':return self.update_record(ident,status='validated',validation=result,revision=r['revision']+1)
            if verb not in ('publish','release') or (verb=='publish' and r['status']!='validated'):raise Fault('ruleset_conflict','Kiểm tra bản nháp trước khi công bố, hoặc dùng Kiểm tra & công bố')
            return self.update_record(ident,status='published',hash=result['hash'],requires=result['requires'],validation=result,
                                     engine_sha256=experimental.ENGINE_SHA256,sdk_sha256=experimental.sdk_hash(),published_unix_ms=time.time_ns()//1000000,revision=r['revision']+1)
        return self.command(actor,verb+':'+ident,key,body,act)
    def resolve(self,ident):
        if not ident:return None
        if not self.settings()['enabled']:raise Fault('experimental_disabled','Ruleset add-on đang tắt; BTC cần bật trước khi tạo job hoặc giải mới')
        r=self.store.get(ident,kind='ruleset')
        if r['status']!='published':raise Fault('ruleset_conflict','Chỉ chọn được ruleset đã Published')
        snapshot={k:r[k] for k in ('id','definition','version','hash','requires','engine_sha256','sdk_sha256')}
        experimental.provider(snapshot)
        return snapshot
