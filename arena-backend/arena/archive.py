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
        raise Fault("invalid_archive", "ZIP vượt 10 MiB. Chỉ nén mã nguồn cần thiết.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            if not items or len(items) > 64:
                raise Fault("invalid_archive", "ZIP cần 1–64 file. Chỉ giữ mã nguồn và README/giấy phép.")
            total, seen, portable_names = 0, set(), set()
            for item in items:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if item.is_dir():
                    raise Fault("invalid_archive", "ZIP phải phẳng, không có thư mục con. Đặt các file nguồn cùng cấp với file bot chính.")
                if len(path.parts) != 1 or path.name in (".", "..") or "\\" in item.filename or ":" in item.filename or path.is_absolute():
                    raise Fault("invalid_archive", "Đường dẫn ZIP không an toàn. Bỏ đường dẫn tuyệt đối, dấu .., dấu hai chấm và dấu gạch chéo ngược.")
                name = path.name
                # The same source must unpack identically on Linux and Windows graders.
                device = name.split(".", 1)[0].rstrip(" ").casefold()
                reserved = {"con", "prn", "aux", "nul"} | {p + n for p in ("com", "lpt") for n in "123456789¹²³"}
                if (name.casefold() in portable_names or name.rstrip(" .") != name or len(name.encode("utf-8")) > 240
                        or device in reserved or any(ord(c) < 32 or c in '<>"|?*' for c in name)):
                    raise Fault("invalid_archive", "Tên file phải khác nhau và dùng được trên Windows/Linux. Bỏ tên thiết bị, ký tự đặc biệt, dấu chấm hoặc khoảng trắng cuối tên.")
                portable_names.add(name.casefold())
                if runtime == "python3" and name == "__main__.py":
                    raise Fault("invalid_archive", "Hệ thống chạy bot.py; không dùng __main__.py. Giữ main() của bot mẫu trong bot.py.")
                if name in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.flag_bits & 1:
                    raise Fault("invalid_archive", "ZIP có file trùng, liên kết, file đặc biệt hoặc mã hóa. Chỉ nén file nguồn thường, không đặt mật khẩu ZIP.")
                total += item.file_size
                if total > limits.max_source_bytes:
                    raise Fault("invalid_archive", "Mã nguồn giải nén vượt 20 MiB. Bỏ dữ liệu và file sinh không cần thiết.")
                seen.add(name)
            if any(PurePosixPath(name).suffix.lower() in ('.java','.jar','.js','.ts') for name in seen):
                raise Fault('invalid_archive','Hệ thống nhận Python 3, C11, C++17; chưa nhận Java/JavaScript.')
            languages={'bot.py':'Python 3','bot.c':'C11','bot.cpp':'C++17'}
            other=[name for name in seen if name in languages]
            if entry not in seen and len(other)==1:
                raise Fault('invalid_archive',f'ZIP có {other[0]}: hãy chọn Ngôn ngữ {languages[other[0]]}.')
            for item in items:
                if not (item.filename.endswith(extensions) or item.filename in ('README.md','LICENSE.MIT')):
                    raise Fault('invalid_archive',f'File không được phép: {item.filename}. Chỉ giữ source cùng ngôn ngữ và README.md/LICENSE.MIT.')
            if entry not in seen:
                candidates=[i.filename for i in items if i.filename.endswith({'cpp17':'.cpp','c11':'.c','python3':'.py'}[runtime])]
                hint=f'đổi tên {candidates[0]} thành {entry}.' if len(candidates)==1 else f'đặt một file chính tên {entry} ở gốc ZIP.'
                raise Fault('invalid_archive',f'Thiếu {entry}. Hệ thống chạy file tên {entry}; {hint}')
            # Validate everything before writing; read() checks CRC and refuses
            # malformed compression. No ZipFile.extract or user-controlled dirs.
            payloads = {item.filename: archive.read(item) for item in items}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, UnicodeError) as exc:
        raise Fault("invalid_archive", "Không đọc được ZIP. Hãy tạo lại bằng ZIP thường, không mã hóa.") from exc
    target.mkdir(parents=True, mode=0o755)
    hashes = {}
    for name, data in payloads.items():
        (target / name).write_bytes(data)
        (target / name).chmod(0o444)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return {"zip_sha256": hashlib.sha256(raw).hexdigest(), "files": hashes, "uncompressed_bytes": total}
