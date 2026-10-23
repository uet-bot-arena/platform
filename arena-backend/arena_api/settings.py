"""Hub settings: explicit argument > environment variable > config file > built-in default.

The config file has no secrets; see config/arena.example.toml. Secrets come from a second
file (config/secrets.example.toml) and are only reachable through ``Settings.secrets``.
"""
from dataclasses import dataclass, field, fields
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from arena.conf import ConfigError, Reader, Secrets, load_config

UNSET = object()
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")
DAY = 86_400
TLS_MODES = ("starttls", "ssl", "none")


@dataclass(frozen=True)
class EmailProvider:
    name: str
    host: str
    port: int
    tls: str
    sender: str
    sender_name: str = "UET Bot Arena"
    user_secret: str = ""
    password_secret: str = ""
    daily_limit: int = 200
    hourly_limit: int = 50
    timeout_seconds: int = 30


@dataclass(frozen=True)
class AccountRules:
    # Student addresses: 8-digit student number at the university domain. No faculty filter.
    student_email: str = r"^\d{8}@vnu\.edu\.vn$"
    # Other addresses under these domains become staff accounts; any other domain is refused.
    staff_domains: tuple = ("vnu.edu.vn",)
    block_plus: bool = True
    cohort_base: int = 2000
    # 0 = derive from today's date (the intake year of the current academic year).
    freshman_cohort: int = 0
    team_size: int = 4
    name_max: int = 40
    season_id: str = "default"


@dataclass(frozen=True)
class Verification:
    code_ttl_seconds: int = 600
    resend_seconds: int = 60
    drop_after_seconds: int = 1800
    max_attempts: int = 5


@dataclass(frozen=True)
class GraderSettings:
    lease_seconds: int = 30
    heartbeat_seconds: int = 5
    max_attempts: int = 3
    long_poll_seconds: int = 25


@dataclass(frozen=True)
class PublisherSettings:
    interval_seconds: int = 5
    output_dir: Path | None = None


@dataclass(frozen=True)
class GitHubSettings:
    api_url: str = "https://api.github.com"
    # Hosts (e.g. a Quick Tunnel name) allowed only for the webhook path.
    webhook_hosts: tuple = ()
    max_tarball_bytes: int = 10 * 1024 * 1024
    repo_prefix: str = "ott2026-"
    poll_seconds: int = 60


@dataclass(frozen=True)
class SubmissionSettings:
    max_per_hour: int = 30


HUB_ENV = {"data_dir": "ARENA_API_DATA", "origin": "ARENA_API_ORIGIN", "cookie_secure": "ARENA_API_COOKIE_SECURE",
           "trusted_proxies": "ARENA_TRUSTED_PROXIES", "connection_limit": "ARENA_CONNECTION_LIMIT",
           "thread_limit": "ARENA_THREAD_LIMIT", "hash_workers": "ARENA_HASH_WORKERS", "ui_root": "ARENA_UI_ROOT",
           "port": "ARENA_PORT", "bind": "ARENA_BIND", "session_seconds": "ARENA_SESSION_SECONDS"}


@dataclass(frozen=True)
class Settings:
    data: Path = UNSET
    origin: str = UNSET
    port: int = UNSET
    bind: str = UNSET
    cookie_secure: bool = UNSET
    session_seconds: int = UNSET
    trusted_proxies: tuple = UNSET
    # Sync endpoints and Argon2 waits run in this pool; the AnyIO default of 40 threads makes a
    # login burst block uploads and public reads.
    connection_limit: int = UNSET
    thread_limit: int = UNSET
    hash_workers: int = UNSET
    ui_root: Path = UNSET
    email_providers: tuple = UNSET
    account_rules: AccountRules = UNSET
    verification: Verification = UNSET
    grader: GraderSettings = UNSET
    publisher: PublisherSettings = UNSET
    github: GitHubSettings = UNSET
    submissions: SubmissionSettings = UNSET
    secrets: Secrets = field(default=UNSET, repr=False)
    config_file: Path | None = None

    def __post_init__(self):
        unset = [f.name for f in fields(self) if getattr(self, f.name) is UNSET]
        if unset:
            for name, value in load(unset, self.config_file).items():
                object.__setattr__(self, name, value)
        validate(self)


def _set(target, name, value):
    object.__setattr__(target, name, value)


