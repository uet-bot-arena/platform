# OTTv2 starter · JSON Lines protocol v2

Chọn `cpp17`, `c11` hoặc `python3` trên trang Nộp bot, rồi tải starter đúng ngôn ngữ. ZIP phẳng chứa `bot.cpp` và `json.hpp`, hoặc `bot.c` và `jsmn.h`, hoặc `bot.py` và các module `.py`. Chỉ nộp source, không nộp executable, pip package hay build script.

```bash
g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot
gcc -std=c11 -O2 -Wall -Wextra bot.c -lm -o bot
pypy3 bot.py
```

Python chạy bằng **PyPy 7.3.23 (Python 3.11)**, chỉ thư viện chuẩn. C/C++ và Python có **cùng quỹ giờ**, không nhân hệ số; C/C++ thường tính nhanh hơn Python. Xem [số đo OTTv2](../docs/PYTHON-COMPARISON.md) và [kiểm chứng PyPy](../docs/PYPY-VALIDATION.md).

Bot sống xuyên trận, stdin/stdout JSON Lines: `init → ready`, `observation → action`. Copy nguyên `match_id`, `turn_id`, `state_version`; stdout chỉ JSON + LF và flush; log ra stderr; EOF thì thoát. Server ghép hai bot ở hai container độc lập, không cần dịch chúng về cùng ngôn ngữ.

Đọc `legal_moves`, `board`, `basic_rules` và `timing` từ server. Bàn mặc định 9×9, căn cứ a1/i9; giải có thể dùng bàn 7–15 hàng/cột và luật khác. `board[0]` là hàng mang số lớn nhất. Tọa độ gồm chữ cột và đầy đủ số hàng, ví dụ `j10`, `o15`. `init.payload.basic_rules` và `observation.payload.basic_rules` chứa `{definition, hash}`; `state.basic_rules` chứa trực tiếp definition khi dùng luật tùy chỉnh. Response vẫn `{from,to}` hoặc `{resign:true}`.

Startup có 2 giây riêng; bot 256 MiB, một process/thread, không network; seccomp chặn fork/thread/socket. Bot bị freeze khi chưa tới lượt. Quỹ mặc định 60 hoặc 300 giây/bot, chỉ trừ khi tới lượt; 72 mẫu benchmark dùng chung một quỹ. `timing.budget_ms` là toàn quỹ còn lại, cần chia cho các nước tiếp theo và chừa dư cho JSON/pipe. Trusted worker quyết định deadline; timestamp bot không có quyền xử thắng/thua. Giải tùy chỉnh tự scale quỹ/ply theo diện tích bàn.

Benchmark dự giải bằng `{submission_id, competition_id}` để dùng đúng luật và quỹ đã ghim. Trận luyện tập không tính hạng. Luật mặc định hòa khi lặp thế 3 lần hoặc đạt 512 ply/20 phút. Add-on năng lượng/khiên dùng SDK v3 riêng, mặc định tắt.

Giữ giấy phép MIT của nlohmann/json trong `LICENSE.MIT` và của jsmn trong `jsmn.h` khi phân phối. Chi tiết: [BOT-LANGUAGES.md](../docs/BOT-LANGUAGES.md), [PROTOCOL.md](../docs/PROTOCOL.md), [TIMING.md](../docs/TIMING.md).