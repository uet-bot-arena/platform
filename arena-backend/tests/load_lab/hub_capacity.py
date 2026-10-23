"""Hub capacity under a 2 vCPU / 2 GB cgroup (VPS-like). Run from arena-backend in WSL:
    .venv/bin/python <this file>
Hub = one `arena_api serve` process inside `systemd-run --user --scope -p CPUQuota=200% -p MemoryMax=2G`.
Load = open-loop "page views" (5 GET each, like the UI on navigation) at rising rates + SSE viewers.
"""
import concurrent.futures as cf
import io
import json
import os
from pathlib import Path
import random
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

import httpx

ROOT = Path.cwd()
sys.path.insert(0,str(ROOT))
PASSWORD = "capacity-test-only-password"
TEAMS = 60
PAGE = ["/api/v1/teams/me", "/api/v1/dashboard", "/api/v1/submissions?limit=50", "/api/v1/jobs?limit=50", "/api/v1/competitions"]
LEVELS = [4, 8, 16, 24, 32, 48, 64]  # page views per second (x5 requests)
PHASE_S = 30


def serve_stack():
    """Both production processes inherit the same 2-CPU/2-GB cgroup."""
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    children=[]
    try:
        children.append(subprocess.Popen([sys.executable,'-m','arena_api','serve']))
        for _ in range(200):
            try:
                if httpx.get(os.environ['ARENA_API_ORIGIN']+'/api/v1/health/live',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            if stop.wait(.1):return
        else:raise RuntimeError('Hub stack did not start')
        children.append(subprocess.Popen([sys.executable,'-m','arena_api','publish']))
        while not stop.wait(.2):
            if any(p.poll() is not None for p in children):raise RuntimeError('Hub/publisher exited during load test')
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(20)
            except subprocess.TimeoutExpired:child.kill();child.wait()


def cg_path(pid):
    rel = open(f"/proc/{pid}/cgroup").read().strip().split("::", 1)[1]
    return Path("/sys/fs/cgroup" + rel)


def cpu_usec(cg):
    for line in (cg / "cpu.stat").read_text().splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])


def mem_mb(cg, name):
    return int((cg / name).read_text()) / 2**20


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else float("nan")


