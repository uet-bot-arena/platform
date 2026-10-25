"""Offline boundaries of the interactive 3c trial; no grader or hub starts."""
import io
from types import SimpleNamespace
from unittest import TestCase
import zipfile
import httpx
from remote_grader_trial import Trial, patient_bot


class RemoteTrialSafety(TestCase):
    def test_patient_bot_keeps_the_sdk_strategy_and_delays_only_matches(self):
        with zipfile.ZipFile(io.BytesIO(patient_bot())) as bundle:
            self.assertEqual(bundle.namelist(),['bot.cpp'])
            code=bundle.read('bot.cpp').decode()
        self.assertIn('if (slow) { static bool first_slow',code)
        self.assertIn('first_slow ? 180000 : 400',code)
        self.assertIn('== "match"',code)

    def test_credential_body_never_enters_failure_message(self):
        client=httpx.Client(base_url='http://example.test',transport=httpx.MockTransport(
            lambda request:httpx.Response(403,json={'temporary_password':'private-value'})))
        with client,self.assertRaisesRegex(AssertionError,'403') as caught:
            Trial.api(SimpleNamespace(),client,'POST','/admin/teams',201,json={})
        self.assertNotIn('private-value',str(caught.exception))

    def test_completed_check_must_pass_all_72_samples_on_the_laptop(self):
        from unittest.mock import MagicMock
        for worker,passed,samples,valid in [('laptop',True,72,True),('laptop',False,72,False),('laptop',True,71,False),('pc',True,72,False)]:
            def check(name,condition,**kwargs):
                if not condition:raise AssertionError(name)
            trial=SimpleNamespace(laptop='laptop',api=MagicMock(return_value={'id':'job'}),
                wait_job=MagicMock(return_value={'id':'job','status':'completed','worker':worker,'report':{'passed':passed,'samples':samples}}),check=check)
            if valid:self.assertEqual(Trial.check_job(trial,None,'submission'),'job')
            else:
                with self.assertRaises(AssertionError):Trial.check_job(trial,None,'submission')
