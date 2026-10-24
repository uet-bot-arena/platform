"""Gói 1: configuration and secrets live outside the code."""
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from arena.conf import ConfigError, Secrets, host_path
from arena_api.app import create_app
from arena_api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SENTINEL = "sentinel-secret-value-7f3a9c"
HERMETIC = {"ARENA_CONFIG": "none", "ARENA_SECRETS": "none"}


def write(directory, name, text):
    path = Path(directory) / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class ConfigFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("ARENA_")}
        self.env = patch.dict(os.environ, {**clean, **HERMETIC}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_defaults_without_file(self):
        settings = Settings()
        self.assertEqual(settings.origin, "http://127.0.0.1:4181")
        self.assertEqual(settings.port, 4181)
        self.assertEqual(settings.session_seconds, 14 * 86400)
        self.assertEqual(settings.verification.code_ttl_seconds, 600)
        self.assertEqual(settings.verification.resend_seconds, 60)
        self.assertEqual(settings.verification.drop_after_seconds, 1800)
        self.assertEqual((settings.grader.lease_seconds, settings.grader.heartbeat_seconds, settings.grader.max_attempts), (30, 5, 3))
        self.assertEqual(settings.email_providers, ())
        self.assertEqual(settings.secrets.names(), [])

    def test_everything_changes_through_the_file(self):
        path = write(self.dir, "arena.toml", """
            [hub]
            origin = "http://127.0.0.1:5999"
            port = 5999
            data_dir = "state"
            session_seconds = 604800
            trusted_proxies = ["127.0.0.1/32"]
            thread_limit = 64
            hash_workers = 2
            [[email.providers]]
            name = "mailpit"
            host = "127.0.0.1"
            port = 1025
            tls = "none"
            sender = "no-reply@arena.test"
            daily_limit = 1000
            hourly_limit = 100
            [[email.providers]]
            name = "relay"
            host = "smtp.relay.test"
            port = 465
            tls = "ssl"
            user_secret = "relay_user"
            password_secret = "relay_password"
            sender = "bot@relay.test"
            [accounts]
            student_email = '^\\d{9}@uni\\.test$'
            staff_domains = ["uni.test"]
            block_plus = false
            team_size = 3
            [verification]
            code_ttl_seconds = 300
            [grader]
            lease_seconds = 40
            max_attempts = 5
            [publisher]
            interval_seconds = 7
            output_dir = "snap"
        """)
        settings = Settings(config_file=path)
        self.assertEqual(settings.origin, "http://127.0.0.1:5999")
        self.assertEqual(settings.port, 5999)
        self.assertEqual(settings.data, (self.dir / "state").resolve())
        self.assertEqual(settings.session_seconds, 604800)
        self.assertEqual(settings.trusted_proxies, ("127.0.0.1/32",))
        self.assertEqual([p.name for p in settings.email_providers], ["mailpit", "relay"])
        self.assertEqual(settings.email_providers[1].tls, "ssl")
        self.assertEqual(settings.email_providers[1].password_secret, "relay_password")
        self.assertEqual(settings.account_rules.student_email, r"^\d{9}@uni\.test$")
        self.assertEqual(settings.account_rules.staff_domains, ("uni.test",))
        self.assertFalse(settings.account_rules.block_plus)
        self.assertEqual(settings.account_rules.team_size, 3)
        self.assertEqual(settings.verification.code_ttl_seconds, 300)
        self.assertEqual(settings.grader.lease_seconds, 40)
        self.assertEqual(settings.grader.max_attempts, 5)
        self.assertEqual(settings.publisher.output_dir, (self.dir / "snap").resolve())
        # The same file is found through ARENA_CONFIG.
        with patch.dict(os.environ, {"ARENA_CONFIG": str(path)}):
            self.assertEqual(Settings().origin, "http://127.0.0.1:5999")

    def test_environment_overrides_file_and_explicit_overrides_both(self):
        path = write(self.dir, "arena.toml", '[hub]\norigin = "http://127.0.0.1:5000"\nport = 5000\n')
        with patch.dict(os.environ, {"ARENA_CONFIG": str(path), "ARENA_API_ORIGIN": "http://localhost:5001", "ARENA_PORT": "5001"}):
            settings = Settings()
            self.assertEqual((settings.origin, settings.port), ("http://localhost:5001", 5001))
            self.assertEqual(Settings(origin="http://127.0.0.1:6000").origin, "http://127.0.0.1:6000")
        with patch.dict(os.environ, {"ARENA_PORT": "4999"}):
            self.assertEqual(Settings().origin, "http://127.0.0.1:4999", "default origin follows the port")

    def test_errors_are_vietnamese_and_name_the_key(self):
        cases = {
            "[hub]\nport = \"abc\"\n": "hub.port",
            "[hub]\nsession_seconds = 10\n": "hub.session_seconds",
            "[hub]\nportt = 1\n": "hub.portt",
            "[hubb]\n": "[hubb]",
            "[hub]\norigin = \"http://arena.example\"\n": "hub.origin",
            "[hub]\ntrusted_proxies = [\"300.1.1.1/8\"]\n": "hub.trusted_proxies",
            "[accounts]\nstudent_email = \"([\"\n": "accounts.student_email",
            "[grader]\nlease_seconds = 10\nheartbeat_seconds = 6\n": "grader.heartbeat_seconds",
            "[[email.providers]]\nname = \"x\"\nhost = \"h.test\"\ntls = \"tls13\"\nsender = \"a@b.test\"\n": "email.providers[0].tls",
            "[[email.providers]]\nname = \"x\"\nhost = \"h.test\"\nsender = \"not-an-email\"\n": "email.providers[0].sender",
            "[[email.providers]]\nname = \"x\"\nhost = \"smtp.test\"\ntls = \"none\"\nsender = \"a@b.test\"\nuser_secret = \"u\"\npassword_secret = \"p\"\n": "TLS",
            "[hub\n": "TOML",
        }
        for text, key in cases.items():
            with self.subTest(key=key):
                path = write(self.dir, "bad.toml", text)
                with self.assertRaises(ConfigError) as caught:
                    Settings(config_file=path)
                self.assertIn(key, str(caught.exception))
                self.assertRegex(str(caught.exception), r"Cấu hình|cấu hình")
        with patch.dict(os.environ, {"ARENA_PORT": "abc"}):
            with self.assertRaisesRegex(ConfigError, "ARENA_PORT"):
                Settings()
        with patch.dict(os.environ, {"ARENA_CONFIG": str(self.dir / "missing.toml")}):
            with self.assertRaisesRegex(ConfigError, "Không tìm thấy file cấu hình"):
                Settings()

    def test_example_files_are_valid_and_contain_only_fake_values(self):
        example = REPO / "config" / "arena.example.toml"
        settings = Settings(config_file=example)
        self.assertEqual([p.name for p in settings.email_providers], ["mailjet", "gmail"])
        secrets = Secrets.load(REPO / "config" / "secrets.example.toml", True)
        self.assertIn("verification_hmac_key", secrets.names())
        self.assertTrue(all(set(v) <= {"0"} or "example" in v or "doi-thanh" in v or "ten-ban" in v or v.isdigit()
                            for v in secrets.values()), "example secrets must be obviously fake")


class SecretsNeverLeak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_repr_lookup_and_parse_errors_hide_values(self):
        secrets = Secrets({"smtp_password": SENTINEL, "empty": ""})
        self.assertNotIn(SENTINEL, repr(secrets))
        self.assertNotIn(SENTINEL, str(secrets))
        self.assertEqual(secrets.get("smtp_password"), SENTINEL)
        with self.assertRaises(ConfigError) as caught:
            secrets.get("empty", "gửi thư")
        self.assertIn("empty", str(caught.exception))
        self.assertEqual(secrets.redact(f"535 auth failed for {SENTINEL}"), "535 auth failed for [bí mật]")
        settings = Settings(data=self.dir, secrets=secrets)
        self.assertNotIn(SENTINEL, repr(settings))
        bad = write(self.dir, "secrets.toml", f'smtp_password = "{SENTINEL}"\nbroken = {SENTINEL}\n')
        with self.assertRaises(ConfigError) as caught:
            Secrets.load(bad, True)
        self.assertNotIn(SENTINEL, str(caught.exception))
        typed = write(self.dir, "typed.toml", f'smtp_password = "{SENTINEL}"\nport = 5\n')
        with self.assertRaises(ConfigError) as caught:
            Secrets.load(typed, True)
        self.assertIn("port", str(caught.exception))
        self.assertNotIn(SENTINEL, str(caught.exception))

    def test_app_logs_and_errors_do_not_contain_secret_values(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        previous = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            settings = Settings(data=self.dir / "db", origin="http://127.0.0.1:4181",
                                secrets=Secrets({"verification_hmac_key": SENTINEL, "smtp_password": SENTINEL + "2"}))
            app = create_app(settings, start_worker=False)
            with TestClient(app, base_url=settings.origin) as client:
                bodies = [client.get("/api/v1/platform").text, client.get("/api/v1/health/live").text,
                          client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x" * 9}).text,
                          client.get("/api/v1/auth/me").text]
            for body in bodies:
                self.assertNotIn(SENTINEL, body)
        finally:
            logging.getLogger().removeHandler(handler)
            logging.getLogger().setLevel(previous)
        self.assertNotIn(SENTINEL, stream.getvalue())

    def test_session_lifetime_comes_from_settings(self):
        settings = Settings(data=self.dir / "db", origin="http://127.0.0.1:4181", session_seconds=7 * 86400,
                            secrets=Secrets({}))
        app = create_app(settings, start_worker=False)
        with TestClient(app, base_url=settings.origin) as client:
            app.state.accounts.bootstrap_admin("organizer", "test-only-admin-password")
            login = client.post("/api/v1/auth/login", json={"username": "organizer", "password": "test-only-admin-password"})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertIn("Max-Age=604800", login.headers["set-cookie"])
            remaining = login.json()["expires_ms"] - __import__("time").time() * 1000
            self.assertGreater(remaining, 7 * 86400 * 1000 - 60_000)

    def test_windows_paths_from_the_shared_secrets_file_work_in_wsl(self):
        if os.name == "posix":
            self.assertEqual(host_path("C:/Users/x/.uet-arena/github-app.pem"), Path("/mnt/c/Users/x/.uet-arena/github-app.pem"))
            self.assertEqual(host_path(r"D:\keys\app.pem"), Path("/mnt/d/keys/app.pem"))
        self.assertEqual(host_path("relative/file"), Path("relative/file"))


class NoHardcodedDeploymentValues(unittest.TestCase):
    """URLs, email addresses and keys belong in config/secrets files, not in code."""
    ALLOWED_URLS = {
        "https://json-schema.org/draft/2020-12/schema",  # JSON Schema identifier, not a deployment
        "https://api.github.com",  # fixed App API; fake transport in tests
        "https://git-lfs.github.com/spec/v1",  # Git LFS pointer format
        "https://github.com/apps/uet-bot-arena/installations/new",  # App identity from PLAN_M1
        "https://github.com/uet-bot-arena/ott-bot-starter",  # approved public template
    }

    def sources(self):
        for folder in (ROOT / "arena", ROOT / "arena_api", ROOT / "scripts", REPO / "scripts"):
            yield from (p for p in folder.rglob("*") if p.suffix in (".py", ".sh", ".ps1") and "__pycache__" not in p.parts)
        live = REPO / "uet-bot-arena" / "dist" / "live"
        yield from (p for p in live.rglob("*") if p.suffix in (".js", ".html"))

    def test_no_urls_emails_or_keys_in_code(self):
        problems = []
        for path in self.sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for url in re.findall(r"https?://[A-Za-z0-9.\-]+(?::\d+)?[^\s'\"`)]*", text):
                host = re.match(r"https?://([^/:]+)", url).group(1)
                if host in ("127.0.0.1", "localhost") or url.rstrip("/") in self.ALLOWED_URLS or "{" in url:
                    continue
                if path.parent.name == "scripts" and path.name in ("install-dev-tools.sh", "build-rootfs.sh", "install-grader.sh", "install-grader.ps1", "install-hub.sh", "fetch-toolchain.py"):
                    continue  # pinned download sources with SHA-256 checks
                problems.append(f"{path.relative_to(REPO)}: URL {url}")
            for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}", text):
                if email.endswith((".test", ".invalid", ".example")) or "@example." in email:
                    continue
                problems.append(f"{path.relative_to(REPO)}: email {email}")
            for key in re.findall(r"(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}", text):
                problems.append(f"{path.relative_to(REPO)}: key-like value")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
