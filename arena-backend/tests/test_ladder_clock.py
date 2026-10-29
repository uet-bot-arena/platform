import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from arena.ladder_clock import boundary,round_boundary,schedule_preview,next_minute
from arena_api.elo_scheduler import EloScheduler
import test_elo_scheduler as old
from test_elo_rounds import EloFixture

class LadderClock(unittest.TestCase):
    def test_shared_preview_cases_and_timezone_independence(self):
        rows=json.loads((Path(__file__).parent/'fixtures/ladder-clock.json').read_text(encoding='utf-8'))
        previous=os.environ.get('TZ')
        try:
            for zone in ('UTC','America/New_York','Asia/Tokyo'):
                os.environ['TZ']=zone
                if hasattr(time,'tzset'):time.tzset()
                for row in rows:
                    self.assertEqual(schedule_preview(row['ladder'],row['start_ms'],row['deadline_ms']),row['expected'],(zone,row['name']))
        finally:
            if previous is None:os.environ.pop('TZ',None)
            else:os.environ['TZ']=previous
            if hasattr(time,'tzset'):time.tzset()

    def test_every_legacy_interval_keeps_existing_boundaries(self):
        for hours in range(1,25):
            for minute in range(0,3*24*60,13):
                now=1_793_000_000_000+minute*60_000
                for after in (False,True):self.assertEqual(round_boundary(now,{'round_hours':hours},next=after),boundary(now,hours,next=after))

class MinuteScheduling(EloFixture):
    configure=old.EloScheduling.configure

    def create(self,ladder):
        return self.post(self.client,'/admin/competitions',dict(title='Minute calendar',format='elo',max_teams=16,match_ms=10000,submission_deadline=9_000_000_000_000,ladder=ladder),expected=201)

    def test_open_persists_next_minute_default_and_restart_does_not_shift(self):
        now=1_793_000_012_345;item=self.create({'round_minutes':105});ident=item['id']
        self.assertNotIn('first_round_ms',item['config']['ladder'])
        with patch('arena_api.competitions.now_ms',return_value=now):self.post(self.client,f'/admin/competitions/{ident}/open')
        saved=self.comp.get(ident);self.assertEqual(saved['config']['ladder']['first_round_ms'],next_minute(now))
        with patch('arena_api.elo_scheduler.time.time_ns',return_value=now*1_000_000):
            expected=self.comp.elo.public(ident)['next_round_ms'];self.assertEqual(expected,next_minute(now))
            self.assertEqual(EloScheduler(self.comp).public(ident)['next_round_ms'],expected)
        self.assertEqual(self.comp.get(ident)['config'],saved['config'])

    def test_legacy_open_keeps_config_bytes_and_old_calendar(self):
        item=self.create({'round_hours':5});ident=item['id']
        before=self.comp.one('SELECT config FROM competitions WHERE id=?',(ident,))['config']
        now=1_793_000_012_345
        with patch('arena_api.competitions.now_ms',return_value=now):self.post(self.client,f'/admin/competitions/{ident}/open')
        self.assertEqual(self.comp.one('SELECT config FROM competitions WHERE id=?',(ident,))['config'],before)
        with patch('arena_api.elo_scheduler.time.time_ns',return_value=now*1_000_000):self.assertEqual(self.comp.elo.public(ident)['next_round_ms'],boundary(now,5,next=True))

    def test_five_minute_busy_boundary_skipped_without_catchup(self):
        first=1_793_000_040_000;item=self.configure();cfg=dict(item['config'],ladder={'round_minutes':5,'first_round_ms':first})
        with self.store.transaction():
            self.store.db.execute('UPDATE competitions SET config=? WHERE id=?',(json.dumps(cfg),self.ident))
            self.store.db.execute('INSERT INTO elo_calendar VALUES(?,?)',(self.ident,first-300_000))
        scheduler=self.comp.elo
        with patch.object(scheduler.book,'latest_passes',return_value=self.entries):
            scheduler.advance(self.comp.get(self.ident),first-1);self.assertEqual(self.comp.rows('SELECT id FROM elo_rounds'),[])
            scheduler.advance(self.comp.get(self.ident),first)
            rid=self.comp.one('SELECT id FROM elo_rounds')['id']
            scheduler.advance(self.comp.get(self.ident),first+300_000)
            self.complete_games(rid);scheduler.book.finish(rid,first+300_001)
            scheduler.advance(self.comp.get(self.ident),first+300_002);self.assertEqual(len(self.comp.rows('SELECT id FROM elo_rounds')),1)
            scheduler=EloScheduler(self.comp)
            with patch.object(scheduler.book,'latest_passes',return_value=self.entries):scheduler.advance(self.comp.get(self.ident),first+600_000)
            self.assertEqual(len(self.comp.rows('SELECT id FROM elo_rounds')),2)

    def test_json_interval_limits_and_new_open_edit_preserves_active_round(self):
        for ladder in ({'round_minutes':4},{'round_minutes':10081},{'round_minutes':5.0},{'round_minutes':True},{'round_hours':2,'round_minutes':120}):
            result=self.client.post('/api/v1/admin/competitions',json=dict(title='Bad interval',format='elo',submission_deadline=9_000_000_000_000,ladder=ladder),headers={'Idempotency-Key':'bad-minute-interval'})
            self.assertEqual(result.status_code,422,(ladder,result.text))
        item=self.configure();rid=self.book.start(self.ident,'ladder',1,1000,self.entries);frozen=self.book.get(rid)['config']
        now=1_793_000_012_345;first=next_minute(now)+60_000
        body=dict(title=item['title'],max_teams=item['config']['max_teams'],match_ms=item['config']['profile']['match_ms'],expected_revision=item['revision'],ladder={'round_minutes':135,'first_round_ms':first})
        with patch('arena_api.competitions.now_ms',return_value=now):result=self.post(self.client,f'/admin/competitions/{self.ident}/edit',body)
        self.assertEqual(result['config']['ladder'],body['ladder']);self.assertEqual(self.book.get(rid)['config'],frozen)
        self.assertEqual(self.comp.one("SELECT COUNT(*) AS n FROM audit WHERE action='elo.schedule_changed' AND target=?",(self.ident,))['n'],1)
        later=dict(body,expected_revision=result['revision'],ladder={'round_minutes':5})
        with patch('arena_api.competitions.now_ms',return_value=9_000_000_000_001):
            response=self.client.post(f'/api/v1/admin/competitions/{self.ident}/edit',json=later,headers={'Idempotency-Key':'schedule-after-deadline'})
        self.assertEqual(response.status_code,409,response.text)
