#!/usr/bin/env python3
"""Build the grader install ZIP that the hub serves at /downloads/.

The ZIP holds only grader code, the SDK, the two installer scripts with their
SHA-256 files without config, secrets or tokens. It is deterministic
(sorted names, fixed timestamps), so the same source always gives the same
SHA-256 and file name. Standard library only; install-hub.sh runs it as root.

The Linux grader also needs the read-only toolchain rootfs-<sha256>.tar.zst
(scripts/build-rootfs.sh). --rootfs publishes it next to the ZIP after checking
that the file's SHA-256 matches its name; later runs without --rootfs keep
publishing the verified rootfs already there.

    python3 scripts/build-grader-zip.py --source . --out /opt/uet-arena/downloads [--rootfs FILE]
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

TREES = ("arena-backend/arena", "arena-backend/sdk")
FILES = (".gitattributes",
         "scripts/install-grader.ps1", "scripts/install-grader.ps1.sha256",
         "scripts/install-grader.sh", "scripts/install-grader.sh.sha256")
OPTIONAL = {".gitattributes"}
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
NAME = re.compile(r"^uet-arena-grader-[0-9a-f]{12}\.zip$")
ROOTFS = re.compile(r"^rootfs-([0-9a-f]{64})\.tar\.zst$")
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


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(target, payload=None, copy_from=None):
    temporary = target.with_name(target.name + ".tmp")
    if copy_from is None:
        temporary.write_bytes(payload)
    else:
        shutil.copyfile(copy_from, temporary)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)


def publish_rootfs(out, rootfs=None, keep=2):
    """Publish a new rootfs, or keep the newest verified one already in out; return its manifest entry."""
    if rootfs is not None:
        rootfs = Path(rootfs)
        match = ROOTFS.match(rootfs.name)
        if not match or not rootfs.is_file():
            raise SystemExit(f"{rootfs}: expected an existing rootfs-<sha256>.tar.zst file")
        if file_sha256(rootfs) != match[1]:
            raise SystemExit(f"{rootfs.name}: SHA-256 does not match the file name; refusing to publish it")
        target = out / rootfs.name
        if not (target.is_file() and target.stat().st_size == rootfs.stat().st_size and file_sha256(target) == match[1]):
            write_atomic(target, copy_from=rootfs)
        os.utime(target)  # newest wins below
    found = sorted((p for p in out.iterdir() if ROOTFS.match(p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
    chosen = next((p for p in found if file_sha256(p) == ROOTFS.match(p.name)[1]), None)
    if chosen is None:
        return None
    digest = ROOTFS.match(chosen.name)[1]
    write_atomic(out / (chosen.name + ".sha256"), f"{digest}  {chosen.name}\n".encode())
    # Keep the previous rootfs as well, so a member mid-download is not cut off by a reinstall.
    for path in [p for p in found if p != chosen][max(0, keep - 1):]:
        path.unlink()
        (out / (path.name + ".sha256")).unlink(missing_ok=True)
    return {"file": chosen.name, "sha256": digest, "bytes": chosen.stat().st_size}


def build(source, out, keep=3, rootfs=None):
    source, out = Path(source).resolve(), Path(out)
    check_installers(source)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in members(source):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.create_system = 3  # Unix on every builder, so Windows and Linux give the same bytes
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, (source / name).read_bytes())
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    name = f"uet-arena-grader-{digest[:12]}.zip"
    version = re.search(r'^VERSION = "([^"]+)"', (source / "arena-backend/arena/grader.py").read_text(encoding="utf-8"), re.M)
    manifest = {"file": name, "sha256": digest, "bytes": len(data), "grader_version": version[1] if version else None}
    out.mkdir(parents=True, exist_ok=True)
    linux = publish_rootfs(out, rootfs)
    if linux:
        manifest["linux_rootfs"] = linux
    for target, payload in ((name, data), (name + ".sha256", f"{digest}  {name}\n".encode()),
                            (MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())):
        write_atomic(out / target, payload)
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
    parser.add_argument("--rootfs", type=Path, help="rootfs-<sha256>.tar.zst for Linux graders; checked, then published")
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    json.dump(build(args.source, args.out, args.keep, args.rootfs), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