def load(names, path=None, env=None):
    """Values for the requested fields from environment, config file and defaults."""
    env = os.environ if env is None else env
    if path is None and env.get("ARENA_CONFIG", "").lower() == "none":
        data, base = {}, None
    else:
        data, base = load_config(path, True if path else None)
    hub = Reader(data, "hub", base, env, HUB_ENV)
    values = {}
    port = hub.integer("port", 4181, 1024, 65535)
    values["port"] = port
    values["bind"] = hub.string("bind", "127.0.0.1", r"[0-9A-Za-z.:\[\]-]+")
    values["data"] = hub.path("data_dir", ".platform-state")
    values["origin"] = hub.string("origin", f"http://127.0.0.1:{port}")
    values["cookie_secure"] = hub.boolean("cookie_secure", False)
    values["session_seconds"] = hub.integer("session_seconds", 14 * DAY, 300, 30 * DAY)
    values["trusted_proxies"] = hub.strings("trusted_proxies", [])
    values["connection_limit"] = hub.integer("connection_limit", 1024, 256, 16384)
    values["thread_limit"] = hub.integer("thread_limit", 256, 40, 4096)
    values["hash_workers"] = hub.integer("hash_workers", 4, 1, 32)
    values["ui_root"] = hub.path("ui_root", str(Path(__file__).resolve().parents[2] / "uet-bot-arena" / "dist"))
    hub.finish()
    values["email_providers"] = email_providers(data)
    values["account_rules"] = account_rules(data)
    section = Reader(data, "verification")
    values["verification"] = Verification(
        code_ttl_seconds=section.integer("code_ttl_seconds", 600, 60, 3600),
        resend_seconds=section.integer("resend_seconds", 60, 10, 3600),
        drop_after_seconds=section.integer("drop_after_seconds", 1800, 60, DAY),
        max_attempts=section.integer("max_attempts", 5, 1, 20))
    section.finish()
    section = Reader(data, "grader")
    values["grader"] = GraderSettings(
        lease_seconds=section.integer("lease_seconds", 30, 10, 600),
        heartbeat_seconds=section.integer("heartbeat_seconds", 5, 1, 60),
        max_attempts=section.integer("max_attempts", 3, 1, 10),
        long_poll_seconds=section.integer("long_poll_seconds", 25, 1, 55))
    section.finish()
    if values["grader"].heartbeat_seconds * 2 > values["grader"].lease_seconds:
        raise ConfigError("Cấu hình sai: grader.heartbeat_seconds phải nhỏ hơn một nửa grader.lease_seconds")
    section = Reader(data, "publisher", base)
    values["publisher"] = PublisherSettings(
        interval_seconds=section.integer("interval_seconds", 5, 1, 60),
        output_dir=section.path("output_dir", None))
    section.finish()
    section = Reader(data, "github", env=env, env_map={"webhook_hosts": "ARENA_GITHUB_WEBHOOK_HOSTS"})
    values["github"] = GitHubSettings(
        api_url=section.string("api_url", "https://api.github.com", r"https://[A-Za-z0-9.-]+(?::\d+)?"),
        webhook_hosts=section.strings("webhook_hosts", []),
        max_tarball_bytes=section.integer("max_tarball_bytes", 10 * 1024 * 1024, 1024, 10 * 1024 * 1024),
        repo_prefix=section.string("repo_prefix", "ott2026-", r"[a-z0-9-]{1,40}"),
        poll_seconds=section.integer("poll_seconds", 60, 5, 3600))
    section.finish()
    section = Reader(data, "submissions")
    values["submissions"] = SubmissionSettings(max_per_hour=section.integer("max_per_hour", 30, 1, 1000))
    section.finish()
    for host in values["github"].webhook_hosts:
        if not re.fullmatch(r"[a-z0-9.-]+(?::\d+)?", host):
            raise ConfigError("Cấu hình sai: github.webhook_hosts chỉ chứa tên máy, ví dụ abc.trycloudflare.com")
    if "secrets" in names:
        values["secrets"] = Secrets({}) if env.get("ARENA_SECRETS", "").lower() == "none" else Secrets.load()
    return {name: values[name] for name in names}


