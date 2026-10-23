"""Persistence tests use synthetic qualified records, never timing measurements."""
import json
import uuid
from unittest.mock import patch
from arena.protocol import Fault
from arena_api.elo_rounds import RoundBook
from test_web_api import APIHarness
import test_competitions as helpers


class EloFixture(APIHarness):
    post=helpers.CompetitionAPI.post
    qualified=helpers.CompetitionAPI.qualified

    def setUp(self):
        super().setUp()
        self.store,self.comp=self.app.state.store,self.app.state.competitions
        image=patch('arena.service.Service.runtime_for',return_value='sha256:'+'1'*64)
        image.start();self.addCleanup(image.stop)
        self.item=self.post(self.client,'/admin/competitions',dict(title='Round storage',max_teams=8,match_ms=60000),expected=201)
        self.ident=self.item['id'];self.book=RoundBook(self.comp);self.entries={}
        for i in range(4):
            _,issued=self.create_team('Elo '+str(i),'elo-'+str(i))
            team=issued['account']['team_id'];body=self.qualified(team,self.item['config'])
            entry=self.comp.qualification(self.item['config'],team,body['submission_id'],body['benchmark_id'])
            self.entries[team]=dict(entry,github_sha=f'{i+1:040x}',bot_hash=f'bot-{i}')

    def complete_games(self,rid,reverse=False):
        games=self.book.games(rid)
        for game in reversed(games) if reverse else games:
            self.store.db.execute("UPDATE elo_games SET status='completed',result=? WHERE id=?",(json.dumps(dict(winner='red',reason='base_capture')),game['id']))
        self.store.db.commit()



