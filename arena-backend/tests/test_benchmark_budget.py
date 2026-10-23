import json

from arena.config import benchmark_profile,match_profile
from arena.protocol import Fault
from test_elo_rounds import EloFixture


class BenchmarkBudget(EloFixture):
    def test_competition_check_pins_private_runtime_and_uses_independent_half_second(self):
        self.post(self.client,'/admin/competitions/'+self.ident+'/open')
        team=next(iter(self.entries));entry=self.entries[team]
        response=self.post(self.extra_clients[0],'/benchmarks',dict(submission_id=entry['submission_id'],competition_id=self.ident,match_ms=300000),expected=202)
        job=self.store.get(response['id'])
        self.assertEqual(job['profile'],benchmark_profile().public())
        self.assertEqual(job['requires'],dict(platform='windows',runtime_id=self.item['config']['runtime_id']))
        # Synthetic accepted-report fixture isolates qualification from grader timing.
        report=json.loads(json.dumps(self.store.get(entry['benchmark_id'])['report']))
        report.update(profile=job['profile'],clock_scope='per_sample_500_ms')
        self.store.update(job['id'],status='completed',report=report)
        accepted=self.comp.qualification(self.item['config'],team,entry['submission_id'],job['id'])
        self.assertEqual(accepted['artifact_sha256'],entry['artifact_sha256'])
        report['profile']=match_profile(60000).public()
        self.store.update(job['id'],report=report)
        with self.assertRaises(Fault):self.comp.qualification(self.item['config'],team,entry['submission_id'],job['id'])