def main():
    data = Path(tempfile.mkdtemp(prefix="cap-hub-"))
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ARENA_API_DATA": str(data), "ARENA_PORT": str(port), "ARENA_CONFIG": "none",
           "ARENA_SECRETS": "none", "ARENA_API_ORIGIN": origin,"ARENA_HASH_WORKERS":"1"}
    py = str(ROOT / ".venv/bin/python")
    subprocess.run([py, "-m", "arena_api", "bootstrap-admin", "--username", "organizer", "--password-stdin"],
                   input=PASSWORD + "\n", text=True, env=env, check=True, capture_output=True)
    hub = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "-p", "CPUQuota=200%", "-p", "MemoryMax=2G",
                            "-p", "MemorySwapMax=0", py, str(Path(__file__).resolve()), '--stack'],
                           env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=open(data / "hub.log", "w"))
    try:
        for _ in range(200):
            try:
                if httpx.get(origin + "/api/v1/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        cg = cg_path(hub.pid)
        print("cgroup:", cg, "| cpu.max:", (cg / "cpu.max").read_text().strip(), "| memory.max MB:",
              int((cg / "memory.max").read_text()) // 2**20, flush=True)
        print(f"idle RAM: {mem_mb(cg, 'memory.current'):.0f} MB", flush=True)

        def post(c, path, expected, **kw):
            kw.setdefault("headers", {})["Idempotency-Key"] = uuid.uuid4().hex
            r = c.post("/api/v1" + path, **kw)
            assert r.status_code == expected, (path, r.status_code, r.text[:300])
            return r.json()

        admin = httpx.Client(base_url=origin, timeout=60)
        admin.headers["X-CSRF-Token"] = post(admin, "/auth/login", 200, json={"username": "organizer", "password": PASSWORD})["csrf_token"]
        src = (ROOT / "sdk" / "bot.cpp").read_text(encoding="utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("bot.cpp", src)
        bot_zip = buf.getvalue()

        cpu0, t0 = cpu_usec(cg), time.monotonic()

        def make_team(i):
            issued = post(admin, "/admin/teams", 201, json={"profile": {
                "team": f"Cap {i}", "email": f"cap{i}@example.test", "phone": "", "confirmed": True,
                "members": [{"name": f"SV {k}", "student_id": f"2602{i:02d}0{k}"} for k in range(4)]}, "username": f"cap-{i}"})
            c = httpx.Client(base_url=origin, timeout=60)
            c.headers["X-CSRF-Token"] = post(c, "/auth/login", 200, json={"username": f"cap-{i}", "password": issued["temporary_password"]})["csrf_token"]
            c.headers["X-CSRF-Token"] = post(c, "/auth/password", 200, json={"current_password": issued["temporary_password"], "new_password": PASSWORD})["csrf_token"]
            for v in range(2):
                sub = post(admin, "/admin/teams/"+issued["account"]["team_id"]+"/submissions", 201, content=bot_zip, headers={"Content-Type": "application/zip", "X-Submission-Version": f"v1.0.{v}"})
                post(c, "/benchmarks", 202, json={"submission_id": sub["id"]})
            return "; ".join(f"{k}={v}" for k, v in c.cookies.items())

        with cf.ThreadPoolExecutor(8) as pool:
            cookies = list(pool.map(make_team, range(TEAMS)))
        dt, used = time.monotonic() - t0, (cpu_usec(cg) - cpu0) / 1e6
        print(f"setup: {TEAMS} teams (3 Argon2 each) + {TEAMS*2} ZIP uploads + {TEAMS*2} queued checks in {dt:.1f} s, "
              f"hub CPU {used:.1f} s = {used/dt/2*100:.0f}% of 2 vCPU, RAM {mem_mb(cg, 'memory.current'):.0f} MB", flush=True)
        # Prepare an official fixture offline, preserving the one-writer contract.
        hub.terminate();hub.wait(30)
        fixture=json.loads(subprocess.check_output([py,str(Path(__file__).with_name('capacity_fixture.py')),str(data)],env=env,text=True))
        hub=subprocess.Popen(["systemd-run","--user","--scope","--quiet","-p","CPUQuota=200%","-p","MemoryMax=2G","-p","MemorySwapMax=0",py,str(Path(__file__).resolve()),'--stack'],env=env,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=open(data/'hub.log','a'))
        for _ in range(200):
            try:
                if httpx.get(origin+'/api/v1/health/live',timeout=1).status_code==200:break
            except httpx.HTTPError:time.sleep(.1)
        cg=cg_path(hub.pid)
        # One simulated grader emits real fenced HTTP events; it never submits a score.
        grader_token=post(admin,'/admin/graders',201,json={'name':'capacity-synthetic-events'})['token']
        fake=httpx.Client(base_url=origin,headers={'Authorization':'Bearer '+grader_token},timeout=30)
        post(fake,'/grader/hello',200,json=dict(platform='windows',runtime_id='sha256:'+'1'*64,backend='windows',slots=1,version='capacity-fixture'))
        lease=post(fake,'/grader/lease',200,json=dict(wait_seconds=0))['lease'];assert lease and lease['job']['official']['competition_id']==fixture['competition_id']
        fixture_id=lease['job']['official']['fixture_id']
        event_stop=threading.Event();event_errors=[]
        def emit_events():
            from arena import engine
            last_beat=0
            while not event_stop.wait(.5):
                try:
                    if time.monotonic()-last_beat>=5:
                        post(fake,'/grader/leases/'+lease['lease_id']+'/heartbeat',200,json=dict(fencing=lease['fencing']));last_beat=time.monotonic()
                    post(fake,'/grader/leases/'+lease['lease_id']+'/events',200,json=dict(fencing=lease['fencing'],events=[dict(type='state',data=dict(state=engine.initial_state(),counts=engine.counts(engine.initial_state()),clocks=dict(red=60000,blue=60000)))]))
                except Exception as exc:event_errors.append(type(exc).__name__);return
        emitter=threading.Thread(target=emit_events,daemon=True);emitter.start()

        local = threading.local()

        def page_view():
            if not hasattr(local, "c"):
                local.c = httpx.Client(base_url=origin, timeout=30)
            cookie = random.choice(cookies)
            out = []
            for path in PAGE:
                t = time.perf_counter()
                try:
                    code = local.c.get(path, headers={"Cookie": cookie}).status_code
                except httpx.HTTPError:
                    code = 0
                out.append((time.perf_counter() - t, code))
            return out

        results = {"stack":"hub+publisher in the same cgroup","levels": []}
        sse_stop = threading.Event()

        active=set();stream_errors=[];stream_chunks={};stream_lock=threading.Lock()
        def sse_viewer(number,cookie):
            with httpx.Client(base_url=origin,timeout=httpx.Timeout(10,read=90)) as c:
                try:
                    with c.stream('GET',f"/api/v1/competitions/{fixture['competition_id']}/fixtures/{fixture_id}/events",headers={'Cookie':cookie,'Accept':'text/event-stream'}) as response:
                        if response.status_code!=200:raise RuntimeError('SSE HTTP '+str(response.status_code))
                        with stream_lock:active.add(number)
                        for chunk in response.iter_raw():
                            with stream_lock:stream_chunks[number]=stream_chunks.get(number,0)+1
                            if sse_stop.is_set():break
                except Exception as exc:
                    with stream_lock:stream_errors.append(str(exc)[:120])
                finally:
                    with stream_lock:active.discard(number)

        for phase, rate in [(f"{r} lượt/s", r) for r in LEVELS] + [("24 lượt/s + 100 SSE", 24)]:
            sse_threads = []
            if "SSE" in phase:
                for k in range(100):
                    th = threading.Thread(target=sse_viewer, args=(k, cookies[k % len(cookies)]), daemon=True)
                    th.start(); sse_threads.append(th)
                until=time.monotonic()+20
                while time.monotonic()<until and len(active)<100 and not stream_errors:time.sleep(.1)
                assert len(active)==100 and not stream_errors,(len(active),stream_errors)
            lat, codes, futures = [], {}, []
            active_samples=[]
            cpu0, t0 = cpu_usec(cg), time.monotonic()
            with cf.ThreadPoolExecutor(256) as pool:
                n = int(rate * PHASE_S)
                for i in range(n):
                    delay = t0 + i / rate - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    if "SSE" in phase and i%rate==0:active_samples.append(len(active))
                    futures.append(pool.submit(page_view))
                for f in futures:
                    for d, code in f.result():
                        lat.append(d); codes[code] = codes.get(code, 0) + 1
            dt = time.monotonic() - t0
            used = (cpu_usec(cg) - cpu0) / 1e6
            row = {"phase": phase, "req_per_s": round(len(lat) / dt, 1), "p50_ms": round(pct(lat, .5) * 1000),
                   "p95_ms": round(pct(lat, .95) * 1000), "p99_ms": round(pct(lat, .99) * 1000),
                   "cpu_pct_of_2vcpu": round(used / dt / 2 * 100), "ram_mb": round(mem_mb(cg, "memory.current")),
                   "codes": codes}
            if 'SSE' in phase:
                row.update(sse_active_min=min(active_samples),sse_connections=len(stream_chunks),sse_chunks=sum(stream_chunks.values()),sse_errors=stream_errors,event_errors=event_errors)
                assert row['sse_active_min']==100 and row['sse_connections']==100 and row['sse_chunks']>=1000 and not stream_errors and not event_errors,row
            results["levels"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if "SSE" in phase:
                sse_stop.set()
                for th in sse_threads:th.join(10)
                assert not any(th.is_alive() for th in sse_threads)
            if row["p95_ms"] > 3000:
                print("p95 > 3 s: dừng tăng tải", flush=True)
                if "SSE" not in phase:
                    continue
        results["ram_peak_mb"] = round(mem_mb(cg, "memory.peak"))
        print("RAM peak MB:", results["ram_peak_mb"], flush=True)
        Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/hub-capacity.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        if "event_stop" in locals():event_stop.set();emitter.join(10);fake.close()
        hub.terminate()
        try:
            hub.wait(20)
        except subprocess.TimeoutExpired:
            hub.kill()


if __name__ == "__main__":
    serve_stack() if '--stack' in sys.argv else main()
