"""Fail if any real secret value appears in the repository (working tree or git history).

Reads the operator's secrets file (ARENA_SECRETS or ~/.uet-arena/secrets.toml) and prints only
key names and file paths, never values. Run before every commit that touches config:
    python scripts/check-no-secrets.py
"""
import os
from pathlib import Path
import subprocess
import sys
import tomllib

REPO = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = Path(os.environ.get("ARENA_SECRETS") or Path.home() / ".uet-arena" / "secrets.toml")
    if not path.is_file():
        sys.exit(f"Không thấy file bí mật {path}")
    values = {k: v for k, v in tomllib.loads(path.read_text(encoding="utf-8-sig")).items()
              if isinstance(v, str) and len(v) >= 8}
    pem = values.get("github_app_private_key")
    if pem:
        key_file = Path(pem)
        if not key_file.is_file() and os.name == "posix" and pem[1:2] == ":":
            key_file = Path("/mnt", pem[0].lower(), *Path(pem[3:]).parts)
        if key_file.is_file():
            body = [line for line in key_file.read_text().splitlines() if line and not line.startswith("-----")]
            values["github_app_private_key (nội dung)"] = body[len(body) // 2] if body else ""
        values.pop("github_app_private_key")  # a path is not secret
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n")
    found = []
    for name in filter(None, files):
        file = REPO / name
        if not file.is_file() or file.stat().st_size > 20_000_000:
            continue
        data = file.read_bytes()
        for key, value in values.items():
            if value and value.encode() in data:
                found.append(f"{name}: chứa giá trị của {key}")
    history = subprocess.run(["git", "log", "--all", "-p", "--no-color"], cwd=REPO, capture_output=True).stdout
    for key, value in values.items():
        if value and value.encode() in history:
            found.append(f"lịch sử git: chứa giá trị của {key}")
    if found:
        print("\n".join(found))
        sys.exit(1)
    print(f"OK: {len(values)} giá trị bí mật không xuất hiện trong {len(files)} file và lịch sử git")


if __name__ == "__main__":
    main()
