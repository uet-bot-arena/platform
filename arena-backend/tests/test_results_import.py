import dataclasses,gzip,hashlib,json
from unittest.mock import patch
from pathlib import Path
from arena_api.publisher import atomic_gzip,Publisher
from arena_api.results import Results
from test_web_api import APIHarness

class ResultImport(APIHarness):
    cid='a'*32
    fid='b'*32
    def setUp(self):
        super().setUp()
        self.source=self.settings.data/'lan-snapshots'
        self.sender=Results(self.app.state.store,self.app.state.accounts,dataclasses.replace(self.settings,publisher=dataclasses.replace(self.settings.publisher,output_dir=self.source)))
        self.files={
            f'competitions/{self.cid}.json':{'id':self.cid,'title':'Bảng A LAN','status':'completed','entries':[{'team_id':'red','name':'Red'},{'team_id':'blue','name':'Blue'}],'config':{'format':'double_round_robin'}},
            f'fixtures/{self.cid}.json':{'items':[{'id':self.fid,'competition_id':self.cid,'ordinal':1,'status':'completed'}],'total':1},
            f'standings/{self.cid}.json':{'items':[{'team_id':'red','points':1}],'total':1},
            f'standings/{self.cid}.csv':'name,points\nRed,1\n',
            f'fixtures/{self.cid}/{self.fid}.json':{'items':[{'seq':1,'type':'state','data':{'state':{'board':[]}}}],'fixture':{'id':self.fid,'status':'completed'}},
        }
        self.write_source()
    def write_source(self):
        for path,value in self.files.items():atomic_gzip(self.source/(path+'.gz'),value.encode() if isinstance(value,str) else value)
    def export(self):return self.sender.export('test','Chung kết thử',[self.cid])
    def post(self,raw,query='preview=1',client=None):
        return (client or self.client).post('/api/v1/admin/results-import?'+query,content=raw,headers={'Content-Type':'application/gzip'})
    def confirm(self,raw):return self.post(raw,'confirm='+hashlib.sha256(raw).hexdigest())
    def test_export_completed_only_and_preview_writes_nothing(self):
        self.files[f'competitions/{self.cid}.json']['status']='running';self.write_source()
        original=self.app.state.results;self.app.state.results=self.sender
        r=self.client.get('/api/v1/admin/results-export',params={'name':'LAN','competitions':self.cid})
        self.app.state.results=original;self.assertEqual(r.status_code,409)
        with self.assertRaises(Exception) as error:self.export()
        self.assertEqual(error.exception.code,'competition_conflict')
        self.files[f'competitions/{self.cid}.json']['status']='completed';self.write_source()
        raw=self.export();before=list(self.settings.data.rglob('*.gz'))
        r=self.post(raw);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['competitions'][0]['replays'],1)
        self.assertEqual(r.json()['competitions'][0]['teams'],2)
        self.assertEqual(list(self.settings.data.rglob('*.gz')),before)
        self.assertEqual(self.app.state.accounts.rows('SELECT * FROM imported_events'),[])
    def test_import_replay_csv_duplicate_and_hide_correction(self):
        raw=self.export();r=self.confirm(raw);self.assertEqual(r.status_code,200,r.text);event=r.json()
        guest=self.other_client()
        for path,value in self.files.items():
            fetched=guest.get('/snapshots/'+path);self.assertEqual(fetched.status_code,200,fetched.text)
            self.assertEqual(fetched.text if path.endswith('.csv') else fetched.json(),value)
        again=self.confirm(raw);self.assertEqual(again.json()['id'],event['id'])
        self.assertEqual(len(guest.get('/snapshots/imported.json').json()['items']),1)
        self.client.post('/api/v1/admin/results-import/'+event['id']+'/hide')
        self.assertEqual(guest.get('/snapshots/competitions/'+self.cid+'.json').status_code,404)
        self.files[f'competitions/{self.cid}.json']['title']='Bảng A sửa';self.write_source()
        corrected=self.export();self.assertNotEqual(corrected,raw)
        self.assertEqual(self.confirm(corrected).status_code,200)
        self.assertEqual(guest.get('/snapshots/competitions/'+self.cid+'.json').json()['title'],'Bảng A sửa')
        publisher=Publisher(self.settings)
        try:publisher.publish()
        finally:publisher.close()
        self.assertEqual(len(guest.get('/snapshots/imported.json').json()['items']),1)
    def test_conflicts_sha_tampering_paths_and_rbac(self):
        raw=self.export();self.assertEqual(self.confirm(raw).status_code,200)
        modified=json.loads(gzip.decompress(raw));modified['name']='Changed name';other=gzip.compress(json.dumps(modified).encode())
        self.assertEqual(self.confirm(other).status_code,409)
        broken=raw[:-1]+bytes([raw[-1]^1])
        self.assertEqual(self.post(broken,'confirm='+hashlib.sha256(raw).hexdigest()).status_code,409)
        self.assertEqual(self.post(broken).status_code,400)
        for path in ('../secret.json',f'fixtures/{"c"*32}/bad.json',f'fixtures/{self.cid}/../../x.json'):
            invalid=json.loads(gzip.decompress(raw));invalid['competitions'][0]['files'][path]={}
            self.assertEqual(self.post(gzip.compress(json.dumps(invalid).encode())).status_code,400)
        guest=self.other_client();self.assertEqual(self.post(raw,client=guest).status_code,403)
        team,_=self.create_team();self.assertEqual(self.post(raw,client=team).status_code,403)
        self.assertEqual(team.get('/api/v1/admin/results-export',params={'name':'LAN','competitions':self.cid}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/admin/results-import?preview=1',content=raw,headers={'Content-Type':'application/gzip','X-CSRF-Token':'bad'}).status_code,403)
    def test_collision_with_online_competition(self):
        cup=self.app.state.competitions.create('test',dict(title='Online match',max_teams=2,runtime_id='sha256:'+'1'*64),'online-create')
        self.app.state.store.db.execute('UPDATE competitions SET id=? WHERE id=?',(self.cid,cup['id']));self.app.state.store.db.commit()
        self.assertEqual(self.post(self.export()).status_code,409)
    def test_compressed_upload_budget_matches_plan(self):
        self.files[f'standings/{self.cid}.csv']='x'*10000;self.write_source();raw=self.export()
        with patch('arena_api.results.MAX_BYTES',len(raw)-1):
            self.assertEqual(self.post(raw).status_code,400)
        self.assertEqual(self.app.state.accounts.rows('SELECT * FROM imported_events'),[])

    def test_decompressed_upload_budget_rejects_a_gzip_bomb(self):
        raw=gzip.compress(b'\0'*(65*1024*1024))
        response=self.post(raw)
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(response.json()['error']['detail'],'File kết quả sau giải nén tối đa 64 MB')

    def test_real_export_still_previews_with_the_decompression_limit(self):
        response=self.post(self.export())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['competitions'][0]['id'],self.cid)

    def test_finals_export_includes_children_and_imported_catalog(self):
        final=dict(id='f'*32,name='Chung kết LAN',status='completed',teams=[],slots={'A':dict(competition_id=self.cid)},podium=[])
        atomic_gzip(self.source/('finals/'+final['id']+'.json.gz'),final)
        raw=self.sender.export('test','Finals event',[],[final['id']])
        payload=json.loads(gzip.decompress(raw));self.assertEqual([c['id'] for c in payload['competitions']],[self.cid])
        self.assertEqual(self.post(raw).json()['finals'],[final]);r=self.confirm(raw);self.assertEqual(r.status_code,200,r.text)
        fetched=self.other_client().get('/snapshots/finals/'+final['id']+'.json');self.assertEqual(fetched.json(),final)
        publisher=Publisher(self.settings)
        try:publisher.publish()
        finally:publisher.close()
        self.assertEqual(self.other_client().get('/snapshots/finals.json').json()['items'],[final])
        self.assertEqual(self.confirm(raw).json()['id'],r.json()['id'])
        self.client.post('/api/v1/admin/results-import/'+r.json()['id']+'/hide')
        publisher=Publisher(self.settings)
        try:publisher.publish()
        finally:publisher.close()
        self.assertEqual(self.other_client().get('/snapshots/finals.json').json()['items'],[])
        self.assertEqual(self.other_client().get('/snapshots/finals/'+final['id']+'.json').status_code,404)
        payload['finals'][0]['slots']['B']=dict(competition_id='z'*32)
        self.assertEqual(self.post(gzip.compress(json.dumps(payload).encode())).status_code,400)
