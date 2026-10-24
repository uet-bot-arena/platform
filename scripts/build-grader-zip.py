#!/usr/bin/env python3
"""Build the grader install ZIP that the hub serves at /downloads/.

The ZIP holds only grader code, the SDK, the two installer scripts with their
SHA-256 files and two docs: no config, secret or token. It is deterministic
(sorted names, fixed timestamps), so the same source always gives the same
SHA-256 and file name. Standard library only; install-hub.sh runs it as root.

    python3 scripts/build-grader-zip.py --source . --out /opt/uet-arena/downloads
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

TREES = ("arena-backend/arena", "arena-backend/sdk")
FILES = (".gitattributes", "docs/MAY-CHAM.md", "docs/SANDBOX.md",
         "scripts/install-grader.ps1", "scripts/install-grader.ps1.sha256",
         "scripts/install-grader.sh", "scripts/install-grader.sh.sha256")
OPTIONAL = {".gitattributes"}
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
NAME = re.compile(r"^uet-arena-grader-[0-9a-f]{12}\.zip$")
MANIFEST = "grader-latest.json"


def members(source):
    found = []
    for tree in TREES:
        root = source / tree
        if not root.is_dir():
            raise SystemExit(f"Missing {tree} under {source}")
        for path in root.rglob("*"):
            relative = path.relative_to(source)
            if path.is_file() and not SKIP_PARTS.intersection(relative.parts) and path.suffix not in SKIP_SUFFIXES:
                found.append(relative.as_posix())
    for name in FILES:
        if (source / name).is_file():
            found.append(name)
        elif name not in OPTIONAL:
            raise SystemExit(f"Missing {name} under {source}")
    return sorted(set(found))


def check_installers(source):
    for script in ("scripts/install-grader.ps1", "scripts/install-grader.sh"):
        expected = (source / (script + ".sha256")).read_text().split()[0].lower()
        actual = hashlib.sha256((source / script).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"{script} does not match {script}.sha256; refusing to package it")


def build(source, out, keep=3):
    source, out = Path(source).resolve(), Path(out)
    check_installers(source)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in members(source):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, (source / name).read_bytes())
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    name = f"uet-arena-grader-{digest[:12]}.zip"
    version = re.search(r'^VERSION = "([^"]+)"', (source / "arena-backend/arena/grader.py").read_text(encoding="utf-8"), re.M)
    manifest = {"file": name, "sha256": digest, "bytes": len(data), "grader_version": version[1] if version else None}
    out.mkdir(parents=True, exist_ok=True)
    for target, payload in ((name, data), (name + ".sha256", f"{digest}  {name}\n".encode()),
                            (MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())):
        temporary = out / (target + ".tmp")
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o644)
        os.replace(temporary, out / target)
    # Keep the newest few packages so a member mid-download is not cut off by a reinstall.
    old = sorted((p for p in out.iterdir() if NAME.match(p.name) and p.name != name), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in old[max(0, keep - 1):]:
        path.unlink()
        (out / (path.name + ".sha256")).unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--keep", type=int, default=3)
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    json.dump(build(args.source, args.out, args.keep), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
