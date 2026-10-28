"""One-way publication of public LAN snapshots; never import accounts or bot artifacts."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import uuid
import zlib

from arena.protocol import Fault, strict_json
from arena_api.accounts import now_ms
from arena_api.publisher import atomic_gzip
from arena_api.snapshot_routes import PUBLIC_PATH, ID
import re

MAX_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
FORMAT = 'uet-arena-results/1'


def belongs(path, cid):
    return bool(PUBLIC_PATH.fullmatch(path) and (path in (
        f'competitions/{cid}.json', f'fixtures/{cid}.json', f'standings/{cid}.json', f'standings/{cid}.csv')
        or path.startswith(f'fixtures/{cid}/') or path.startswith(f'standings/{cid}/')))


def event_view(row):
    return {k:json.loads(row[k]) if k in ('competitions','finals') else row[k] for k in ('id','name','sha256','imported_ms','hidden_ms','competitions','finals')}


def listing(db):
    rows=db.execute('SELECT id,name,sha256,imported_ms,hidden_ms,competitions,finals FROM imported_events WHERE hidden_ms IS NULL ORDER BY imported_ms DESC,id').fetchall()
    return {'items':[dict(zip(('id','name','sha256','imported_ms','hidden_ms','competitions','finals'),r)) | {'competitions':json.loads(r[5]),'finals':json.loads(r[6])} for r in rows]}


class Results:
    def __init__(self, store, accounts, settings):
        self.store,self.db,self.accounts,self.settings=store,store.db,accounts,settings
        self.output=settings.publisher.output_dir or settings.data/'snapshots'
        self.root=(settings.data/'imported').resolve()

    def index(self):
        with self.store.lock:return listing(self.db)

    def publish_index(self):
        # Publisher and the import handler may publish this index concurrently.
        temporary=self.output/('.imported-'+uuid.uuid4().hex+'.json.gz')
        try:
            atomic_gzip(temporary, self.index())
            temporary.replace(self.output/'imported.json.gz')
        finally:temporary.unlink(missing_ok=True)

    def export(self, actor, name, ids, final_ids=()):
        ids=list(ids);finals=[]
        for fid in final_ids:
            if not re.fullmatch(ID,fid):raise Fault('invalid_request','Mã chung kết không hợp lệ')
            path=self.output/f'finals/{fid}.json.gz'
            if not path.is_file():raise Fault('not_found','Chưa có snapshot chung kết')
            final=strict_json(gzip.decompress(path.read_bytes()))
            if final.get('status')!='completed':raise Fault('competition_conflict','Chung kết chưa hoàn tất: '+final.get('name',fid))
            for slot in final['slots'].values():
                if slot['competition_id'] not in ids:ids.append(slot['competition_id'])
            finals.append(final)
        if not name.strip() or not ids or len(set(ids))!=len(ids):
            raise Fault('invalid_request','Chọn các giải khác nhau và nhập tên sự kiện')
        competitions=[]
        for cid in ids:
            if not re.fullmatch(ID,cid):raise Fault('invalid_request','Mã giải không hợp lệ')
            path=self.output/f'competitions/{cid}.json.gz'
            if not path.is_file():raise Fault('not_found','Chưa có snapshot của giải')
            competition=json.loads(gzip.decompress(path.read_bytes()))
            if competition.get('status')!='completed':raise Fault('competition_conflict','Giải chưa hoàn tất: '+competition.get('title',cid))
            paths=[path,self.output/f'fixtures/{cid}.json.gz',self.output/f'standings/{cid}.json.gz',self.output/f'standings/{cid}.csv.gz']
            paths+=sorted((self.output/f'fixtures/{cid}').glob('*.json.gz'))+sorted((self.output/f'standings/{cid}').glob('*.json.gz'))
            files={}
            for item in paths:
                relative=item.relative_to(self.output).as_posix()[:-3]
                if not belongs(relative,cid) or item.is_symlink() or not item.is_file():raise Fault('invalid_request','Snapshot không hợp lệ hoặc chưa đủ')
                raw=gzip.decompress(item.read_bytes())
                files[relative]=raw.decode('utf-8') if relative.endswith('.csv') else strict_json(raw)
            competitions.append({'id':cid,'files':files})
        envelope={'format':FORMAT,'name':name.strip(),'exported_ms':now_ms(),'origin':self.settings.origin,'competitions':competitions}
        if finals:envelope['finals']=finals
        raw=json.dumps(envelope,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        with self.store.transaction():self.accounts.audit(actor,'results.export',','.join(ids))
        return gzip.compress(raw,mtime=0)

    def validate(self, raw):
        if len(raw)>MAX_BYTES:raise Fault('invalid_request','File kết quả tối đa 64 MB')
        try:
            d=zlib.decompressobj(16 + zlib.MAX_WBITS)
            out=d.decompress(raw, MAX_JSON_BYTES + 1)
            if len(out)>MAX_JSON_BYTES or not d.eof or d.unused_data:
                raise Fault('invalid_request','File kết quả sau giải nén tối đa 64 MB')
            value=strict_json(out)
        except (OSError,EOFError,ValueError,zlib.error) as exc:
            raise Fault('invalid_request','File gzip hoặc JSON không hợp lệ') from exc
        if not isinstance(value,dict) or value.get('format')!=FORMAT or not isinstance(value.get('name'),str) or not value['name'].strip():
            raise Fault('invalid_request','Định dạng kết quả không hợp lệ')
        cups=value.get('competitions')
        if not isinstance(cups,list) or not cups:raise Fault('invalid_request','File không có giải đấu')
        metadata=[];seen=set()
        for cup in cups:
            if not isinstance(cup,dict) or not isinstance(cup.get('id'),str) or not re.fullmatch(ID,cup['id']) or cup['id'] in seen:
                raise Fault('invalid_request','Mã giải không hợp lệ hoặc bị lặp')
            cid=cup['id'];seen.add(cid);files=cup.get('files')
            if not isinstance(files,dict) or not files:raise Fault('invalid_request','Thiếu snapshot của giải')
            for path,content in files.items():
                if not belongs(path,cid) or (not isinstance(content,str) if path.endswith('.csv') else not isinstance(content,dict)):
                    raise Fault('invalid_request','Đường dẫn hoặc nội dung snapshot không hợp lệ')
            competition=files.get(f'competitions/{cid}.json');fixtures=files.get(f'fixtures/{cid}.json')
            if not isinstance(competition,dict) or competition.get('id')!=cid or competition.get('status')!='completed' or not isinstance(fixtures,dict) or not isinstance(files.get(f'standings/{cid}.json'),dict) or not isinstance(files.get(f'standings/{cid}.csv'),str):
                raise Fault('invalid_request','Giải phải hoàn tất và có đủ bảng điểm, lịch đấu')
            entries=competition.get('entries');config=competition.get('config')
            if (not isinstance(entries,list) or not isinstance(config,dict) or not isinstance(fixtures.get('items'),list)
                    or any(k in fixtures and (type(fixtures[k]) is not int or fixtures[k]<0) for k in ('history_total','total'))):
                raise Fault('invalid_request','Thông tin giải không hợp lệ')
            metadata.append({'id':cid,'title':competition.get('title',cid),'format':config.get('format'),'teams':len(entries),
                'games':fixtures.get('history_total',fixtures.get('total',len(fixtures.get('items',[])))),
                'replays':sum(p.startswith(f'fixtures/{cid}/') and bool(v.get('items')) and not v.get('replay_pruned') for p,v in files.items() if isinstance(v,dict))})
        finals=value.get('finals',[])
        if not isinstance(finals,list):raise Fault('invalid_request','Danh sách chung kết không hợp lệ')
        final_seen=set()
        for final in finals:
            if (not isinstance(final,dict) or not isinstance(final.get('id'),str) or not re.fullmatch(ID,final['id']) or final['id'] in final_seen
                    or final.get('status')!='completed' or not isinstance(final.get('name'),str) or not isinstance(final.get('slots'),dict)
                    or not final['slots'] or not isinstance(final.get('teams'),list)
                    or any(not isinstance(slot,dict) or slot.get('competition_id') not in seen for slot in final['slots'].values())):
                raise Fault('invalid_request','Chung kết phải hoàn tất và có đủ các giải con trong file')
            final_seen.add(final['id'])
        return value,metadata,hashlib.sha256(raw).hexdigest()

    def conflicts(self, metadata, finals=()):
        for cup in metadata:
            if self.accounts.one('SELECT id FROM competitions WHERE id=?',(cup['id'],)):
                raise Fault('competition_conflict','Mã giải đã có trên hub này: '+cup['id'])
            for event in self.index()['items']:
                if any(c['id']==cup['id'] for c in event['competitions']):raise Fault('competition_conflict','Mã giải đã thuộc sự kiện khác: '+cup['id'])
        for final in finals:
            if self.accounts.one('SELECT id FROM finals WHERE id=?',(final['id'],)) or any(any(f['id']==final['id'] for f in e['finals']) for e in self.index()['items']):
                raise Fault('competition_conflict','Mã chung kết đã tồn tại: '+final['id'])

    def ingest(self, actor, raw, confirm=None):
        if confirm is not None and hashlib.sha256(raw).hexdigest()!=confirm:
            raise Fault('idempotency_conflict','SHA không khớp file đã xem trước')
        value,metadata,sha=self.validate(raw)
        finals=value.get('finals',[])
        if confirm is not None and confirm!=sha:raise Fault('idempotency_conflict','SHA không khớp file đã xem trước')
        with self.store.lock:
            old=self.accounts.one('SELECT * FROM imported_events WHERE sha256=?',(sha,))
            if old:return dict(event_view(old),duplicate=True)
            self.conflicts(metadata,finals)
        preview={'name':value['name'].strip(),'sha256':sha,'competitions':metadata,'finals':finals}
        if confirm is None:return preview
        stage=self.root/('.tmp-'+uuid.uuid4().hex);target=self.root/sha[:16]
        try:
            stage.mkdir(parents=True)
            for cup in value['competitions']:
                for path,content in cup['files'].items():
                    atomic_gzip(stage/(path+'.gz'),content.encode('utf-8') if isinstance(content,str) else content)
            for final in finals:atomic_gzip(stage/('finals/'+final['id']+'.json.gz'),final)
            (stage/'sha256').write_text(sha,encoding='ascii')
            with self.store.transaction():
                old=self.accounts.one('SELECT * FROM imported_events WHERE sha256=?',(sha,))
                if old:return dict(event_view(old),duplicate=True)
                self.conflicts(metadata,finals)
                if target.exists():
                    if not (target/'sha256').is_file() or (target/'sha256').read_text(encoding='ascii')!=sha:
                        raise Fault('competition_conflict','Thư mục sự kiện đã tồn tại; không ghi đè')
                else:stage.rename(target)
                ident=uuid.uuid4().hex
                self.db.execute('INSERT INTO imported_events(id,name,sha256,imported_ms,actor_id,hidden_ms,competitions,finals) VALUES(?,?,?,?,?,NULL,?,?)',(ident,preview['name'],sha,now_ms(),actor,json.dumps(metadata,ensure_ascii=False),json.dumps(finals,ensure_ascii=False)))
                self.accounts.audit(actor,'results.import',ident)
            self.publish_index()
            return self.get(ident)
        finally:
            if stage.exists() and stage.parent==self.root and stage.name.startswith('.tmp-'):shutil.rmtree(stage)

    def get(self, ident):
        with self.store.lock:row=self.accounts.one('SELECT * FROM imported_events WHERE id=?',(ident,))
        if not row:raise Fault('not_found','Không có sự kiện này')
        return event_view(row)

    def hide(self, actor, ident):
        with self.store.transaction():
            self.get(ident)
            self.db.execute('UPDATE imported_events SET hidden_ms=COALESCE(hidden_ms,?) WHERE id=?',(now_ms(),ident))
            self.accounts.audit(actor,'results.hide',ident)
        self.publish_index();return self.get(ident)

    def snapshot_path(self, name):
        with self.store.lock:
            for event in self.index()['items']:
                if any(belongs(name,cup['id']) for cup in event['competitions']) or any(name=='finals/'+f['id']+'.json' for f in event['finals']):
                    path=self.root/event['sha256'][:16]/(name+'.gz')
                    if path.is_file() and not path.is_symlink():return path
        return None
