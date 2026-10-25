"""Offline guard for the authorized production demo's team scope."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import MagicMock,patch
import live_demo_elo
from live_demo_elo import restore_other
from live_demo_elo import validate_scope


class DemoScope(TestCase):
    def test_only_the_four_demo_repositories_and_the_authorized_trial_team(self):
        rows=[dict(name='DEMO ELO '+str(i),owner='uet-bot-arena',repo='demo-elo-'+str(i)) for i in range(1,5)]
        rows.append(dict(name='THU-M2 A',owner='uet-bot-arena',repo='bot-thu'))
        validate_scope(rows)
        for extra in (dict(name='THU-M2 B',owner='uet-bot-arena',repo='bot-thu'),dict(name='DEMO ELO 1',owner='other',repo='demo-elo-1')):
            with self.assertRaises(AssertionError):validate_scope(rows+[extra])

    def test_restore_requires_completion_and_two_finalists_before_mutation(self):
        for view in ({'status':'open','config':{'finalists':['a','b']}},{'status':'completed','config':{'finalists':['a']}}):
            with patch.object(live_demo_elo,'api',return_value=view) as api:
                with self.assertRaises(AssertionError):restore_other(None,{'cid':'demo'})
                self.assertEqual(api.call_count,1)
                self.assertEqual(api.call_args.args[1],'GET')

    def test_restore_preserves_each_account_lock_and_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            private=Path(tmp);(private/'smoke-teams.json').write_text(json.dumps({'thu-m2-a':'fixture-password'}))
            state={'cid':'demo','other_before':{'id':'trial','repo':'uet-bot-arena/bot-thu','accounts':[{'id':'one','locked':False},{'id':'two','locked':True}]}}
            restored=[False];locks={}
            def api(client,method,path,**kwargs):
                if method=='POST' and path=='/admin/teams/trial/restore':
                    restored[0]=True;return {}
                if method=='POST' and path.startswith('/admin/accounts/'):
                    locks[path]=kwargs['json']['locked'];return {}
                if path=='/competitions/demo':return {'status':'completed','config':{'finalists':['a','b']},'entries':[{}, {}, {}, {}]}
                if path=='/admin/teams/trial':return {'archived_ms':None if restored[0] else 1,'revision':4}
                if path=='/admin/accounts?limit=100':return {'items':[{'id':'one','locked':locks['/admin/accounts/one/lock']},{'id':'two','locked':locks['/admin/accounts/two/lock']}]}
                if path=='/auth/me':return {'user':{'team_id':'trial'}}
                if path=='/teams/me/github':return {'repo':{'owner':'uet-bot-arena','name':'bot-thu'}}
                raise AssertionError((method,path))
            with patch.object(live_demo_elo,'api',side_effect=api),patch.object(live_demo_elo,'login',return_value=MagicMock()),patch.object(live_demo_elo,'PRIVATE',private),patch.object(live_demo_elo,'STATE',private/'state.json'):
                restore_other(None,state)
            self.assertTrue(state['other_restored'])
            self.assertEqual(locks,{'/admin/accounts/one/lock':False,'/admin/accounts/two/lock':True})

    def test_polling_reuses_sessions_instead_of_repeating_login(self):
        state={'cid':'demo','deadline_ms':2000,'pushes':{str(i)+'-before':'sha'+str(i) for i in range(1,5)}}
        def api(client,method,path):
            if path=='/time':return {'server_sent_unix_ms':1000}
            if path=='/competitions/demo':return {'status':'open'}
            if path=='/teams/me/github':return {'commits':[],'deadlines':[]}
            if path=='/team/elo':return {'items':[]}
            raise AssertionError(path)
        with patch.object(live_demo_elo,'api',side_effect=api),patch.object(live_demo_elo,'save'),patch.object(live_demo_elo,'login') as login:
            clients={i:MagicMock() for i in range(1,5)}
            for _ in range(2):self.assertFalse(live_demo_elo.tick(None,state,clients))
            login.assert_not_called()
