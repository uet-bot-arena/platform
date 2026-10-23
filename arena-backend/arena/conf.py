"""TOML configuration and secrets shared by the hub, the mailer and the grader.

Two files, both outside the repository:
  * configuration (ARENA_CONFIG, default ~/.uet-arena/config.toml): no secrets;
  * secrets (ARENA_SECRETS, default ~/.uet-arena/secrets.toml): flat ``name = "value"``.

Errors are in Vietnamese and name the offending key. They never contain a secret value.
"""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tomllib

DEFAULT_DIR = Path.home() / ".uet-arena"


class ConfigError(ValueError):
    """Invalid configuration; the message names the key and is safe to print."""


def config_path(env=None):
    env = os.environ if env is None else env
    value = env.get("ARENA_CONFIG")
    return (Path(value).expanduser(), True) if value else (DEFAULT_DIR / "config.toml", False)


def secrets_path(env=None):
    env = os.environ if env is None else env
    value = env.get("ARENA_SECRETS")
    return (Path(value).expanduser(), True) if value else (DEFAULT_DIR / "secrets.toml", False)


def host_path(value):
    """Accept ``C:/Users/x`` written for Windows tools when this process runs in WSL/Linux."""
    text = str(value)
    if os.name == "posix" and re.match(r"^[A-Za-z]:[\\/]", text):
        windows = PureWindowsPath(text)
        mounted = PurePosixPath("/mnt", windows.drive[0].lower(), *windows.parts[1:])
        return Path(mounted)
    return Path(text).expanduser()


def _toml_error(path, what, exc):
    # tomllib messages carry only line/column; keep just that part.
    where = re.search(r"\(at line \d+, column \d+\)", str(exc))
    return ConfigError(f"File {what} {path} không phải TOML hợp lệ {where.group(0) if where else ''}".rstrip())


def load_config(path=None, required=None):
    """Return (dict, base directory). A missing default file means built-in defaults."""
    if path is None:
        path, required = config_path()
    path = Path(path)
    if not path.is_file():
        if required:
            raise ConfigError(f"Không tìm thấy file cấu hình {path} (ARENA_CONFIG)")
        return {}, None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise _toml_error(path, "cấu hình", exc) from None
    for key in data:
        if key not in SECTIONS:
            raise ConfigError(f"Cấu hình có mục lạ [{key}]; các mục hợp lệ: {', '.join(sorted(SECTIONS))}")
    return data, path.parent


SECTIONS = {"hub", "email", "accounts", "verification", "grader", "publisher", "github", "submissions"}


class Secrets:
    """Read-only secret store. repr/str never show values; lookups name only the key."""

    def __init__(self, values=None, source=None):
        self._values = dict(values or {})
        self.source = source
        for name, value in self._values.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", name):
                raise ConfigError("Tên khóa bí mật chỉ gồm a-z, 0-9, _ (tối đa 64 ký tự)")
            if not isinstance(value, str):
                raise ConfigError(f"Khóa bí mật {name} phải là chuỗi")

    @classmethod
    def load(cls, path=None, required=None):
        if path is None:
            path, required = secrets_path()
        path = Path(path)
        if not path.is_file():
            if required:
                raise ConfigError(f"Không tìm thấy file bí mật {path} (ARENA_SECRETS)")
            return cls({}, None)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except tomllib.TOMLDecodeError as exc:
            raise _toml_error(path, "bí mật", exc) from None
        for name, value in data.items():
            if isinstance(value, dict):
                raise ConfigError(f"File bí mật chỉ chứa cặp tên = \"giá trị\"; {name} là một bảng")
        return cls(data, path)

    def has(self, name):
        return bool(self._values.get(name))

    def get(self, name, purpose=None):
        value = self._values.get(name)
        if not value:
            raise ConfigError(f"Thiếu khóa bí mật {name}" + (f" (cần cho {purpose})" if purpose else "") +
                              f" trong {self.source or 'ARENA_SECRETS'}")
        return value

    def names(self):
        return sorted(self._values)

    def values(self):
        """All non-empty secret values, for redaction checks only."""
        return [v for v in self._values.values() if v]

    def redact(self, text):
        text = str(text)
        for value in sorted(self.values(), key=len, reverse=True):
            if len(value) >= 4:
                text = text.replace(value, "[bí mật]")
        return text

    def __repr__(self):
        return f"Secrets(names={self.names()!r})"

    __str__ = __repr__


class Reader:
    """Typed access to one section with Vietnamese errors naming ``section.key``."""

    def __init__(self, data, section, base=None, env=None, env_map=None):
        value = data.get(section, {})
        if not isinstance(value, dict):
            raise ConfigError(f"[{section}] phải là một bảng TOML")
        self.data, self.section, self.base = value, section, base
        self.env, self.env_map = (os.environ if env is None else env), env_map or {}
        self.used = set()

    def name(self, key):
        return f"{self.section}.{key}"

    def raw(self, key, default):
        self.used.add(key)
        variable = self.env_map.get(key)
        if variable and self.env.get(variable) not in (None, ""):
            return self.env[variable], variable
        if key in self.data:
            return self.data[key], "file"
        return default, None

    def integer(self, key, default, low, high):
        value, source = self.raw(key, default)
        if source and source != "file":
            if not str(value).strip().lstrip("-").isdigit():
                raise ConfigError(f"Cấu hình sai: {source} ({self.name(key)}) phải là số nguyên {low}..{high}")
            value = int(value)
        if type(value) is not int or not low <= value <= high:
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là số nguyên {low}..{high}" +
                              (f" (đang lấy từ {source})" if source and source != "file" else ""))
        return value

    def boolean(self, key, default):
        value, source = self.raw(key, default)
        if source and source != "file":
            if value not in ("0", "1", "true", "false"):
                raise ConfigError(f"Cấu hình sai: {source} ({self.name(key)}) phải là 0 hoặc 1")
            return value in ("1", "true")
        if type(value) is not bool:
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là true hoặc false")
        return value

    def string(self, key, default, pattern=None, allow_empty=False):
        value, source = self.raw(key, default)
        if not isinstance(value, str) or (not value and not allow_empty):
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là chuỗi" + ("" if allow_empty else " không rỗng"))
        if pattern and value and not re.fullmatch(pattern, value):
            raise ConfigError(f"Cấu hình sai: {self.name(key)} không đúng định dạng")
        return value

    def strings(self, key, default):
        value, source = self.raw(key, default)
        if source and source != "file":
            value = [part.strip() for part in str(value).split(",") if part.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là danh sách chuỗi")
        return tuple(value)

    def path(self, key, default):
        value, source = self.raw(key, default)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là đường dẫn")
        path = host_path(value)
        if not path.is_absolute() and source == "file" and self.base:
            path = self.base / path
        return path.resolve()

    def table(self, key):
        value = self.data.get(key, {})
        self.used.add(key)
        if not isinstance(value, dict):
            raise ConfigError(f"Cấu hình sai: {self.name(key)} phải là bảng")
        return value

    def finish(self):
        unknown = sorted(set(self.data) - self.used)
        if unknown:
            raise ConfigError(f"Cấu hình có khóa lạ: {', '.join(self.name(k) for k in unknown)}")
