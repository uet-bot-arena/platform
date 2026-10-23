"""Artifact validation and build-cache helpers shared by native sandbox backends."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from arena.protocol import Fault

ROOT = Path(__file__).resolve().parents[2]


def sdk_hash():
    return hashlib.sha256(b"".join((ROOT/"sdk"/name).read_bytes() for name in ("json.hpp","jsmn.h"))).hexdigest()


def seal(report, output, runtime, stage=None, built=None, signature=None):
    """Verify the compiler output and replace it with a controller-owned, read-only copy."""
    if report["ok"]:
        path = output / ("translation.ii" if stage == "preprocess" else "bot")
        built = built or path
        signature = signature or (b"PK\x03\x04" if runtime in ("python3", "pypy3", "cpython3") else b"\x7fELF")
        if built.is_symlink() or not built.is_file() or built.stat().st_size > 16 * 1024 * 1024 or stage != "preprocess" and built.read_bytes()[:len(signature)] != signature:
            raise Fault("worker_error", "Invalid built artifact")
        # Compiler output has uid 10001 on native Linux. Make a verified
        # controller-owned copy before sealing, without CAP_CHOWN.
        binary = built.read_bytes()
        verified = output / ("verified-" + uuid.uuid4().hex)
        with verified.open("xb") as stream:
            stream.write(binary)
        verified.chmod(0o444 if stage == "preprocess" else 0o555)
        verified.replace(path)
        report["artifact_sha256"] = hashlib.sha256(binary).hexdigest()
        if stage != "preprocess":
            (output / "runtime.json").write_text(json.dumps({"runtime": runtime}))
            (output / "runtime.json").chmod(0o444)
    return report


def cached_cpp_build(source, output, profile, backend, cache_root, lock_for, compile_slots):
    """Cache exact GCC preprocessed bytes, including line/time macros and PCH.

    Only this submission enters each sandbox. No shared competitor cache is
    mounted. Uploaded files never run on the controller or choose compiler args.
    """
    started = time.monotonic()
    cache_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="preprocess-",dir=cache_root) as tmp:
        translated = Path(tmp)/"translated"
        prepared = backend.build(source,translated,profile,stage="preprocess")
        if not prepared["ok"]:
            return prepared
        key = hashlib.sha256((backend.identity()['runtime_id']+prepared["artifact_sha256"]).encode()).hexdigest()
        cached = cache_root/key
        with lock_for("translation:"+key):
            hit = (cached/"manifest.json").is_file()
            if hit:
                result = json.loads((cached/"manifest.json").read_text())
                if not (cached/"bot").is_file() or hashlib.sha256((cached/"bot").read_bytes()).hexdigest() != result["artifact_sha256"]:
                    raise Fault("artifact_corrupt","Shared compiled artifact hash mismatch")
            else:
                if cached.exists():
                    cached.rename(cached.with_name(key+"-interrupted-"+str(time.time_ns())))
                with compile_slots:
                    result = backend.build(translated,cached,profile,stage="preprocessed")
                if not result["ok"]:
                    return result
                (cached/"manifest.json").write_text(json.dumps(result))
            output.mkdir(mode=0o755)
            for name in ("bot","runtime.json"):
                shutil.copyfile(cached/name,output/name)
                (output/name).chmod(0o555 if name=="bot" else 0o444)
            return {**result,"cache_hit":hit,"compiler_wall_ms":result["wall_ms"],"wall_ms":(time.monotonic()-started)*1000,
                    "translation_sha256":prepared["artifact_sha256"]}


def remove_tree(path):
    """rmtree that also removes read-only files (sources are 0444; Windows refuses those)."""
    import stat

    def retry(function, target, _):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            function(target)
        except OSError:
            pass
    shutil.rmtree(path, onerror=retry) if Path(path).exists() else None