class EloRounds(EloFixture):
    def test_snapshot_pins_sha_and_round_commit_survives_recreation_without_double_points(self):
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        saved=self.book.games(rid)[0]['red_sha']
        for entry in self.entries.values():entry['github_sha']='f'*40
        self.assertEqual(self.book.games(rid)[0]['red_sha'],saved)
        with self.assertRaises(Fault):self.book.finish(rid,2000)
        self.assertEqual(self.comp.rows('SELECT * FROM elo_ratings'),[])
        with self.assertRaises(Fault):self.book.start(self.ident,'ladder',2,2000,self.entries)
        self.complete_games(rid,reverse=True)
        result=self.book.finish(rid,3000)
        self.assertTrue(all(count==2 for count in result['played'].values()))
        self.assertEqual(RoundBook(self.comp).finish(rid,4000),result)
        self.assertEqual(self.book.get(rid)['completed_ms'],3000)
        self.assertEqual(len(self.comp.rows('SELECT * FROM elo_ratings')),4)

    def test_official_uses_ladder_at_deadline_not_round_that_finishes_later(self):
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid);early=self.book.finish(rid,2000)['ratings']
        rid=self.book.start(self.ident,'ladder',2,3000,self.entries)
        self.complete_games(rid);self.book.finish(rid,5000)
        seeded=self.book.seed_official(self.ident,self.entries,4000)
        self.assertEqual(seeded,early)
        general=self.book.start(self.ident,'general',1,6000,self.entries)
        self.assertEqual(self.book.get(general)['snapshot']['ratings'],early)
        self.assertTrue(all(v==0 for v in self.book.get(general)['snapshot']['played'].values()))
        self.complete_games(general);self.book.finish(general,7000)
        self.assertEqual(self.book.seed_official(self.ident,self.entries,4000),early)
        self.assertTrue(all(r['games']==2 for r in self.comp.rows("SELECT * FROM elo_ratings WHERE stage='official'")))

    def test_failed_commit_rolls_back_all_ratings_and_round_status(self):
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid)
        with patch.object(self.comp.accounts,'audit',side_effect=RuntimeError('synthetic failed commit')):
            with self.assertRaises(RuntimeError):self.book.finish(rid,2000)
        self.assertEqual(self.book.get(rid)['status'],'running')
        self.assertEqual(self.comp.rows('SELECT * FROM elo_ratings'),[])
        self.assertTrue(all(v==2 for v in self.book.finish(rid,3000)['played'].values()))

    def test_new_sha_with_identical_bot_does_not_reset_change_boost(self):
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid);self.book.finish(rid,2000)
        for entry in self.entries.values():entry['github_sha']='e'*40
        changed=next(iter(self.entries));self.entries[changed]['bot_hash']='new-source'
        rid=self.book.start(self.ident,'ladder',2,3000,self.entries)
        snapshot=self.book.get(rid)['snapshot']
        self.assertEqual(snapshot['changed_teams'],[changed])
        self.assertEqual(snapshot['changes'][changed],10)
        self.assertTrue(all(v==8 for t,v in snapshot['changes'].items() if t!=changed))

    def test_export_recomputes_exact_stored_ratings_counts_and_hides_source(self):
        import test_recompute_elo as tool
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid);self.book.finish(rid,2000)
        self.book.seed_official(self.ident,self.entries,2000)
        for phase,stamp in (('general',3000),('top',5000)):
            rid=self.book.start(self.ident,phase,1,stamp,self.entries)
            self.complete_games(rid);self.book.finish(rid,stamp+1000)
        for stage in ('ladder','official'):
            exported=self.book.export(self.ident,stage)
            serialized=json.dumps(exported)
            for private in ('source_path','artifact_sha256','benchmark_report','runtime_id'):
                self.assertNotIn(private,serialized)
            result=tool.module.recompute(exported)
            stored=self.comp.rows('SELECT * FROM elo_ratings WHERE competition_id=? AND stage=?',(self.ident,stage))
            self.assertEqual(result['ratings'],{r['team_id']:r['rating'] for r in stored})
            self.assertEqual(result['ladder_played' if stage=='ladder' else 'official_played'],{r['team_id']:r['games'] for r in stored})

    def test_round_robin_persists_all_pairs_both_colors_and_never_changes_elo(self):
        import test_recompute_elo as tool
        initial=self.book.seed_official(self.ident,self.entries,0)
        before=self.comp.rows("SELECT * FROM elo_ratings WHERE stage='official' ORDER BY team_id")
        rid=self.book.start(self.ident,'round_robin',1,1000,self.entries)
        games=self.book.games(rid)
        self.assertEqual(len(games),12)
        self.assertEqual(len({(g['red_team_id'],g['blue_team_id']) for g in games}),12)
        self.assertEqual([g['id'] for g in games],sorted(g['id'] for g in games))
        self.complete_games(rid,reverse=True)
        result=self.book.finish(rid,2000)
        self.assertEqual(result['ratings'],initial)
        self.assertTrue(all(v==0 for v in result['played'].values()))
        self.assertEqual(self.comp.rows("SELECT * FROM elo_ratings WHERE stage='official' ORDER BY team_id"),before)
        self.assertTrue(all(r['played']==6 and r['points']==3 for r in result['standings']))
        exported=self.book.export(self.ident,'official')
        replayed=tool.module.recompute(exported)
        self.assertEqual(replayed['standings'],result['standings'])
        self.assertEqual(replayed['ratings'],initial)
        self.assertEqual(self.book.finish(rid,3000),result)

    def test_latest_pass_at_boundary_keeps_previous_pass_for_checking_failed_or_later_verdict(self):
        team=next(iter(self.entries));entry=self.entries[team]
        with self.store.lock,self.store.db:
            self.store.db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms) VALUES(?,1,'owner','repo',1,1)",(team,))
            for i,(state,finished) in enumerate((('pass',100),('pass',501),('checking',None),('fail',400)),1):
                sha=f'{i:040x}'
                self.store.db.execute('INSERT INTO github_commits(team_id,sha,received_ms,submission_id,bot_hash) VALUES(?,?,?,?,?)',(team,sha,i*50,entry['submission_id'],'same-bot'))
                self.store.db.execute('INSERT INTO github_observations(team_id,sha,observed_ms) VALUES(?,?,?)',(team,sha,i*50))
                self.store.db.execute('INSERT INTO github_checks(team_id,sha,purpose,job_id,state,finished_ms) VALUES(?,?,?,?,?,?)',(team,sha,self.ident,entry['benchmark_id'],state,finished))
        at_boundary=self.book.latest_passes(self.ident,500)
        self.assertEqual(at_boundary[team]['github_sha'],f'{1:040x}')
        self.assertEqual(self.book.latest_passes(self.ident,502)[team]['github_sha'],f'{2:040x}')
