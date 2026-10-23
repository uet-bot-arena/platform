"""Synthetic retention records; no real GitHub or mail calls."""
from dataclasses import replace
import gzip
import io
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

from arena.runner import digest
from arena.config import benchmark_profile
from arena_api.elo_rounds import RoundBook
from arena_api.retention import Retention
from arena_api.settings import RetentionSettings
import test_competitions as helpers
from test_web_api import APIHarness


class RetentionTests(APIHarness):
    qualified = helpers.CompetitionAPI.qualified

    def setUp(self):
        super().setUp()
        self.store = self.app.state.store
        self.service = self.app.state.service
        self.comp = self.app.state.competitions
        self.comp_item = self.comp.create('test', {'title':'Retention fixtures','max_teams':8,'runtime_id':'sha256:'+'1'*64}, 'retention-config')
        _, credential = self.create_team()
        self.team = credential['account']['team_id']
        self.retention = Retention(self.service, RetentionSettings(replay_games=2))

    def source(self):
        item = self.store.create('submission', self.team, {'runtime':'cpp17','version':'v1.0.0','source':{'files':{'bot.cpp':'fixture'}}})
        folder = self.store.root/'sources'/item['id']
        folder.mkdir(parents=True)
        (folder/'bot.cpp').write_bytes(b'int main(){}')
        return self.store.update(item['id'], source_path=str(folder))

    def test_timing_job_events_and_lease_state_rollback_together_on_interruption(self):
        job=self.store.create('job',self.team,dict(kind='benchmark',status='starting',profile=benchmark_profile().public()))
        with self.store.lock,self.store.db:
            self.store.db.execute('CREATE TABLE atomic_probe(state TEXT)')
            self.store.db.execute("INSERT INTO atomic_probe VALUES('active')")
        with self.assertRaisesRegex(RuntimeError,'interrupted'):
            with self.store.transaction():
                self.store.db.execute("UPDATE atomic_probe SET state='completed'")
                self.service.timing.record(job,dict(platform='windows',runtime_id='fixture'),1000,2000)
                self.store.update(job['id'],status='completed')
                self.store.event(job['id'],'completed',{})
                raise RuntimeError('interrupted')
        self.assertEqual(self.store.db.execute('SELECT state FROM atomic_probe').fetchone()[0],'active')
        self.assertEqual(self.store.get(job['id'])['status'],'starting')
        self.assertEqual(self.store.events(job['id']),[])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM duration_samples').fetchone()[0],0)

    def game(self, *, official=None):
        job = self.store.create('job', self.team, dict(kind='match', status='completed', submission_ids=[],
            official=official, report=dict(result={'winner':'red'}, artifacts=[{'log':'old detailed build log'}], moves=[{}]*100, samples=[{}]*100)))
        with self.store.transaction():
            self.store.db.executemany('INSERT INTO events VALUES(?,?,?)',
                ((job['id'], i, json.dumps(dict(seq=i,type='state',server_unix_ms=1,data={'state':{'payload':'compress me '*20}}))) for i in range(1,1003)))
        return job

    def test_replay_limits_keep_results_official_and_shared_team_recent_games(self):
        games = [self.game() for _ in range(4)]
        ladder = self.game(official={'phase':'ladder','entries':[{'team_id':self.team},{'team_id':'second-team'}]})
        official = self.game(official={'phase':'round_robin','entries':[{'team_id':self.team}]})
        rows_before = len(self.store.list('job'))
        self.retention.run()
        self.assertEqual(len(self.store.list('job')), rows_before)
        self.assertEqual([g['id'] for g in games if self.store.get(g['id']).get('replay_pruned')], [g['id'] for g in games[:3]])
        self.assertEqual(self.store.get(games[0]['id'])['report'],{'result':{'winner':'red'}})
        self.assertEqual(len(self.store.events(ladder['id'],1000)), 2)
        self.assertEqual(len(self.store.events(official['id'])), 1000)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertEqual(self.store.get(official['id'])['report']['result'], {'winner':'red'})
        self.assertNotIn('moves', self.store.get(official['id'],hydrate=False)['report'])
        self.assertEqual(len(self.store.get(official['id'])['report']['moves']),100)
        self.assertEqual(self.retention.run()['replays_removed'], [])

    def test_old_pass_after_25_failed_pushes_keeps_certificate_and_three_source_two_binary_roles(self):
        # The frozen entry and latest Pass are deliberately different versions.
        frozen = self.source()
        frozen_body=self.qualified(self.team,self.comp_item['config'])
        frozen_job=self.store.get(frozen_body['benchmark_id'])
        frozen_report=frozen_job['report'];frozen_report['artifacts'][0].update(source=frozen['source'],artifact_sha256=f'{0:064x}')
        self.store.update(frozen_job['id'],submission_ids=[frozen['id']],report=frozen_report)
        frozen_entry=self.comp.qualification(self.comp_item['config'],self.team,frozen['id'],frozen_job['id'])
        passed = self.source()
        body = self.qualified(self.team, self.comp_item['config'])
        old = self.store.get(body['benchmark_id'])
        report = old['report']
        report['artifacts'][0].update(source=passed['source'],artifact_sha256=f'{1:064x}')
        self.store.update(old['id'], submission_ids=[passed['id']], report=report)
        expected = self.comp.qualification(self.comp_item['config'],self.team,passed['id'],old['id'])
        with self.store.transaction():
            self.store.db.execute("UPDATE competitions SET status='locked',locked_ms=1 WHERE id=?",(self.comp_item['id'],))
            self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',
                (self.comp_item['id'],self.team,frozen['id'],frozen_job['id'],json.dumps(frozen_entry),1))
        failed_sources=[]
        for _ in range(25):
            source=self.source();failed_sources.append(source)
            self.store.create('job',self.team,dict(kind='benchmark',status='failed',submission_ids=[source['id']],
                report=dict(passed=False,samples=[{}]),error=dict(code='illegal_action')))
        (self.service.artifacts/'windows'/('4'*64)).unlink()  # helper's unreferenced synthetic blob
        all_sources=[frozen,passed,*failed_sources]
        for index,source in enumerate(all_sources):
            sha=f'{index:064x}'
            folder=self.service.artifacts/'windows';folder.mkdir(parents=True,exist_ok=True)
            (folder/sha).write_bytes(b'binary fixture '+bytes([index])*1000)
            with self.store.transaction():
                self.store.db.execute('INSERT INTO builds VALUES(?,?,?,?,?,?)',(source['id'],'windows','runtime',sha,'fixture',index))
        status=self.retention.run()
        self.assertEqual(status['blocked_teams'],[])
        remaining={s['id'] for s in all_sources if (self.store.root/'sources'/s['id']).with_suffix('.zip').exists()}
        self.assertEqual(remaining,{frozen['id'],passed['id'],failed_sources[-1]['id']})
        self.assertEqual(len(list((self.service.artifacts/'windows').iterdir())),2)
        self.assertEqual(len([j for j in self.store.list('job',self.team) if j.get('report')]),20)
        certificate=self.store.get(old['id'])
        self.assertIsNone(certificate['report'])
        self.assertNotIn('samples',certificate['qualification'])
        self.assertEqual(self.comp.qualification(self.comp_item['config'],self.team,passed['id'],old['id']),expected)
        self.assertEqual(certificate['qualification']['report_sha256'],digest(report))
        self.assertEqual(self.comp.qualification(self.comp_item['config'],self.team,frozen['id'],frozen_job['id']),frozen_entry)
        raw=self.service.submission_archive(self.store.get(passed['id']))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.read('bot.cpp'),b'int main(){}')
        blob=self.service.artifact_path({},'windows',f'{1:064x}')
        self.assertEqual(gzip.decompress(blob.read_bytes()),b'binary fixture '+b'\1'*1000)

    def test_disk_emergency_removes_practice_oldest_before_ladder_never_official(self):
        self.retention.settings=replace(self.retention.settings,replay_games=50)
        first=self.game();second=self.game()
        ladder=self.game(official={'phase':'ladder','entries':[{'team_id':self.team}]})
        official=self.game(official={'phase':'general','entries':[{'team_id':self.team}]})
        # Stay above 75% through both practice deletions, then fall below it.
        readings=iter([80,80,70,70])
        with patch.object(self.retention,'disk',side_effect=lambda:dict(percent=next(readings),warning=True)):
            status=self.retention.run()
        self.assertEqual(status['emergency_removed'],[first['id'],second['id']])
        self.assertTrue(self.store.events(ladder['id']))
        self.assertTrue(self.store.events(official['id']))
