"""Turn bot/ from a pinned GitHub tarball into the existing flat ZIP ingestion format."""
import gzip
import hashlib
import io
from pathlib import PurePosixPath
import tarfile
import zipfile

from arena.protocol import Fault

MAX_EXPANDED = 24 * 1024 * 1024


def bot_archive(raw, tree=None):
    if len(raw) > 10 * 1024 * 1024:
        raise Fault("invalid_archive", "Repo nén vượt 10 MiB; chỉ giữ mã nguồn cần thiết")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            expanded = stream.read(MAX_EXPANDED + 1)
        if len(expanded) > MAX_EXPANDED:
            raise Fault("invalid_archive", "Repo giải nén vượt 24 MiB")
        files, root, count = {}, None, 0
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for member in archive:
                count += 1
                if count > 4096:
                    raise Fault("invalid_archive", "Repo có quá nhiều file")
                name = member.name.rstrip("/")
                path = PurePosixPath(name)
                if (not name or name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/"))
                        or "\\" in name or ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)):
                    raise Fault("invalid_archive", "Đường dẫn không an toàn trong repo")
                root = root or path.parts[0]
                if path.parts[0] != root:
                    raise Fault("invalid_archive", "Repo thiếu thư mục gốc duy nhất")
                if len(path.parts) < 2 or path.parts[1] != "bot":
                    continue
                if member.isdir() and len(path.parts) == 2:
                    continue
                if len(path.parts) != 3 or not member.isfile() or member.size > 20 * 1024 * 1024:
                    raise Fault("invalid_archive", "bot/ chỉ chứa file nguồn phẳng; không symlink, submodule hay thư mục con")
                if path.name in files:
                    raise Fault("invalid_archive", "File trùng trong bot/")
                data = archive.extractfile(member).read()
                if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise Fault("invalid_archive", "Không nộp con trỏ Git LFS trong bot/")
                files[path.name] = data
        entries = [(name, runtime) for name, runtime in (("bot.cpp", "cpp17"), ("bot.c", "c11"), ("bot.py", "python3")) if name in files]
        if len(entries) != 1 or not 1 <= len(files) <= 64 or sum(map(len, files.values())) > 20 * 1024 * 1024:
            raise Fault("invalid_archive", "bot/ cần đúng một bot.cpp, bot.c hoặc bot.py; tối đa 64 file, 20 MiB")
        if tree is not None:
            # GitHub tarballs omit gitlinks. Check the pinned Git tree as well.
            if tree.get("truncated") or any(x.get("mode") not in ("100644", "100755") or x.get("type") != "blob" for x in tree["tree"]):
                raise Fault("invalid_archive", "bot/ không nhận submodule, symlink hoặc thư mục con")
            if set(files) != {x["path"] for x in tree["tree"]}:
                raise Fault("invalid_archive", "Cây Git và archive không khớp")
        output = io.BytesIO()
        digest = hashlib.sha256()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                digest.update(name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
                # Fixed timestamps make internal idempotency independent of download time.
                item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, data)
        return entries[0][1], output.getvalue(), digest.hexdigest()
    except (OSError, EOFError, tarfile.TarError, ValueError) as exc:
        raise Fault("invalid_archive", "Không đọc được archive GitHub") from exc
