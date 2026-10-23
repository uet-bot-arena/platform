"""M1: temporary webhook tunnel and optional local hub. Never exposes login over it."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from arena_api.github_client import GitHubClient, GitHubError
from arena_api.settings import Settings


@contextmanager
def tunnel(port, secrets):
    binary = shutil.which("cloudflared") or str(Path.home() / ".local/bin/cloudflared")
    process = subprocess.Popen([binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found = queue.Queue(maxsize=1)
    def read():
        for line in process.stdout:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match and found.empty():
                found.put(match[0])
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        try:
            url = found.get(timeout=60)
        except queue.Empty:
            raise RuntimeError("Quick Tunnel chưa cấp URL sau 60 giây") from None
        client = GitHubClient(secrets)
        # The owner explicitly approved automatic URL updates in PLAN_M1 gói 7.
        client.request("PATCH", "/app/hook/config", client.jwt(), {"url": url + "/api/v1/github/webhook"})
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=2)
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    settings = Settings()
    port = args.port or settings.port
    if not 1024 <= port <= 65535:
        parser.error("port cần trong khoảng 1024..65535")
    try:
        with tunnel(port, settings.secrets) as url:
            host = urlsplit(url).netloc
            env = {**os.environ, "ARENA_GITHUB_WEBHOOK_HOSTS": host, "ARENA_PORT": str(port),
                   "ARENA_API_ORIGIN": f"http://127.0.0.1:{port}"}
            print("Webhook:", url + "/api/v1/github/webhook", flush=True)
            print(f"Web đăng nhập: http://127.0.0.1:{port} · Ctrl+C để dừng", flush=True)
            hub = subprocess.Popen([sys.executable, "-m", "arena_api", "serve"], env=env)
            try:
                hub.wait()
            finally:
                if hub.poll() is None:
                    hub.terminate()
                    hub.wait(timeout=20)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, GitHubError, OSError) as exc:
        print("Không mở được webhook/hub:", str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
