"""Build your bot and play the bundled OTTv2 sample locally; Python stdlib only."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
from arena import engine
from arena.protocol import Fault, response


class LocalError(Exception):
    pass


def build(source, label, compiler_cpp="g++", compiler_c="gcc"):
    source = Path(source).resolve()
    names = {"bot.cpp": "cpp17", "bot.c": "c11", "bot.py": "python3"}
    if source.is_dir():
        entries = [source / name for name in names if (source / name).is_file()]
        if len(entries) != 1:
            raise LocalError("bot/ cần đúng một file chính: bot.cpp, bot.c hoặc bot.py")
        source = entries[0]
    runtime = names.get(source.name)
    if not runtime or not source.is_file():
        raise LocalError("Không tìm thấy file bot.cpp / bot.c / bot.py")
    if runtime == "python3":
        return [sys.executable, "-I", "-S", str(source)]
    compiler = compiler_cpp if runtime == "cpp17" else compiler_c
    if not shutil.which(compiler):
        raise LocalError("Chưa tìm thấy compiler " + compiler + ". Cài GCC hoặc truyền --cxx / --cc.")
    folder = ROOT / ".local-build" / label
    folder.mkdir(parents=True, exist_ok=True)
    binary = folder / ("bot.exe" if sys.platform == "win32" else "bot")
    sources = sorted(source.parent.glob("*.cpp" if runtime == "cpp17" else "*.c"))
    try:
        result = subprocess.run([compiler, "-std=c++17" if runtime == "cpp17" else "-std=c11", "-O2", "-pipe",
                                 *(["-static"] if sys.platform == "win32" else []), *map(str, sources), "-o", str(binary)], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalError("Không build được bot: " + type(exc).__name__) from None
    if result.returncode:
        raise LocalError("Biên dịch lỗi:\n" + result.stderr.decode("utf-8", "replace")[:12000])
    return [str(binary)]


class Bot:
    def __init__(self, command, label):
        folder = ROOT / ".local-build"
        folder.mkdir(exist_ok=True)
        self.stderr = open(folder / (label + ".stderr.log"), "wb")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr)
        except OSError:
            self.stderr.close()
            raise LocalError("Không khởi động được bot " + label) from None
        self.label, self.lines, self.requests, self.stopping = label, queue.Queue(2), queue.Queue(1), threading.Event()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.writer = threading.Thread(target=self.write, daemon=True)
        self.reader.start()
        self.writer.start()

    def publish(self, item):
        while not self.stopping.is_set():
            try:
                self.lines.put(item, timeout=.1)
                return
            except queue.Full:
                pass

    def write(self):
        while not self.stopping.is_set():
            try:
                raw = self.requests.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.publish(LocalError("Bot đã dừng; xem .local-build/*.stderr.log"))
                return

    def read(self):
        try:
            while not self.stopping.is_set():
                line = self.process.stdout.readline(65537)
                item = LocalError("Bot đóng stdout") if not line else LocalError("Một dòng stdout vượt 64 KiB") if len(line) > 65536 else line
                self.publish(item)
                if not line or isinstance(item, LocalError):
                    return
        except (OSError, ValueError):
            return

    def ask(self, message, timeout_ms, kind):
        try:
            self.requests.put((json.dumps(message, ensure_ascii=False) + "\n").encode(), timeout=.1)
            raw = self.lines.get(timeout=max(.001, timeout_ms / 1000))
        except queue.Empty:
            raise LocalError(self.label + ": quá thời gian trả lời") from None
        except (BrokenPipeError, OSError):
            raise LocalError(self.label + ": bot đã dừng; xem .local-build/*.stderr.log") from None
        if isinstance(raw, LocalError):
            raise LocalError(self.label + ": " + str(raw))
        try:
            return response(raw.decode("utf-8"), message, kind)
        except (Fault, UnicodeError) as exc:
            raise LocalError(self.label + ": sai giao thức: " + str(exc)) from None

    def close(self):
        self.stopping.set()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        self.writer.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()
        self.stderr.close()


def play(red, blue, match_ms=60000, max_plies=512, check=False):
    state, match = engine.initial_state(), uuid.uuid4().hex
    bots, remaining = {"red": red, "blue": blue}, {"red": match_ms, "blue": match_ms}
    for side, bot in bots.items():
        bot.ask({"protocol_version": 2, "type": "init", "match_id": match,
                 "payload": {"side": side, "profile": {"match_ms": match_ms}}}, 2000, "ready")
    def key():
        return json.dumps([state["board"], state["to_move"]], sort_keys=True, separators=(",", ":"))
    repetitions = {key(): 1}
    while not state["result"] and state["ply"] < max_plies:
        side = state["to_move"]
        message = {"protocol_version": 2, "type": "observation", "match_id": match,
                   "turn_id": state["ply"] + 1, "state_version": state["ply"], "payload": engine.observation(state)}
        started = time.monotonic()
        answer = bots[side].ask(message, remaining[side], "action")
        remaining[side] -= (time.monotonic() - started) * 1000
        if remaining[side] <= 0:
            raise LocalError(side + ": đã dùng hết quỹ giờ")
        try:
            state = engine.apply(state, answer["payload"])
        except ValueError as exc:
            raise LocalError(side + ": nước đi không hợp lệ: " + str(exc)) from None
        fingerprint = key()
        repetitions[fingerprint] = repetitions.get(fingerprint, 0) + 1
        if not state["result"] and repetitions[fingerprint] >= 3:
            engine.finish(state, None, "threefold_repetition")
    if not state["result"]:
        engine.finish(state, None, "local_check_complete" if check else "ply_limit")
    return {"plies": state["ply"], "result": state["result"], "local": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", type=Path, default=ROOT / "bot")
    parser.add_argument("--opponent", type=Path, default=ROOT / "examples/bot.py")
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--check", action="store_true", help="Kiểm build, ready và hai lượt đi")
    parser.add_argument("--max-plies", type=int, default=512)
    args = parser.parse_args()
    if not 1 <= args.max_plies <= 512:
        parser.error("--max-plies phải từ 1 tới 512")
    commands = [build(path, side, args.cxx, args.cc) for path, side in ((args.bot, "red"), (args.opponent, "blue"))]
    with ExitStack() as stack:
        bots = []
        for command, side in zip(commands, ("red", "blue")):
            bot = Bot(command, side)
            stack.callback(bot.close)
            bots.append(bot)
        print(json.dumps(play(*bots, max_plies=2 if args.check else args.max_plies, check=args.check), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except LocalError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
