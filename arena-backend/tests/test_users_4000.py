"""Contract checks only: importing this test starts no load generator or service."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).parent/'load_lab'))
from users_4000 import evaluate,percentile
from users_4000_workers import paths


class LoadContract(unittest.TestCase):
    def test_thresholds_are_exactly_the_plan_and_http_p95_uses_nearest_rank(self):
        result=dict(http_p95_ms=300,http_errors=[],login_p95_ms=1000,slice_cpu_percent_of_4_vcpu=50,
            ram_peak_without_file=.6*2*1024**3,sse_states=100,sse_errors=[],completed_games_per_second=3,
            event_errors=[],github_polls_per_minute=950,github_errors=[])
        self.assertEqual(evaluate(result),[])
        for name,value in [('http_p95_ms',301),('http_errors',['401']),('login_p95_ms',1001),
            ('slice_cpu_percent_of_4_vcpu',50.1),('ram_peak_without_file',.6*2*1024**3+1),
            ('sse_states',99),('sse_errors',['timeout']),('completed_games_per_second',2.99),
            ('event_errors',['digest']),('github_polls_per_minute',949),('github_errors',['500'])]:
            self.assertTrue(evaluate(dict(result,**{name:value})),name)
        self.assertEqual(percentile([i/1000 for i in range(1,101)]),95)

    def test_generators_read_the_same_six_navigation_plans_as_the_ui_test(self):
        document=(Path(__file__).resolve().parents[2]/'docs/UI.md').read_text()
        contract=paths(document)
        self.assertEqual(set(contract),{'home','team','submissions','standings','schedule','queue'})
        self.assertEqual(contract['team'],[['/ui/personal?view=team','/ui/read?view=team'],[]])
        self.assertEqual(len(contract['standings'][1]),4)
