"""Remote grader: pulls jobs from the hub and runs them in a local sandbox.

    python -m arena.grader --hub http://127.0.0.1:4181 --slots 2 --backend windows
    python -m arena.grader --hub https://<hub> --slots 4 --backend bwrap

The token is never taken from the command line or a URL and never logged. The first run asks
for it with a hidden prompt and stores it in a file only this user can read:
%LOCALAPPDATA%\\uet-arena\\grader-token (Windows) or ~/.config/uet-arena/grader-token, mode
0600 (Linux). systemd services pass it with LoadCredential= (read from $CREDENTIALS_DIRECTORY).

Ctrl+C once: finish the running jobs, then exit. Twice: hand the leases back and exit now.
Only the standard library is needed, so a Windows grader needs no pip packages.
"""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from arena import experimental, sandbox
from arena.archive import unpack
from arena.config import runtime_flags
from arena.protocol import Fault
from arena.runner import TERMINAL, Runner

VERSION = "0.7.0-m1"
log = logging.getLogger("arena.grader")


class Revoked(Exception):
    """The hub refused the token: stop everything."""


class LeaseLost(Exception):
    """The hub no longer accepts this lease: drop the job without a result."""


class HubError(Exception):
    """Network or server failure: retry with backoff."""


class HubClient:
    def __init__(self, hub, token, timeout=60):
        self.hub, self.token, self.timeout = hub.rstrip("/"), token, timeout

    def call(self, method, path, body=None, data=None, headers=None, timeout=None, raw=False):
        request_headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json", **(headers or {})}
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        elif data is not None:
            request_headers["Content-Type"] = "application/octet-stream"
        request = urllib.request.Request(self.hub + path, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:2000].decode(errors="replace")
            try:
                code = json.loads(detail)["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = "http_error"
            if exc.code == 401:
                raise Revoked(code) from None
            if exc.code == 409 and code == "stale_lease":
                raise LeaseLost(code) from None
            if exc.code in (400, 403, 404, 413, 415, 422):
                raise Fault("worker_error", f"Hub refused {method} {path.split('?')[0]}: {exc.code} {code}") from None
            raise HubError(f"{exc.code} {code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise HubError(str(exc)) from None
        return payload if raw else (json.loads(payload) if payload else None)


class GraderRunner(Runner):
    """One leased job: events go to the hub in batches, the result goes with the fencing token."""

    def __init__(self, grader, backend, lease, stop):
        super().__init__(stop)
        self.grader, self.backend, self.lease = grader, backend, lease
        self.client = grader.client
        self.submissions = {s["id"]: s for s in lease["submissions"]}
        self.buffer, self.buffer_lock = [], threading.Lock()
        self.report, self.status, self.error = None, None, None
        self.state_published = False
        self.built = {}  # submission id -> (folder, manifest): one build per submission per job
        self.lost = threading.Event()
        self.workers = []
        self.flusher = threading.Thread(target=self.flush_loop, daemon=True, name="events-" + lease["lease_id"][:8])
        self.flusher.start()

    def path(self, action):
        return f"/api/v1/grader/leases/{self.lease['lease_id']}/{action}"

    # ---- Runner hooks -------------------------------------------------------------------
    def record(self, job_id, kind, data):
        if kind in TERMINAL:
            return  # sent with the result
        if kind == "state":
            self.state_published = True
        with self.buffer_lock:
            self.buffer.append({"type": kind, "data": json.loads(json.dumps(data))})

    def save(self, job_id, **fields):
        if "report" in fields:
            self.report = fields["report"]
        if fields.get("status") in TERMINAL:
            self.status = fields["status"]
        if "error" in fields:
            self.error = fields["error"]

    def published_state(self, job_id):
        return self.state_published

    def runtime_report(self, job):
        identity = self.grader.identity
        return {"platform": identity["platform"], "runtime_id": identity["runtime_id"], "backend": identity["backend"]}

    def submissions_for(self, job):
        return [self.submissions[s] for s in job["submission_ids"]]

    def new_worker(self, artifact, p, job):
        if self.lost.is_set():
            raise Fault("worker_error", "Lease lost; job abandoned")
        worker = self.backend.worker(artifact, p)
        self.workers.append(worker)
        return worker

    def ensure_artifact(self, job, submission, p, require_existing=False):
        sha = submission.get("artifact_sha256")
        runtime = submission["runtime"]
        manifest = {"runtime": runtime, "source": submission["source"], "flags": runtime_flags(runtime),
                    "sdk_sha256": experimental.sdk_hash() if job.get("ruleset") else sandbox.sdk_hash(),
                    **self.runtime_report(job)}
        if sha:
            folder = self.grader.cached_artifact(sha, runtime, submission.get("artifact_url"))
            if folder:
                return folder, {**manifest, "artifact_sha256": sha, "cache_hit": True}
            if submission.get("artifact_required") or require_existing:
                raise Fault("artifact_missing", "Locked binary is not available on the hub")
        with self.grader.build_lock(submission["id"]):
            if submission["id"] not in self.built:
                self.built[submission["id"]] = self.build(job, submission, p, manifest)
            return self.built[submission["id"]]

    def build(self, job, submission, p, manifest):
        raw = self.client.call("GET", submission["source_url"], raw=True)
        work = Path(tempfile.mkdtemp(prefix="build-", dir=self.grader.work_dir))
        try:
            source = work / "source"
            if unpack(raw, source, p, submission["runtime"])["files"] != submission["source"]["files"]:
                raise Fault("provenance_mismatch", "Downloaded source differs from the submission manifest")
            result = self.backend.build(source, work / "out", p, submission["runtime"])
            self.record(job["id"], "build", {"submission_id": submission["id"], **result})
            if not result["ok"]:
                raise Fault(result["code"], result["log"] or "Compilation failed")
            sha = result["artifact_sha256"]
            self.client.call("PUT", f"/api/v1/grader/artifacts/{self.grader.identity['platform']}/{sha}",
                             data=(work / "out" / "bot").read_bytes(),
                             headers={"X-Lease-Id": self.lease["lease_id"], "X-Fencing": str(self.lease["fencing"]),
                                      "X-Submission-Id": submission["id"]})
            folder = self.grader.store_artifact(sha, work / "out")
            return folder, {**manifest, **{k: v for k, v in result.items() if k != "log"}, "artifact_sha256": sha}
        finally:
            sandbox.remove_tree(work)

    # ---- events and lifetime ------------------------------------------------------------
    def flush(self):
        with self.buffer_lock:
            batch, self.buffer = self.buffer[:600], self.buffer[600:]
        if not batch:
            return
        try:
            self.client.call("POST", self.path("events"), {"fencing": self.lease["fencing"], "events": batch})
        except HubError:
            with self.buffer_lock:
                self.buffer[:0] = batch  # keep order; retried on the next tick
            raise

    def flush_loop(self):
        while not self.lost.is_set() and not getattr(self, "finished", False):
            try:
                self.flush()
            except HubError:
                pass
            except (LeaseLost, Revoked):
                self.abandon()
                return
            time.sleep(0.15)

    def abandon(self):
        """Lease lost: kill the bots now; no result will be sent."""
        self.lost.set()
        self.stop.set()
        for worker in list(self.workers):
            try:
                worker.transport.kill()
            except Exception:
                pass

    def finish(self):
        self.finished = True
        self.flusher.join(2)
        for attempt in range(20):
            try:
                self.flush()
                with self.buffer_lock:
                    if not self.buffer:
                        return True
            except HubError:
                time.sleep(min(5, 0.5 * (attempt + 1)))
            except (LeaseLost, Revoked):
                return False
        return False


def token_file(profile="default"):
    name = "grader-token" if profile == "default" else f"grader-token-{profile}"
    credentials = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials and (Path(credentials) / "grader-token").is_file():
        return Path(credentials) / "grader-token"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "uet-arena" / name
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "uet-arena" / name


def save_token(path, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path.write_text(token, encoding="utf-8")
        import subprocess
        user = os.environ.get("USERNAME", "")
        if user:  # only this user (no inherited ACEs)
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True,
                           creationflags=0x08000000)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(token)
        os.chmod(path, 0o600)


def load_token(path, interactive):
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token, False
    if not interactive:
        raise SystemExit(f"Chưa có mã máy chấm ở {path}. Chạy một lần trong cửa sổ dòng lệnh để nhập mã.")
    token = getpass.getpass("Mã máy chấm (admin tạo ở trang Máy chấm; không hiện khi gõ): ").strip()
    if not token:
        raise SystemExit("Chưa nhập mã")
    return token, True


class Grader:
    def __init__(self, hub, token, backend=None, slots=1, cpus=None, state_dir=None, backend_options=None, backend_factory=None):
        backend = backend or ("windows" if os.name == "nt" else "bwrap")
        self.client = HubClient(hub, token)
        self.backend_name, self.slots = backend, slots
        cpu_count = os.cpu_count() or 1
        self.cpus = list(cpus) if cpus else [max(0, cpu_count - 1 - i) for i in range(slots)]
        if len(self.cpus) < slots:
            raise ValueError("Cần một CPU cho mỗi slot")
        self.state = Path(state_dir or (sandbox_base() / "grader"))
        self.artifacts_dir = self.state / "artifacts"
        self.work_dir = self.state / "work"
        for folder in (self.artifacts_dir, self.work_dir):
            folder.mkdir(parents=True, exist_ok=True)
        options = backend_options or {}
        self.backends = [(backend_factory or self.make_backend)(cpu, options) for cpu in self.cpus[:slots]]
        self.identity = self.backends[0].identity()
        self.stopping = threading.Event()   # no new leases
        self.aborting = threading.Event()   # drop running jobs now
        self.revoked = threading.Event()
        self.locks, self.locks_guard = {}, threading.Lock()
        self.active = {}
        self.active_lock = threading.Lock()
        self.threads = []
        self.settings = None
        self.done = 0

    def make_backend(self, cpu, options):
        return sandbox.get_backend(self.backend_name, cpu=cpu, **options)

    def build_lock(self, key):
        with self.locks_guard:
            return self.locks.setdefault(key, threading.Lock())

    def cached_artifact(self, sha, runtime, url):
        import hashlib
        folder = self.artifacts_dir / sha
        bot = folder / "bot"
        with self.build_lock("artifact:" + sha):
            if bot.is_file() and hashlib.sha256(bot.read_bytes()).hexdigest() == sha:
                return folder
            if not url:
                return None
            try:
                data = self.client.call("GET", url, raw=True)
            except Fault:
                return None
            if hashlib.sha256(data).hexdigest() != sha:
                raise Fault("artifact_corrupt", "Downloaded binary hash mismatch")
            staging = Path(tempfile.mkdtemp(prefix="artifact-", dir=self.work_dir))
            (staging / "bot").write_bytes(data)
            (staging / "runtime.json").write_text(json.dumps({"runtime": runtime}))
            return self.store_artifact(sha, staging)

    def store_artifact(self, sha, folder):
        target = self.artifacts_dir / sha
        if not (target / "bot").is_file():
            staging = self.artifacts_dir / (sha + "." + uuid.uuid4().hex)
            shutil.copytree(folder, staging)
            try:
                staging.rename(target)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
        return target

    def hello(self):
        self.settings = self.client.call("POST", "/api/v1/grader/hello", {
            "platform": self.identity["platform"], "runtime_id": self.identity["runtime_id"], "slots": self.slots,
            "backend": self.identity["backend"], "version": VERSION})
        return self.settings

    def start(self):
        if self.stopping.is_set():
            return
        delay = 1
        while not self.stopping.is_set():
            try:
                self.hello()
                break
            except Revoked:
                self.revoke()
                return
            except HubError as exc:
                log.warning("Chưa kết nối được hub (%s); thử lại sau %s s", exc, delay)
                if self.stopping.wait(delay):
                    return
                delay = min(30, delay * 2)
        log.info("Đã kết nối hub với tên %s: %s, %d slot, runtime %s", self.settings["name"], self.identity["platform"],
                 self.slots, self.identity["runtime_id"][:19])
        for index, backend in enumerate(self.backends):
            thread = threading.Thread(target=self.slot_loop, args=(index, backend), daemon=True, name=f"slot-{index}")
            thread.start()
            self.threads.append(thread)

    def slot_loop(self, index, backend):
        delay = 1
        while not self.stopping.is_set() and not self.revoked.is_set():
            try:
                lease = self.client.call("POST", "/api/v1/grader/lease", {"wait_seconds": self.settings["long_poll_seconds"]},
                                         timeout=self.settings["long_poll_seconds"] + 30)["lease"]
                delay = 1
            except Revoked:
                self.revoke()
                return
            except (HubError, Fault) as exc:
                log.warning("Slot %d: lỗi khi xin việc (%s); thử lại sau %s s", index, exc, delay)
                if self.stopping.wait(delay):
                    return
                delay = min(30, delay * 2)
                continue
            if lease:
                self.run_lease(index, backend, lease)

    def run_lease(self, index, backend, lease):
        job = lease["job"]
        with self.active_lock:
            stopping = self.stopping.is_set() or self.revoked.is_set()
            if not stopping:
                runner = GraderRunner(self, backend, lease, threading.Event())
                self.active[lease["lease_id"]] = runner
        if stopping:
            self.release_lease(lease)
            return
        beat = threading.Thread(target=self.heartbeat_loop, args=(runner,), daemon=True)
        beat.start()
        log.info("Slot %d: %s %s (lease %s, fencing %d)", index, job["kind"], job["id"][:8], lease["lease_id"][:8], lease["fencing"])
        try:
            status = runner.run(job, attempt_field="grader_attempt")
            if runner.lost.is_set():
                log.warning("Slot %d: mất lease %s, bỏ kết quả", index, lease["lease_id"][:8])
                return
            if not runner.finish():
                log.warning("Slot %d: không gửi được event của lease %s", index, lease["lease_id"][:8])
                return
            self.post_result(runner, status)
            self.done += 1
        finally:
            runner.lost.set()  # stops heartbeat and flusher
            with self.active_lock:
                self.active.pop(lease["lease_id"], None)

    def post_result(self, runner, status):
        body = {"fencing": runner.lease["fencing"], "status": status, "report": runner.report,
                "error": runner.error if status == "failed" else None}
        for attempt in range(30):
            try:
                answer = runner.client.call("POST", runner.path("result"), body)
                log.info("Kết quả %s: %s", runner.lease["job"]["id"][:8], answer.get("status"))
                return answer
            except HubError:
                time.sleep(min(10, attempt + 1))
            except (LeaseLost, Revoked):
                return None
        return None

    def heartbeat_loop(self, runner):
        every = self.settings["heartbeat_seconds"]
        while not runner.lost.wait(every):
            try:
                runner.client.call("POST", runner.path("heartbeat"), {"fencing": runner.lease["fencing"]}, timeout=every * 2)
            except HubError:
                continue  # the lease survives a short outage (lease_seconds > 2 heartbeats)
            except LeaseLost:
                runner.abandon()
                return
            except Revoked:
                self.revoke()
                return

    def revoke(self):
        if not self.revoked.is_set():
            log.error("Hub đã thu hồi mã máy chấm này; dừng ngay.")
        self.revoked.set()
        self.stopping.set()
        with self.active_lock:
            runners = list(self.active.values())
        for runner in runners:
            runner.abandon()

    def release_all(self):
        with self.active_lock:
            self.stopping.set()
            self.aborting.set()
            runners = list(self.active.values())
        for runner in runners:
            runner.abandon()
            self.release_lease(runner.lease)

    def release_lease(self, lease):
        try:
            self.client.call("POST", f"/api/v1/grader/leases/{lease['lease_id']}/release",
                             {"fencing": lease["fencing"], "reason": "grader_stopping"}, timeout=5)
        except (HubError, LeaseLost, Revoked, Fault):
            pass  # If the hub is unreachable, the lease expires instead.

    def wait(self, stop_file=None):
        for thread in self.threads:
            while thread.is_alive():
                if stop_file and stop_file.is_file() and not self.aborting.is_set():
                    self.release_all()
                thread.join(0.5)


def sandbox_base():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "uet-arena"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "uet-arena"


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Máy chấm UET Bot Arena")
    parser.add_argument("--hub", required=True, help="Địa chỉ hub, ví dụ http://127.0.0.1:4181")
    parser.add_argument("--slots", type=int, default=1, help="Số trận chạy song song (mỗi slot một CPU)")
    parser.add_argument("--backend", choices=sandbox.BACKENDS, default="windows" if os.name == "nt" else "bwrap")
    parser.add_argument("--profile", default="default", help="Tên hồ sơ khi chạy nhiều máy chấm trên một máy (mỗi hồ sơ một mã)")
    parser.add_argument("--token-file", help="Đường dẫn file chứa mã (không phải bản thân mã)")
    parser.add_argument("--cpus", help="Danh sách CPU cho các slot, ví dụ 10,11")
    parser.add_argument("--state-dir", help="Thư mục cache artifact và thư mục tạm")
    parser.add_argument("--check", action="store_true", help="Kiểm mã, sandbox và kết nối hub rồi thoát (dùng khi cài)")
    parser.add_argument("--log-file", help="Ghi nhật ký vào file (khi chạy nền không có cửa sổ)")
    parser.add_argument("--pid-file", help="Ghi PID của tiến trình (cho script quản lý)")
    parser.add_argument("--stop-file", type=Path, help="Trả lease và dừng khi file này xuất hiện (script Windows)")
    args = parser.parse_args(argv)
    handlers = [logging.FileHandler(args.log_file, encoding="utf-8")] if args.log_file else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    if args.hub.rstrip("/") != args.hub.rstrip("/").split("?")[0] or "@" in args.hub:
        parser.error("--hub chỉ là địa chỉ hub, không kèm mã hay tham số")
    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()))
    path = Path(args.token_file) if args.token_file else token_file(args.profile)
    token, fresh = load_token(path, sys.stdin is not None and sys.stdin.isatty())
    cpus = [int(c) for c in args.cpus.split(",")] if args.cpus else None
    state = Path(args.state_dir) if args.state_dir else sandbox_base() / ("grader" if args.profile == "default" else f"grader-{args.profile}")
    try:
        grader = Grader(args.hub, token, args.backend, args.slots, cpus, state)
    except Fault as exc:
        raise SystemExit(f"Không dùng được sandbox {args.backend}: {exc.detail}")
    try:
        grader.hello()
    except Revoked:
        log.error("Hub từ chối mã máy chấm (sai hoặc đã thu hồi).")
        raise SystemExit(3)
    except HubError as exc:
        if args.check:
            raise SystemExit(f"Chưa kết nối được hub: {exc}")
        log.warning("Chưa kết nối được hub (%s); sẽ thử lại", exc)
    if args.check:
        if fresh:
            save_token(path, token)
        print(f"OK: hub nhận máy chấm {grader.settings['name']} ({grader.identity['platform']}, {args.backend}, "
              f"runtime {grader.identity['runtime_id'][:19]}…)")
        return
    if fresh:
        save_token(path, token)
        log.info("Đã lưu mã máy chấm vào %s (chỉ tài khoản này đọc được)", path)
    presses = []

    def on_signal(*_):
        presses.append(time.monotonic())
        if len(presses) == 1:
            log.info("Đang dừng: chấm nốt trận đang chạy rồi thoát (nhấn Ctrl+C lần nữa để trả lại việc và thoát ngay)")
            grader.stopping.set()
        else:
            log.info("Trả lại các lease đang giữ và thoát")
            grader.release_all()
    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)
    try:
        grader.start()
        grader.wait(args.stop_file)
    finally:
        if args.pid_file:
            Path(args.pid_file).unlink(missing_ok=True)
    if grader.revoked.is_set():
        sys.exit(3)


if __name__ == "__main__":
    main()
