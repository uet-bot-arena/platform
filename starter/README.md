# Bot OTTv2 của đội

Repo này chứa bot mẫu C++17. Chỉ thư mục **`bot/`** được gửi đi chấm; README, examples, tools và workflow không nằm trong bài nộp.

## Bắt đầu

1. Trên repo mẫu của BTC, chọn **Use this template → Create a new repository**.
2. Chọn tài khoản của đội trưởng, tên **`ott2026-<mã đội 6 ký tự>`**, đặt **Private**. Mã lấy từ trang đội trên UET Bot Arena.
3. Mời ba bạn vào repo qua Settings → Collaborators.
4. Cài [GitHub App UET Bot Arena](https://github.com/apps/uet-bot-arena/installations/new), chọn **Only select repositories**, chọn đúng repo private của đội.
5. Đội trưởng dán link repo vào trang đội. Sửa code trong `bot/`, commit và push nhánh `main`.

BTC sẽ thông báo khi mở kết nối repo để nhận bài. Trong lúc chờ, bạn có thể viết và kiểm bot trên máy mình.

## Chạy trên máy mình

Cần Python 3.11+ và GCC (`g++` cho C++17, `gcc` cho C11); Python bot chỉ cần thư viện chuẩn. Không cần cài thư viện pip. Tại thư mục repo:

```text
python run_local.py --check
python run_local.py
```

Trên Linux dùng `python3` nếu máy không có lệnh `python`. `--check` kiểm build, bắt tay và hai lượt đi. Lệnh không có `--check` chơi một ván với bot mẫu Python, tối đa 512 lượt, mỗi bên 60 giây; in JSON kết quả và số lượt. Có thể chỉ định compiler bằng `--cxx <đường dẫn g++.exe>` / `--cc <đường dẫn gcc.exe>`. Log lỗi bot ở `.local-build/red.stderr.log` và `blue.stderr.log`.

Đây là chạy/debug code của chính bạn trên máy mình. Kết quả local không thay lượt kiểm tra và sandbox Windows/Linux của giải; giới hạn RAM và toolchain chính thức nằm ở hub. Không commit `.local-build/`.

## Chọn C hoặc Python

Có đúng **một** file chính trong `bot/`: `bot.cpp`, `bot.c` hoặc `bot.py`. File phụ cùng ngôn ngữ và header nằm cùng thư mục; không đặt thư mục con. Không nộp binary, symlink, submodule hoặc file con trỏ Git LFS.

- C++17 mặc định: `bot/bot.cpp` và `bot/json.hpp`; sửa hàm `choose_move`.
- C11: bỏ hai file C++ khỏi `bot/`, chép `examples/bot.c` và `examples/jsmn.h` vào `bot/`; sửa hàm `choose_move`.
- Python: bỏ các file C/C++ khỏi `bot/`, chép `examples/bot.py` vào `bot/`; sửa hàm `choose`.

Có thể thử mẫu mà chưa thay bài của đội:

```text
python run_local.py --bot examples/bot.c --check
python run_local.py --bot examples/bot.py
```

Bot là process chạy suốt ván, đọc mỗi dòng JSON trên stdin và trả đúng một dòng JSON trên stdout. In debug ra stderr, nhớ flush stdout. Giữ nguyên phần xử lý `init`, match_id, turn_id, state_version trong mẫu; chỉ sửa hàm chọn nước. `tools/arena/` là bản engine/giao thức từ hub để debug luật cơ bản, không sửa nó để làm bot được điểm.

## Lịch sử và kiểm tra tự động

GitHub Actions kiểm build/giao thức sau push/PR bằng `python3 run_local.py --check`. Phút chạy tính vào tài khoản chứa repo. Đây là kiểm nhanh của repo, không thay dấu kết quả kiểm 72 mẫu do hub gắn sau này.

```text
git log --oneline
git checkout <sha>
python run_local.py
git checkout main
```

Quay lại commit cũ ở local giúp xem và thử lại bài. Chỉ push mới lên `main` là yêu cầu nộp mới; không dùng ngày commit tự đặt để chứng minh đã nộp trước hạn. Quy tắc chốt theo lịch sử push của GitHub và bản Đạt trước hạn sẽ được công bố trong hướng dẫn nộp bài của giải.

`json.hpp` giữ giấy phép Niels Lohmann ở [JSON-LICENSE.MIT](JSON-LICENSE.MIT); `jsmn.h` giữ thông báo MIT của tác giả ngay trong file.
