"""Windows grader toolchain: WinLibs GCC + PyPy + json.hpp PCH, pinned by windows-toolchain.json.

    python -m arena.sandbox.toolchain install     # download (or reuse cache), verify SHA-256, extract, build PCH
    python -m arena.sandbox.toolchain path        # print the installed directory
    python -m arena.sandbox.toolchain id          # print runtime_id (SHA-256 of the manifest)

runtime_id is the SHA-256 of the manifest file. Every archive is checked against its pinned
SHA-256 before extraction; the PCH is rebuilt locally from the pinned json.hpp.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

MANIFEST = Path(__file__).with_name("windows-toolchain.json")
ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def runtime_id():
    # Normalise line endings so a CRLF checkout yields the same identity.
    return "sha256:" + hashlib.sha256(MANIFEST.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def base_dir():
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(local) / "uet-arena"


def install_dir(root=None):
    return Path(root or base_dir() / "toolchain") / runtime_id()[7:23]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def locate(root=None):
    """Installed toolchain directory, or None when missing or for another manifest."""
    folder = install_dir(root)
    marker = folder / "installed.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("runtime_id") == runtime_id():
            return folder
    except (OSError, ValueError):
        pass
    return None


def fetch(archive, downloads, log):
    target = Path(downloads) / archive["file"]
    if target.is_file() and target.stat().st_size == archive["size"] and sha256(target) == archive["sha256"]:
        log(f"dùng bản đã tải {target.name}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    log(f"tải {archive['url']}")
    with urllib.request.urlopen(archive["url"], timeout=60) as response, open(partial, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    if sha256(partial) != archive["sha256"]:
        partial.unlink()
        raise SystemExit(f"SHA-256 sai cho {archive['file']}; đã xóa file tải về")
    partial.replace(target)
    return target


def extract(archive, path, target, log):
    prefix = archive["strip"].rstrip("/") + "/"
    excluded = tuple(archive.get("exclude", []))
    count = 0
    with zipfile.ZipFile(path) as bundle:
        for item in bundle.infolist():
            name = item.filename
            if not name.startswith(prefix):
                raise SystemExit(f"{archive['file']} có đường dẫn ngoài {prefix}: {name}")
            relative = name[len(prefix):]
            if not relative or relative.startswith(excluded) or item.is_dir():
                continue
            destination = (target / relative).resolve()
            if target.resolve() not in destination.parents:
                raise SystemExit(f"Đường dẫn không an toàn trong {archive['file']}: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            count += 1
    log(f"giải nén {archive['name']}: {count} file")


def build_pch(folder, spec, log):
    header = ROOT / spec["header"]
    if sha256(header) != spec["header_sha256"]:
        raise SystemExit("sdk/json.hpp khác bản đã ghim trong manifest")
    pch = folder / "pch"
    pch.mkdir()
    gpp = folder / "gcc" / "bin" / "g++.exe"
    env = {"PATH": str(folder / "gcc" / "bin"), "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
           "TEMP": str(pch), "TMP": str(pch)}
    shutil.copyfile(header, pch / "json.hpp")
    (pch / "json.cpp").write_text('#include "json.hpp"\ntemplate class nlohmann::basic_json<>;\n', encoding="utf-8")
    # Same steps as build-rootfs.sh: one explicit instantiation object + an extern-declared header PCH.
    subprocess.run([str(gpp), *spec["flags"], "-c", "json.cpp", "-o", "json.o"], cwd=pch, env=env, check=True)
    with open(pch / "json.hpp", "a", encoding="utf-8", newline="\n") as stream:
        stream.write("\nextern template class nlohmann::basic_json<>;\n")
    subprocess.run([str(gpp), *spec["flags"], "-x", "c++-header", "json.hpp", "-o", "json.hpp.gch"], cwd=pch, env=env, check=True)
    (pch / "json.cpp").unlink()
    log("dựng PCH json.hpp")


def install(root=None, downloads=None, log=print):
    if os.name != "nt":
        raise SystemExit("Toolchain Windows chỉ cài trên Windows")
    found = locate(root)
    if found:
        log(f"toolchain đã có: {found}")
        return found
    data = manifest()
    downloads = Path(downloads or base_dir() / "downloads")
    final = install_dir(root)
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=final.parent))
    started = time.monotonic()
    try:
        # Grant before extracting: every extracted file inherits read+execute for all AppContainers.
        from arena.winbox import ALL_APPLICATION_PACKAGES, grant
        grant(staging, ALL_APPLICATION_PACKAGES, "RX", recursive=False)
        for archive in data["archives"]:
            extract(archive, fetch(archive, downloads, log), staging / archive["name"], log)
        build_pch(staging, data["pch"], log)
        (staging / "installed.json").write_text(json.dumps({"runtime_id": runtime_id(), "installed_unix": int(time.time()),
                                                            "manifest": data}, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log(f"toolchain sẵn sàng: {final} ({time.monotonic() - started:.0f} s)")
    return final


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    action = argv[1] if len(argv) > 1 else "install"
    if action == "install":
        install()
    elif action == "path":
        folder = locate()
        if not folder:
            raise SystemExit("Chưa cài toolchain: python -m arena.sandbox.toolchain install")
        print(folder)
    elif action == "id":
        print(runtime_id())
    else:
        raise SystemExit("Dùng: install | path | id")


if __name__ == "__main__":
    main(sys.argv)