def email_providers(data):
    section = Reader(data, "email")
    items = section.raw("providers", [])[0]
    section.finish()
    if not isinstance(items, list):
        raise ConfigError("Cấu hình sai: email.providers phải là danh sách [[email.providers]]")
    providers, names = [], set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"Cấu hình sai: email.providers[{index}] phải là bảng")
        reader = Reader({"p": item}, "p")
        reader.section = f"email.providers[{index}]"
        provider = EmailProvider(
            name=reader.string("name", "", r"[a-z0-9_-]{1,32}"),
            host=reader.string("host", "", r"[A-Za-z0-9.-]+"),
            port=reader.integer("port", 587, 1, 65535),
            tls=reader.string("tls", "starttls"),
            sender=reader.string("sender", ""),
            sender_name=reader.string("sender_name", "UET Bot Arena"),
            user_secret=reader.string("user_secret", "", r"[a-z0-9_]{1,64}", allow_empty=True),
            password_secret=reader.string("password_secret", "", r"[a-z0-9_]{1,64}", allow_empty=True),
            daily_limit=reader.integer("daily_limit", 200, 1, 1_000_000),
            hourly_limit=reader.integer("hourly_limit", 50, 1, 1_000_000),
            timeout_seconds=reader.integer("timeout_seconds", 30, 1, 120))
        reader.finish()
        if provider.tls not in TLS_MODES:
            raise ConfigError(f"Cấu hình sai: email.providers[{index}].tls phải là một trong {', '.join(TLS_MODES)}")
        if not re.fullmatch(r"[^@\s<>]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", provider.sender):
            raise ConfigError(f"Cấu hình sai: email.providers[{index}].sender phải là địa chỉ email")
        if bool(provider.user_secret) != bool(provider.password_secret):
            raise ConfigError(f"Cấu hình sai: email.providers[{index}] cần cả user_secret và password_secret, hoặc bỏ cả hai")
        if provider.tls == "none" and provider.user_secret and provider.host not in LOOPBACK:
            raise ConfigError(f"Cấu hình sai: email.providers[{index}] gửi mật khẩu SMTP thì phải dùng TLS")
        if provider.hourly_limit > provider.daily_limit:
            raise ConfigError(f"Cấu hình sai: email.providers[{index}].hourly_limit lớn hơn daily_limit")
        if provider.name in names:
            raise ConfigError(f"Cấu hình sai: tên dịch vụ email {provider.name} bị trùng")
        names.add(provider.name)
        providers.append(provider)
    return tuple(providers)


def account_rules(data):
    section = Reader(data, "accounts")
    rules = AccountRules(
        student_email=section.string("student_email", AccountRules.student_email),
        staff_domains=section.strings("staff_domains", list(AccountRules.staff_domains)),
        block_plus=section.boolean("block_plus", True),
        cohort_base=section.integer("cohort_base", 2000, 1900, 2100),
        freshman_cohort=section.integer("freshman_cohort", 0, 0, 2200),
        team_size=section.integer("team_size", 4, 1, 8),
        name_max=section.integer("name_max", 40, 8, 80),
        season_id=section.string("season_id", "default", r"[A-Za-z0-9_-]{1,40}"))
    section.finish()
    try:
        re.compile(rules.student_email)
    except re.error:
        raise ConfigError("Cấu hình sai: accounts.student_email không phải biểu thức chính quy hợp lệ") from None
    for domain in rules.staff_domains:
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
            raise ConfigError("Cấu hình sai: accounts.staff_domains chỉ chứa tên miền viết thường, ví dụ vnu.edu.vn")
    return rules


def validate(settings):
    if settings.github.api_url != "https://api.github.com":
        raise ConfigError("github.api_url phải là api.github.com qua HTTPS; test dùng transport giả")
    for network in settings.trusted_proxies:
        try:
            ipaddress.ip_network(network.strip())
        except ValueError:
            raise ConfigError(f"Cấu hình sai: hub.trusted_proxies có mạng không hợp lệ: {network}") from None
    if not 40 <= settings.thread_limit <= 4096 or not 1 <= settings.hash_workers <= 32:
        raise ConfigError("Cấu hình sai: hub.thread_limit phải 40..4096 và hub.hash_workers 1..32")
    if not 256 <= settings.connection_limit <= 16384:
        raise ConfigError("Cấu hình sai: hub.connection_limit phải 256..16384")
    parts = urlsplit(settings.origin)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
        raise ConfigError("Cấu hình sai: hub.origin phải là origin chính xác, không có dấu / ở cuối, ví dụ http://127.0.0.1:4181")
    if parts.hostname not in LOOPBACK and (parts.scheme != "https" or not settings.cookie_secure):
        raise ConfigError("Cấu hình sai: hub.origin công khai phải dùng https và hub.cookie_secure = true")
    if not 300 <= settings.session_seconds <= 30 * DAY:
        raise ConfigError("Cấu hình sai: hub.session_seconds phải 300..2592000 (5 phút..30 ngày)")
    if not isinstance(settings.secrets, Secrets):
        raise ConfigError("Cấu hình sai: secrets phải là đối tượng Secrets")
    _set(settings, "data", Path(settings.data).resolve())
    _set(settings, "ui_root", Path(settings.ui_root).resolve())
    _set(settings, "trusted_proxies", tuple(settings.trusted_proxies))


class Lease:
    """Single process owns the controller queue and its data directory."""
    def __init__(self, root):
        import fcntl
        Path(root).mkdir(parents=True, exist_ok=True)
        self.file = (Path(root) / "controller.lock").open("a")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError("Another controller owns this data directory") from None

    def close(self):
        self.file.close()
