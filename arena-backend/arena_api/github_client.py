"""GitHub App REST client. Credentials stay in memory; user URLs never reach HTTP."""
import base64
from datetime import datetime
import json
import re
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from arena.conf import host_path
from arena.protocol import Fault

_http_threads = threading.local()

API = "https://api.github.com"
SHA = re.compile(r"[0-9a-f]{40}")
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def repository_url(url):
    """Accept the displayed GitHub repository URL, never credentials/query/ref URLs."""
    if not isinstance(url, str) or len(url) > 180:
        raise Fault("invalid_request", "Dán link GitHub dạng github.com/chủ-repo/tên-repo")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise Fault("invalid_request", "Link repo GitHub không hợp lệ") from None
    path = parts.path.removesuffix("/").split("/")
    if (parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment
            or len(path) != 3 or not OWNER.fullmatch(path[1]) or not REPO.fullmatch(path[2])
            or path[2] in (".", "..") or path[2].endswith(".git")):
        raise Fault("invalid_request", "Link repo phải là GitHub dạng github.com/chủ-repo/tên-repo, không có nhánh hay tham số")
    return path[1], path[2]


def unix_ms(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


class GitHubError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub API HTTP {status}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, secrets, max_tarball_bytes=10 * 1024 * 1024, transport=None, clock=time.time):
        self.secrets, self.limit, self.clock = secrets, max_tarball_bytes, clock
        self.transport = transport or self.http
        self.tokens, self.token_locks, self.lock = {}, {}, threading.Lock()

    @staticmethod
    def http(method, url, headers, body, limit):
        request = Request(url, data=body, headers=headers, method=method)
        try:
            # HTTPSHandler loads the system CA store when it is constructed.
            # Reuse the opener within each worker; headers remain on Request.
            if not hasattr(_http_threads, 'opener'):
                _http_threads.opener = build_opener(NoRedirect)
            response = _http_threads.opener.open(request, timeout=15)
        except HTTPError as exc:
            response = exc
        except (URLError, OSError, TimeoutError):
            raise GitHubError(503) from None
        with response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise GitHubError(413)
            return response.status, dict(response.headers.items()), data

    def jwt(self):
        def encode(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=")
        now = int(self.clock())
        head = encode(b'{"alg":"RS256","typ":"JWT"}')
        body = encode(json.dumps({"iat": now - 60, "exp": now + 540,
                                  "iss": self.secrets.get("github_app_id")}).encode())
        message = head + b"." + body
        try:
            signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign",
                                     str(host_path(self.secrets.get("github_app_private_key")))],
                                    input=message, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            raise GitHubError(503) from None
        if signed.returncode:
            raise GitHubError(503)
        return (message + b"." + encode(signed.stdout)).decode()

    def request(self, method, path, token, body=None, headers=None):
        # All callers construct paths from validated owner/repo/SHA or numeric IDs.
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path required")
        status, response_headers, data = self.transport(method, API + path,
            {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "UET-Bot-Arena",
             "Content-Type": "application/json", **(headers or {})},
            json.dumps(body).encode() if body is not None else None, 2 * 1024 * 1024)
        lower_headers = {k.lower(): v for k, v in response_headers.items()}
        if status == 403 and (lower_headers.get("x-ratelimit-remaining") == "0" or "retry-after" in lower_headers):
            raise GitHubError(429)
        if status != 304 and not 200 <= status < 300:
            raise GitHubError(status)
        try:
            return status, {k.lower(): v for k, v in response_headers.items()}, json.loads(data) if data else None
        except (ValueError, UnicodeError):
            raise GitHubError(502) from None

    def installation(self, owner, repo):
        value = self.request("GET", f"/repos/{owner}/{repo}/installation", self.jwt())[2]
        if type(value.get("id")) is not int:
            raise GitHubError(502)
        return value["id"]

    def token(self, installation, repo):
        key = installation, repo.lower()
        with self.lock:
            gate = self.token_locks.setdefault(key, threading.Lock())
        with gate:
            old = self.tokens.get(key)
            if old and old[1] > self.clock() + 120:
                return old[0]
            value = self.request("POST", f"/app/installations/{int(installation)}/access_tokens", self.jwt(),
                                 {"repositories": [repo], "permissions": {"contents": "read", "metadata": "read", "statuses": "write"}})[2]
            self.tokens[key] = value["token"], unix_ms(value["expires_at"]) / 1000
            return value["token"]

    def get(self, owner, repo, installation, suffix="", headers=None):
        return self.request("GET", f"/repos/{owner}/{repo}{suffix}", self.token(installation, repo), headers=headers)

    def tarball(self, owner, repo, installation, sha):
        if not SHA.fullmatch(sha):
            raise ValueError("Pinned SHA required")
        token = self.token(installation, repo)
        status, headers, data = self.transport("GET", f"{API}/repos/{owner}/{repo}/tarball/{sha}",
            {"Authorization": "Bearer " + token, "User-Agent": "UET-Bot-Arena"}, None, self.limit)
        if status in (301, 302, 307, 308):
            location = {k.lower(): v for k, v in headers.items()}.get("location", "")
            parts = urlsplit(location)
            if (parts.scheme != "https" or parts.netloc != "codeload.github.com" or parts.fragment
                    or not parts.path.startswith(f"/{owner}/{repo}/")):
                raise GitHubError(502)
            # Temporary signed codeload URL; never forward the installation token.
            status, _, data = self.transport("GET", location, {"User-Agent": "UET-Bot-Arena"}, None, self.limit)
        if status != 200:
            raise GitHubError(status)
        if len(data) > self.limit:
            raise GitHubError(413)
        return data

    def activities(self, owner, repo, installation, deadline_ms=None):
        """Cursor pagination includes force pushes; timestamps belong to GitHub, not Git."""
        path = "/activity?" + urlencode({"ref": "refs/heads/main", "per_page": 100, "direction": "desc"})
        result, visited = [], set()
        for _ in range(100):
            _, headers, rows = self.get(owner, repo, installation, path)
            if not isinstance(rows, list):
                raise GitHubError(502)
            # API 2026-03-10 uses timestamp; older versions/docs use pushed_at.
            for row in rows:
                row["pushed_at"] = row.get("timestamp") or row.get("pushed_at")
                if row.get("ref") == "refs/heads/main":
                    try:
                        unix_ms(row["pushed_at"])
                    except (ValueError, TypeError, AttributeError, OverflowError):
                        raise GitHubError(502) from None
            result.extend(r for r in rows if r.get("ref") == "refs/heads/main" and SHA.fullmatch(r.get("after", ""))
                          and r.get("pushed_at") and (deadline_ms is None or unix_ms(r["pushed_at"]) <= deadline_ms))
            if deadline_ms is None or len({r["after"] for r in result}) >= 6:
                return result
            match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("link", ""))
            if not match:
                return result
            next_url = urlsplit(match[1])
            if (next_url.scheme != "https" or next_url.netloc != "api.github.com"
                    or next_url.path != f"/repos/{owner}/{repo}/activity" or match[1] in visited):
                raise GitHubError(502)
            visited.add(match[1])
            path = "/activity?" + next_url.query
        # Never silently choose a newer/partial history when the cap was reached.
        raise GitHubError(503)

    def status(self, owner, repo, installation, sha, passed, description, details_url):
        if not SHA.fullmatch(sha):
            raise ValueError("Pinned SHA required")
        return self.request("POST", f"/repos/{owner}/{repo}/statuses/{sha}", self.token(installation, repo),
                            {"state": "success" if passed else "failure", "context": "uet-bot-arena/check",
                             "description": description[:140], "target_url": details_url})
