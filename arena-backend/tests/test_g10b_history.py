import json
from arena_api.migrations import migrate
from test_elo_rounds import EloFixture


class NormalizedHistory(EloFixture):
    def setUp(self):
        super().setUp()
        config=self.comp.get(self.ident)['config']
        config.update(format='elo',ladder=dict(round_hours=2))
        with self.store.transaction():
            self.store.db.execute("UPDATE competitions SET config=?,status='open',submission_deadline=9000000000000 WHERE id=?",(json.dumps(config),self.ident))

    def test_history_backfill_and_new_finish_match_without_season_json_reads(self):
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid);after=self.book.finish(rid,2000)
        expected=self.comp.rows('SELECT * FROM elo_rating_history ORDER BY team_id')
        self.assertEqual(len(expected),4)
        with self.store.transaction():
            self.store.db.execute('DROP TABLE elo_rating_history')
            self.store.db.execute('DROP INDEX github_checks_unsent')
            self.store.db.execute('DROP INDEX github_checks_due')
            self.store.db.execute('DROP INDEX github_observations_latest')
            self.store.db.execute('DROP INDEX competition_entry_public')
            self.store.db.execute('DROP TRIGGER github_terminal_update')
            self.store.db.execute('DELETE FROM schema_migrations WHERE version=14')
        migrate(self.store);migrate(self.store)
        self.assertEqual(self.comp.rows('SELECT * FROM elo_rating_history ORDER BY team_id'),expected)
        self.assertEqual(self.book.ladder_at(self.ident,1999),{})
        self.assertEqual(self.book.ladder_at(self.ident,2000),after['ratings'])
        statements=[];self.store.db.set_trace_callback(statements.append)
        try:
            value=self.comp.elo.team_view(next(iter(self.entries)))
            self.book.ladder_at(self.ident,2000)
        finally:self.store.db.set_trace_callback(None)
        for statement in statements:
            self.assertNotIn('json_extract(snapshot',statement)
            self.assertNotIn('json_extract(after_state',statement)
            self.assertNotIn('SELECT after_state',statement)
        self.assertEqual(value['items'][0]['current']['elo_before'],1500)
        self.assertEqual(value['items'][0]['history'][0]['round'],1)

    def test_standings_cache_refreshes_after_finish_and_exclusion(self):
        first=self.comp.elo.standings(self.ident)
        rid=self.book.start(self.ident,'ladder',1,1000,self.entries)
        self.complete_games(rid);self.book.finish(rid,2000)
        second=self.comp.elo.standings(self.ident)
        self.assertNotEqual(first,second)
        team=next(iter(self.entries))
        with self.store.transaction():
            self.store.db.execute("INSERT INTO elo_exclusions(competition_id,team_id,excluded,reason,actor,created_ms) VALUES(?,?,1,'test','system',2001)",(self.ident,team))
        third=self.comp.elo.standings(self.ident)
        self.assertTrue(next(r for r in third['items'] if r['team_id']==team)['excluded'])
