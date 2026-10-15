from http.server import ThreadingHTTPServer
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import zipfile

from arena.protocol import Fault
from arena.server import Application, handler


class ControlAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.token = "unit-test-only-team-a-token-0001"
        cls.app = Application(cls.tmp.name, {cls.token: "a", "unit-test-only-team-b-token-0002": "b"}, start_worker=False)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(cls.app))
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.app.service.stop.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.store.db.close()
        cls.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + "/api/v1" + path, data=data, headers=headers or {})
        try:
            result = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            result = exc
        with result:
            return result.status, json.loads(result.read()), dict(result.headers)

    def test_session_httponly_scope_and_logout(self):
        status, body, headers = self.request("/session", {"token": self.token})
        self.assertEqual(status, 200)
        self.assertEqual(body["team_id"], "a")
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(self.request("/config", headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.request("/logout", b"", {"Cookie": cookie})[0], 200)
        self.assertEqual(self.request("/config", headers={"Cookie": cookie})[0], 401)

    def test_auth_origin_and_host_fail_closed(self):
        self.assertEqual(self.request("/config")[0], 401)
        self.assertEqual(self.request("/config", headers={"Authorization": "Bearer wrong-token"})[0], 401)
        self.assertEqual(self.request("/time", headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request("/time", headers={"Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(self.request("/time", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/time")[0], 200)

    def test_upload_runtime_idempotency_and_worker_offline(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("bot.cpp", "int main(){}")
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/zip", "X-Submission-Version": "v1.0.0", "Idempotency-Key": "unit-upload-key"}
        status, source, _ = self.request("/submissions", buf.getvalue(), headers)
        self.assertEqual(status, 201)
        self.assertNotIn("source_path", source)
        self.assertEqual(self.request("/submissions", buf.getvalue(), headers)[1]["id"], source["id"])
        self.assertEqual(self.request("/submissions", buf.getvalue(), {**headers, "X-Submission-Version": "v1.0.1"})[0], 409)
        self.assertEqual(self.request("/submissions", buf.getvalue(), {**headers, "X-Arena-Runtime": "python-codon"})[0], 400)
        with patch("arena.sandbox.image_id", side_effect=Fault("worker_unavailable", "offline")):
            status, body, _ = self.request("/benchmarks", {"submission_id": source["id"]}, {"Authorization": "Bearer " + self.token, "Idempotency-Key": "unit-job-key"})
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "worker_unavailable")
        self.assertEqual(self.app.store.list("job"), [])


if __name__ == "__main__":
    unittest.main()
