"""Scheduler tests use synthetic qualified records/results, not hardware timings."""
import json
from unittest.mock import patch,PropertyMock
from arena.elo import round_robin_rank
from arena.ladder_clock import boundary
from test_elo_rounds import EloFixture
import test_competitions as competition_helpers


class EloScheduling(EloFixture):
    def test_one_tick_fills_free_slots_and_reconcile_skips_active_reports(self):
        self.configure()
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        with patch('arena.service.Service.worker_count',new_callable=PropertyMock,return_value=4), patch.object(self.comp.elo,'advance'):
            self.comp.elo.tick()
            self.assertEqual(self.comp.one("SELECT COUNT(*) AS n FROM elo_games WHERE round_id=? AND status='queued'",(rid,))['n'],4)
            self.assertEqual(self.app.state.service.pending.qsize(),4)
            with patch.object(self.store,'get',side_effect=AssertionError('hydrated unfinished report')):
                self.comp.elo.reconcile()
            self.comp.elo.tick()
            self.assertEqual(self.app.state.service.pending.qsize(),4,'Repeated tick exceeded the slot budget')

    def test_live_fixture_checks_visibility_without_building_all_standings(self):
        self.configure()
        self.book.start(self.ident,'ladder',1,1000,self.entries)
        fixture=self.comp.one('SELECT id FROM elo_games')['id']
        with patch.object(self.comp,'view',side_effect=AssertionError('full standings built')):
            self.assertEqual(self.comp.live_fixture(self.ident,fixture,admin=True)['id'],fixture)
        with self.store.transaction():self.store.db.execute("UPDATE competitions SET status='draft' WHERE id=?",(self.ident,))
        from arena.protocol import Fault
        with self.assertRaises(Fault):self.comp.live_fixture(self.ident,fixture)

    def test_team_game_lookup_work_is_bounded_by_own_games(self):
        self.configure();team=next(iter(self.entries));others=[t for t in self.entries if t!=team]
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        with self.store.transaction():
            for i in range(1000):
                self.store.db.execute("INSERT INTO elo_games(id,round_id,ordinal,red_team_id,blue_team_id,red_sha,blue_sha,red_entry,blue_entry,status) VALUES(?,?,?,?,?,?,?,?,?,'pending')",
                    (rid+'-extra-'+str(i),rid,i+10,others[0],others[1],'f'*40,'e'*40,json.dumps(self.entries[others[0]]),json.dumps(self.entries[others[1]])))
        steps=0
        def progress():
            nonlocal steps
            steps+=1000
            return 0
        with self.store.lock:
            self.store.db.set_progress_handler(progress,1000)
            try:value=self.comp.elo.team_view(team)
            finally:self.store.db.set_progress_handler(None,0)
        self.assertEqual(len(value['items'][0]['current']['games']),2)
        self.assertLess(steps,3000,'Own-team page scanned unrelated games')

    def test_team_page_does_not_load_other_teams_frozen_entries(self):
        import tracemalloc
        self.configure();team=next(iter(self.entries))
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        snapshot=self.book.get(rid)['snapshot']
        for i in range(1000):
            other=f'{i+1000000:032x}'
            snapshot['entries'][other]=dict(github_sha='f'*40,private_padding='x'*2048)
            snapshot['ratings'][other]=1500
        with self.store.transaction():
            self.store.db.execute('UPDATE elo_rounds SET snapshot=? WHERE id=?',(json.dumps(snapshot),rid))
        tracemalloc.start()
        try:
            value=self.comp.elo.team_view(team)
            _,peak=tracemalloc.get_traced_memory()
            current=value['items'][0]['current']
            self.assertEqual(current['sha'],self.entries[team]['github_sha'])
            self.assertEqual(len(current['games']),2)
            self.assertNotIn('private_padding',json.dumps(value))
            self.assertLess(peak,1024**2,'Team page decoded unrelated frozen entries')
        finally:tracemalloc.stop()

    def test_competition_view_projects_public_entry_fields_before_loading_rows(self):
        import tracemalloc
        self.configure();base=dict(next(iter(self.entries.values())))
        base['source']=dict(files={f'header-{i}.hpp':'3'*64 for i in range(64)})
        with self.store.transaction():
            for i in range(1000):
                team=f'{i+1000000:032x}';name='Capacity '+str(i)
                self.store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,'','','[]',1)",(team,name,name.casefold()))
                self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,1)',(self.ident,team,base['submission_id'],base['benchmark_id'],json.dumps(dict(base,team_id=team))))
        tracemalloc.start()
        try:
            value=self.comp.view(self.ident)
            _,peak=tracemalloc.get_traced_memory()
            self.assertEqual(len(value['entries']),1004)
            self.assertTrue(all(set(e)=={'team_id','name','version','accepted_ms'} for e in value['entries']))
            self.assertLess(peak,2*1024**2,'Competition list decoded private qualification records')
        finally:tracemalloc.stop()

    def configure(self,deadline=9_000_000_000_000,group=2):
        cfg=dict(self.item['config'],format='elo',ladder=dict(round_hours=2),
                 official=dict(general_rounds=3,group_size=group,finalists=2,k=[[20,32],[None,16]]))
        with self.store.transaction():
            self.store.db.execute("UPDATE competitions SET status='open',config=?,submission_deadline=? WHERE id=?",(json.dumps(cfg),deadline,self.ident))
            for team,entry in self.entries.items():
                self.store.db.execute('INSERT INTO competition_entries VALUES(?,?,?,?,?,?)',(self.ident,team,entry['submission_id'],entry['benchmark_id'],json.dumps(entry),1))
        return self.comp.get(self.ident)

    def test_fixed_calendar_skips_busy_boundary_and_takes_snapshot_at_next_fixed_time(self):
        item=self.configure();scheduler=self.comp.elo
        start=boundary(1_792_728_000_000)
        with patch.object(scheduler.book,'latest_passes',return_value=self.entries) as latest:
            scheduler.advance(item,start+1)
            scheduler.advance(item,start+7_200_000)
            latest.assert_called_once_with(self.ident,start+7_200_000)
            rid=self.comp.one('SELECT id FROM elo_rounds')['id']
            scheduler.advance(item,start+14_400_000)  # busy: consume this fixed boundary
            self.complete_games(rid);scheduler.book.finish(rid,start+14_400_100)
            scheduler.advance(item,start+14_400_200)
            self.assertEqual(len(self.comp.rows('SELECT id FROM elo_rounds')),1)
            scheduler.advance(item,start+21_600_000)
            self.assertEqual(latest.call_args.args,(self.ident,start+21_600_000))
            self.assertEqual(len(self.comp.rows('SELECT id FROM elo_rounds')),2)

    def test_scheduler_runs_general_then_unbarriered_round_robin_and_publishes_seeds(self):
        item=self.configure(deadline=1000);scheduler=self.comp.elo
        with self.store.transaction():self.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(self.ident,))
        jobs=[]
        for i in range(80):
            scheduler.advance(self.comp.get(self.ident),2000+i)
            scheduler.dispatch()
            try:jid=self.app.state.service.pending.get_nowait()
            except Exception:continue
            self.app.state.service.pending.task_done()
            job=self.store.get(jid);jobs.append(job)
            competition_helpers.CompetitionAPI.finish(self,job,winner='red')
            scheduler.reconcile()
            if self.comp.get(self.ident)['status']=='completed':break
        scheduler.advance(self.comp.get(self.ident),3000)
        final=self.comp.get(self.ident)
        self.assertEqual(final['status'],'completed')
        self.assertEqual(len(jobs),14)
        self.assertEqual([r['phase'] for r in self.comp.rows('SELECT phase FROM elo_rounds ORDER BY started_ms')],['general']*3+['round_robin'])
        rr=self.comp.one("SELECT id FROM elo_rounds WHERE phase='round_robin'")['id']
        self.assertEqual(final['config']['finalists'],scheduler.book.get(rr)['after_state']['standings'])
        self.assertEqual(len(final['config']['finalists']),2)
        self.assertTrue(all(job['official']['phase']!='ladder' for job in jobs))
        guest=self.other_client()
        for path in ('','/standings','/fixtures','/watch','/elo/export'):
            response=guest.get('/api/v1/competitions/'+self.ident+path)
            self.assertEqual(response.status_code,200,response.text)
            for private in ('source_path','artifact_sha256','benchmark_report','runtime_id'):
                self.assertNotIn(private,response.text)
        board=guest.get('/api/v1/competitions/'+self.ident+'/fixtures/'+jobs[-1]['official']['fixture_id']+'/events')
        self.assertEqual(board.status_code,200)
        standings=guest.get('/api/v1/competitions/'+self.ident+'/standings').json()
        self.assertEqual(standings['ranking'],'round_robin_points')
        self.assertEqual(len(standings['matrix']),2)
        self.assertEqual([r['team_id'] for r in standings['items']],[r['team_id'] for r in final['config']['finalists']])

    def test_small_season_starts_round_robin_without_general(self):
        self.configure(deadline=1000,group=64)
        with self.store.transaction():self.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(self.ident,))
        self.comp.elo.advance(self.comp.get(self.ident),2000)
        row=self.comp.one('SELECT id,phase FROM elo_rounds')
        self.assertEqual(row['phase'],'round_robin')
        self.assertEqual(len(self.book.games(row['id'])),12)

    def test_infrastructure_retry_does_not_score_and_stops_after_configured_k(self):
        self.configure(deadline=1000,group=64)
        with self.store.transaction():self.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(self.ident,))
        scheduler=self.comp.elo;scheduler.advance(self.comp.get(self.ident),2000)
        for number in range(4):
            scheduler.dispatch()
            jid=self.app.state.service.pending.get_nowait();self.app.state.service.pending.task_done()
            self.store.update(jid,status='failed',error=dict(code='worker_lost',detail='synthetic lost worker'))
            scheduler.reconcile()
        row=self.comp.one("SELECT * FROM elo_games WHERE auto_retries=3")
        self.assertEqual(row['status'],'attention')
        self.assertEqual(self.comp.one('SELECT status FROM elo_rounds')['status'],'attention')
        self.assertTrue(all(r['games']==0 for r in self.comp.rows('SELECT * FROM elo_ratings')))
        path=f'/admin/competitions/{self.ident}/fixtures/{row["id"]}'
        attempts=self.client.get('/api/v1'+path+'/attempts')
        self.assertEqual(attempts.status_code,200)
        self.assertEqual(len(attempts.json()['items']),4)
        self.post(self.client,path+'/retry')
        self.assertEqual(self.comp.one('SELECT status FROM elo_rounds')['status'],'running')
        self.assertEqual(self.comp.fixture(self.ident,row['id'])['auto_retries'],0)

    def test_api_accepts_large_elo_defaults_but_requires_deadline_and_valid_selection(self):
        body=dict(title='API Elo defaults',format='elo',max_teams=1000,match_ms=60000,submission_deadline=9_000_000_000_000)
        item=self.post(self.client,'/admin/competitions',body,expected=201)
        self.assertEqual(item['config']['official'],dict(match_ms=10000,general_rounds=20,group_size=64,finalists=16,k=[[20,32],[None,16]]))
        self.assertEqual(item['config']['ladder'],dict(round_hours=2))
        self.assertEqual(item['config']['scoring'],dict(win=1,draw=.5,loss=0))
        edited=self.post(self.client,'/admin/competitions/'+item['id']+'/edit',dict(title='Elo renamed',max_teams=1000,match_ms=60000,expected_revision=item['revision']))
        self.assertEqual(edited['config']['format'],'elo')
        self.assertEqual(edited['config']['official'],item['config']['official'])
        self.post(self.client,'/admin/competitions',dict(body,submission_deadline=None),expected=422)
        self.post(self.client,'/admin/competitions',dict(body,official=dict(group_size=8,finalists=16)),expected=422)

    def test_deadline_auto_locks_only_resolved_entries_then_starts_official(self):
        self.configure(deadline=1000,group=64)
        with self.store.transaction():
            for team,entry in self.entries.items():
                saved=dict(entry);saved.pop('bot_hash')
                self.store.db.execute('UPDATE competition_entries SET snapshot=? WHERE competition_id=? AND team_id=?',(json.dumps(saved),self.ident,team))
        self.comp.elo.advance(self.comp.get(self.ident),2000)
        self.assertEqual(self.comp.get(self.ident)['status'],'running')
        self.assertEqual(self.comp.rows('SELECT * FROM competition_fixtures'),[])
        self.comp.elo.advance(self.comp.get(self.ident),2001)
        self.assertEqual(self.comp.one('SELECT phase FROM elo_rounds')['phase'],'round_robin')
        config=json.loads(self.comp.one('SELECT config FROM elo_rounds')['config'])
        self.assertEqual(config['profile']['match_ms'],10000)

    def test_open_just_before_fixed_hour_does_not_miss_first_round_if_tick_is_late(self):
        start=boundary(1_792_728_000_000)
        item=self.post(self.client,'/admin/competitions',dict(title='Boundary opening',format='elo',submission_deadline=9_000_000_000_000),expected=201)
        with patch('arena_api.competitions.now_ms',return_value=start+7_200_000-1):
            self.post(self.client,'/admin/competitions/'+item['id']+'/open')
        self.assertEqual(self.comp.one('SELECT last_due_ms FROM elo_calendar WHERE competition_id=?',(item['id'],))['last_due_ms'],start)
        with patch.object(self.comp.elo.book,'latest_passes',return_value=self.entries) as latest:
            self.comp.elo.advance(self.comp.get(item['id']),start+7_200_100)
            latest.assert_called_once_with(item['id'],start+7_200_000)
        self.assertEqual(self.comp.one('SELECT number FROM elo_rounds WHERE competition_id=?',(item['id'],))['number'],1)

    def test_btc_exclusion_is_audited_keeps_games_and_recomputes_finalist_seeds(self):
        self.configure(deadline=1000,group=64)
        self.book.seed_official(self.ident,self.entries,0)
        rid=self.book.start(self.ident,'round_robin',1,2000,self.entries)
        with self.store.transaction():
            for game in self.book.games(rid):
                winner='red' if game['red_team_id']<game['blue_team_id'] else 'blue'
                self.store.db.execute("UPDATE elo_games SET status='completed',result=? WHERE id=?",(json.dumps(dict(winner=winner,reason='base_capture')),game['id']))
            self.store.db.execute("UPDATE competitions SET status='running' WHERE id=?",(self.ident,))
        self.book.finish(rid,3000)
        first=min(self.entries);path=f'/admin/competitions/{self.ident}/teams/{first}/exclude'
        self.post(self.client,path,dict(excluded=True,reason='Synthetic fixture violation'))
        self.comp.elo.advance(self.comp.get(self.ident),4000)
        final=self.comp.get(self.ident)
        self.assertEqual([r['team_id'] for r in final['config']['finalists']],sorted(self.entries)[1:3])
        self.assertEqual([r['rank'] for r in final['config']['finalists']],[1,2])
        self.assertEqual(len(self.book.games(rid)),12)
        guest=self.other_client()
        public=guest.get('/api/v1/competitions/'+self.ident+'/standings').json()
        removed=next(r for r in public['items'] if r['team_id']==first)
        self.assertTrue(removed['excluded']);self.assertIsNone(removed['rank'])
        self.assertNotIn('violation',json.dumps(public))
        import test_recompute_elo as tool
        exported=self.book.export(self.ident,'official')
        replayed=tool.module.recompute(exported)
        self.assertEqual(replayed['finalists'],final['config']['finalists'])
        self.assertEqual([(r['team_id'],r['rank']) for r in replayed['standings']],[(r['team_id'],r['rank']) for r in public['items']])
        self.assertEqual(guest.get('/api/v1/admin/competitions/'+self.ident+'/exclusions').status_code,401)
        self.post(self.client,path,dict(excluded=False,reason='BTC restores fixture'))
        self.assertEqual(self.comp.get(self.ident)['config']['finalists'][0]['team_id'],first)
        history=self.client.get('/api/v1/admin/competitions/'+self.ident+'/exclusions').json()['items']
        self.assertEqual([r['excluded'] for r in history],[1,0])
        self.assertTrue(all(r['actor'] for r in history))
