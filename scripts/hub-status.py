#!/usr/bin/env python3
"""One-second CPU, current RAM/disk, last-minute request and error counts."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

unit = subprocess.check_output(['systemctl', 'show', 'arena.slice', '--property=ControlGroup', '--value', '--no-pager']).decode().strip()
assert unit.startswith('/') and '..' not in unit
group = Path('/sys/fs/cgroup' + unit)


def cpu():
    return int(dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines())['usage_usec'])


before, started = cpu(), time.monotonic()
time.sleep(1)
quota, period = (group / 'cpu.max').read_text().split()
cores = int(quota) / int(period) if quota != 'max' else 1
used = (cpu() - before) / (time.monotonic() - started) / 1_000_000 / cores * 100
raw = subprocess.check_output(['journalctl', '-t', 'uet_arena_nginx', '--since=-1min', '-o', 'json', '--no-pager']).decode()
codes = []
for line in raw.splitlines():
    match = re.search(r'HTTP/1\.[01]" (\d{3})', json.loads(line).get('MESSAGE', ''))
    if match:
        codes.append(int(match[1]))
volume = shutil.disk_usage('/var/lib/uet-arena')
print(json.dumps(dict(cpu_percent_of_budget=round(used, 2), cpu_budget_cores=cores,
    ram_mib=round(int((group / 'memory.current').read_text()) / 1024**2, 1),
    disk_used_gib=round(volume.used / 1024**3, 2), disk_free_gib=round(volume.free / 1024**3, 2),
    requests_last_minute=len(codes), http_errors_last_minute=sum(c >= 400 for c in codes)), indent=2))
