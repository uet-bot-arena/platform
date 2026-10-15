import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import zipfile

from arena.protocol import Fault


def unpack(raw, target, limits, runtime="cpp17"):
    extensions, entry = {"cpp17": ((".cpp", ".hpp", ".h"), "bot.cpp"),
                         "c11": ((".c", ".h"), "bot.c"), "python3": ((".py",), "bot.py")}[runtime]
    if len(raw) > limits.max_zip_bytes:
        raise Fault("invalid_archive", "ZIP exceeds 10 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            if not items or len(items) > 64:
                raise Fault("invalid_archive", "Expected 1..64 ZIP entries")
            total, seen = 0, set()
            for item in items:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if item.is_dir():
                    raise Fault("invalid_archive", "Flat ZIP required; no directories")
                if len(path.parts) != 1 or path.name in (".", "..") or "\\" in item.filename or ":" in item.filename or path.is_absolute():
                    raise Fault("invalid_archive", "Unsafe ZIP path")
                name = path.name
                if runtime == "python3" and name == "__main__.py":
                    raise Fault("invalid_archive", "bot.py is the entry point; __main__.py is reserved")
                if name in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.flag_bits & 1:
                    raise Fault("invalid_archive", "Duplicate, special or encrypted entry")
                if not (name.endswith(extensions) or name in ("README.md", "LICENSE.MIT")):
                    raise Fault("invalid_archive", "Only source files for the selected language and README/LICENSE allowed")
                total += item.file_size
                if total > limits.max_source_bytes:
                    raise Fault("invalid_archive", "Uncompressed source exceeds 20 MiB")
                seen.add(name)
            if entry not in seen:
                raise Fault("invalid_archive", "Missing " + entry + " at ZIP root")
            # Validate everything before writing; read() checks CRC and refuses
            # malformed compression. No ZipFile.extract or user-controlled dirs.
            payloads = {item.filename: archive.read(item) for item in items}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise Fault("invalid_archive", str(exc)) from exc
    target.mkdir(parents=True, mode=0o755)
    hashes = {}
    for name, data in payloads.items():
        (target / name).write_bytes(data)
        (target / name).chmod(0o444)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return {"zip_sha256": hashlib.sha256(raw).hexdigest(), "files": hashes, "uncompressed_bytes": total}
