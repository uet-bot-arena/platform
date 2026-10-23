"""JSON-lines channel to a trusted supervisor process, shared by every sandbox backend.

A dedicated thread reads the supervisor's stdout, because select() does not work on Windows
pipes. Transport errors are never a bot fault: they raise worker_error, so no win is awarded.
"""
import json
import queue
import subprocess
import threading
import time

from arena.protocol import Fault, strict_json

MAX_CONTROL_LINE = 2 * 1024 * 1024
# Same clock choice as supervisor.monotonic_ns (QueryPerformanceCounter on Windows).
monotonic_ns = time.perf_counter_ns if subprocess._mswindows else time.monotonic_ns
_EOF, _OVERSIZE = object(), object()


class Transport:
    def __init__(self, argv, cwd=None, env=None, start_new_session=False):
        flags = 0x08000000 if subprocess._mswindows else 0  # CREATE_NO_WINDOW on Windows
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  bufsize=0, cwd=cwd, env=env, creationflags=flags,
                                  start_new_session=start_new_session)
        self.lines = queue.Queue()
        self.stderr = bytearray()
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True, name="supervisor-stdout")
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True, name="supervisor-stderr")
        self.stdout_thread.start()
        self.stderr_thread.start()

    def _read_stdout(self):
        buffer = bytearray()
        try:
            while True:
                chunk = self.p.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, rest = bytes(buffer).partition(b"\n")
                    buffer = bytearray(rest)
                    self.lines.put(line)
                if len(buffer) > MAX_CONTROL_LINE:
                    self.lines.put(_OVERSIZE)
                    return
        except (OSError, ValueError):
            pass
        self.lines.put(_EOF)

    def _read_stderr(self):
        try:
            while True:
                chunk = self.p.stderr.read(4096)
                if not chunk:
                    return
                if len(self.stderr) < 8192:
                    self.stderr.extend(chunk[:8192 - len(self.stderr)])
        except (OSError, ValueError):
            return

    def line(self, seconds):
        try:
            item = self.lines.get(timeout=max(0.0, seconds))
        except queue.Empty:
            raise Fault("worker_error", "Control transport timeout (not a bot time loss)") from None
        if item is _EOF:
            self.lines.put(_EOF)
            self.stderr_thread.join(1)
            raise Fault("worker_error", "Supervisor exited: " + bytes(self.stderr[:2048]).decode(errors="replace"))
        if item is _OVERSIZE:
            raise Fault("worker_error", "Oversized supervisor response")
        return strict_json(item)

    def send(self, payload):
        try:
            self.p.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            self.p.stdin.flush()
        except (OSError, ValueError) as exc:
            raise Fault("worker_error", "Supervisor input closed") from exc

    def alive(self):
        return self.p.poll() is None

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.p.stdin, self.p.stdout, self.p.stderr):
            try:
                stream.close()
            except OSError:
                pass


class SupervisorWorker:
    """start/turn/close over a Transport; identical protocol for bubblewrap and Windows."""

    boot_seconds = 15

    def __init__(self, transport, profile, cleanup=None):
        self.transport, self.profile, self._cleanup = transport, profile, cleanup
        try:
            boot = self.transport.line(self.boot_seconds)
            if boot.get("type") != "boot":
                raise Fault("worker_error", "Missing trusted supervisor handshake")
            if profile.match_ms is not None and "match" not in boot.get("time_controls", []):
                raise Fault("worker_error", "Worker runtime does not support cumulative clocks")
            self.clock_id = boot["clock_id"]
            # Bound the worker/host monotonic mapping by the probe's send and
            # receive instants. Used for countdown display, never adjudication.
            before = monotonic_ns()
            probe = self.rpc({"op": "probe"}, 5)
            after = monotonic_ns()
            self.clock_offset_ns = (before + after) // 2 - int(probe["mono_ns"])
            self.clock_uncertainty_ms = (after - before) / 2e6
        except BaseException:
            self.close()
            raise

    def line(self, seconds):
        return self.transport.line(seconds)

    def rpc(self, payload, timeout, on_started=None):
        self.transport.send(payload)
        deadline = time.monotonic() + timeout
        while True:
            item = self.line(max(0, deadline - time.monotonic()))
            if item.get("type") == "turn_started":
                if on_started:
                    worker_start = int(item["started_mono_ns"])
                    estimated_host_start = worker_start + self.clock_offset_ns
                    item["estimated_started_unix_ms"] = (time.time_ns() + estimated_host_start - monotonic_ns()) // 1_000_000
                    item["clock_uncertainty_ms"] = self.clock_uncertainty_ms
                    on_started(item)
                continue
            if not item.get("ok"):
                fault = Fault(item.get("code", "worker_error"), item.get("detail", "Unknown supervisor failure"))
                fault.worker_data = item
                raise fault
            return item.get("result")

    def start(self, match_id, side, context=None):
        msg = {"protocol_version": 2, "type": "init", "match_id": match_id,
               "payload": {"side": side, "engine": "ottv2-v1", "profile": self.profile.public()}}
        if context is not None:
            msg["payload"]["match"] = {k: v for k, v in context.items() if k != 'ruleset'}
            if context.get("basic_rules"):
                msg["payload"]["basic_rules"] = context["basic_rules"]
            if context.get('ruleset'):
                msg['protocol_version'] = 3
                msg['payload']['engine'] = 'ottv2-experimental-v1'
                msg['payload']['ruleset'] = context['ruleset']
        return self.rpc({"op": "start", "profile": self.profile.public(), "message": msg}, self.profile.startup_ms / 1000 + 10)

    def turn(self, message, on_started=None):
        return self.rpc({"op": "turn", "message": message, "budget_ms": self.profile.turn_ms},
                        self.profile.turn_ms / 1000 + 10, on_started)

    def close(self):
        final = None
        try:
            if self.transport.alive():
                final = self.rpc({"op": "close"}, 4)
        except (Fault, OSError, ValueError):
            pass
        finally:
            try:
                self.transport.kill()
            finally:
                if self._cleanup:
                    self._cleanup()
        return final
