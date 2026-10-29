"""Real long-poll bounds: 32 idle graders, notification and revocation."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from unittest.mock import patch
from test_web_api import APIHarness
from arena import engine


class WaitingGraders(APIHarness):
    def machine(self, name):
        service=self.app.state.service
        issued=service.create_grader(name,'system')
        who=service.authenticate(issued['token'])
        service.hello(who,dict(platform='windows',backend='windows',runtime_id='sha256:'+'1'*64,slots=1,version='test',engine_sha256=engine.ENGINE_SHA256))
        return issued, {'Authorization':'Bearer '+issued['token']}

    def test_32_idle_graders_20_seconds_bound_attempts_and_liveness_writes(self):
        service=self.app.state.service
        headers=[self.machine(str(i))[1] for i in range(32)]
        updates=[]
        service.store.db.set_trace_callback(lambda sql:updates.append(sql) if sql.startswith('UPDATE graders SET last_seen_ms=') else None)
        try:
            with patch.object(service,'before_next') as schedule, patch.object(service,'try_lease',wraps=service.try_lease) as attempt, ThreadPoolExecutor(max_workers=32) as pool:
                results=list(pool.map(lambda h:self.client.post('/api/v1/grader/lease',json=dict(wait_seconds=20),headers=h),headers))
                self.assertTrue(all(r.status_code==200 and r.json()=={'lease':None} for r in results))
                self.assertEqual(schedule.call_count,32)
                self.assertLessEqual(attempt.call_count,640)
            self.assertLessEqual(len(updates),160)
        finally:service.store.db.set_trace_callback(None)

    def test_put_wakes_waiter_and_revocation_remains_bounded(self):
        service=self.app.state.service
        issued,headers=self.machine('waiting')
        ready=threading.Event(); has_job=threading.Event()
        def claim(*args):
            ready.set()
            return {'id':'notified'} if has_job.is_set() else None
        with patch.object(service,'try_lease',side_effect=claim),ThreadPoolExecutor(max_workers=1) as pool:
            response=pool.submit(self.client.post,'/api/v1/grader/lease',json=dict(wait_seconds=20),headers=headers)
            self.assertTrue(ready.wait(2))
            time.sleep(.05)
            started=time.monotonic();has_job.set()
            service.pending.put_official('notification-only')
            self.assertEqual(response.result(timeout=1).json()['lease']['id'],'notified')
            self.assertLessEqual(time.monotonic()-started,1)
            service.pending.remove('notification-only')
        with patch.object(service,'try_lease',return_value=None),ThreadPoolExecutor(max_workers=1) as pool:
            response=pool.submit(self.client.post,'/api/v1/grader/lease',json=dict(wait_seconds=20),headers=headers)
            time.sleep(.1)
            started=time.monotonic();service.revoke_grader(issued['id'],'system')
            self.assertEqual(response.result(timeout=1.5).status_code,401)
            self.assertLessEqual(time.monotonic()-started,1.5)
