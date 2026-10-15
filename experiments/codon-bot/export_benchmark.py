"""Export a small, provenance-labelled report for the static UI."""
import json
from pathlib import Path
from experiment import ROOT, RESULTS, sha

data = json.loads((RESULTS / 'results.json').read_text())
assert sha(ROOT / 'bot.py') == data['environment']['bot_python_sha256'], 'Python source differs from measured version'
assert sha(ROOT / 'bot.cpp') == data['environment']['bot_cpp_sha256'], 'C++ source differs from measured version'
report = {
    'schema_version': 1, 'report_id': 'LAB-20261010-01',
    'origin': 'internal_lab', 'submission_id': None,
    'measured_at': data['environment']['date_utc'],
    'environment': {k: data['environment'][k] for k in ['cpu', 'platform', 'python', 'codon', 'g++', 'cpu_affinity']},
    'source_hashes': {k: data['environment'][k] for k in ['bot_python_sha256', 'bot_cpp_sha256', 'compiler_sha256']},
    'suite_sha256': sha(RESULTS / 'fixtures.json'),
    'method': data['method'], 'correctness': data['correctness'],
    'timing': data['summary'], 'startup': data['startup'],
    'memory': {k: {'peak_rss_mib': v['peak_rss_mib'], 'scope': 'whole persistent process across all algorithms'} for k, v in data['runtime_info'].items()},
    'build': {k: {'seconds': v['seconds'], 'exit_code': v['exit_code']} for k, v in data['builds'].items()},
    'limits': {'turn_wall_ms': None, 'memory_mib': None, 'note': 'Lab runner has a 10 s response watchdog, not competition limits.'},
    'failures': {'timeouts': 0, 'crashes': 0, 'response_mismatches': 0, 'ott_legality': None},
    'limitations': ['Synthetic connect-five, not OTTv2.', 'Laptop WSL measurements; not server estimates.', 'Per-turn wall time, not CPU time.', 'Warm-cache startup; no cold-start measurement.', 'No uploaded team source was executed.']
}
target = ROOT.parents[1] / 'uet-bot-arena/dist/benchmark-data.js'
target.write_text('export const labReport = ' + json.dumps(report, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')
(RESULTS / 'benchmark-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('Exported benchmark-data.js and benchmark-report.json')
