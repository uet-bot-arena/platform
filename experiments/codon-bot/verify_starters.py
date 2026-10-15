"""Check valid JSON-lines protocol across shipped C++ and Codon starters."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from experiment import ROOT, BUILD, RESULTS, sha

site = ROOT.parents[1] / 'uet-bot-arena'
cases = [
    {'type': 'observation', 'turn_id': 't1', 'state_version': 0,
     'payload': {'state': {}, 'legal_moves': [{'kind': 'move', 'from': [6, 1], 'to': [5, 1]}]}},
    {'type': 'observation', 'turn_id': 'turn-"2"', 'state_version': 3,
     'payload': {'state': {'board': [0, 1, -1]}, 'legal_moves': []}},
    {'type': 'observation', 'turn_id': 'lượt-3', 'state_version': 42,
     'payload': {'state': {}, 'legal_moves': [{'kind': 'move', 'to': [1, 2]}, {'kind': 'move', 'to': [2, 2]}]}},
]
expected = [{'protocol_version': 1, 'type': 'action', 'turn_id': c['turn_id'],
             'state_version': c['state_version'], 'payload': c['payload']['legal_moves'][0] if c['payload']['legal_moves'] else None} for c in cases]
lines = [json.dumps(c, ensure_ascii=False) for c in cases]
lines.insert(1, '{not valid json')
lines.insert(2, '{"type":"ignore"}')
commands = {
    'cpp': [str(BUILD / 'starter-cpp')],
    'codon': [str(BUILD / 'starter-codon')],
    'codon-source-cpython': [sys.executable, str(site / 'dist/assets/starter-codon/bot.py')],
    'legacy-python': [sys.executable, str(site / 'dist/assets/starter/bot.py')]
}
results = []
for runtime, argv in commands.items():
    child = subprocess.run(argv, input='\n'.join(lines) + '\n', text=True, capture_output=True, timeout=15)
    output = [json.loads(s) for s in child.stdout.splitlines()]
    assert child.returncode == 0, (runtime, child.stderr)
    assert output == expected, (runtime, output)
    results.append({'runtime': runtime, 'passed': True, 'actions': len(output), 'exit_code': child.returncode})
    print(runtime, 'PASS', len(output), 'valid actions; malformed input recovered; other message ignored')
report = {'measured_at': datetime.now(timezone.utc).isoformat(), 'scope': 'Three valid protocol cases, malformed input recovery and non-observation ignore; not game legality.',
          'source_hashes': {'cpp': sha(site / 'dist/assets/starter-cpp/bot.cpp'), 'codon': sha(site / 'dist/assets/starter-codon/bot.py')},
          'json_header_sha256': sha(site / 'dist/assets/starter-cpp/json.hpp'), 'results': results}
(RESULTS / 'starter-validation.json').write_text(json.dumps(report, indent=2))
