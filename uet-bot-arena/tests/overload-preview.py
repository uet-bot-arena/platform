"""Opt-in UI fixture: real client.js handles a plain-text admission 503."""
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util

spec = importlib.util.spec_from_file_location("refresh_fixture", Path(__file__).with_name("refresh-preview.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class Handler(fixture.Handler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self.send((fixture.DIST / "live/index.html").read_text(encoding="utf-8"), "text/html")
        if path == "/api/v1/dashboard":
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Service Unavailable")
            return
        super().do_GET()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"http://127.0.0.1:{server.server_port}/#/home", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
