# OTTv2 Experimental · SDK v3 · Minimax / Alpha–Beta

Nộp ZIP này nguyên vẹn ở **Nộp bot**, chọn **SDK Experimental (v3)** và một
version mới (ví dụ `v3.1.0`). Trong **Benchmark**, chọn đúng ruleset đã publish
của giải. Kết quả benchmark classic hoặc ruleset khác không đủ để đăng ký giải.
Bot này dành cho experimental; không có fallback protocol v2.

ZIP phẳng chứa `bot.cpp`, `experimental.hpp`, `json.hpp`, `LICENSE.MIT`,
`README.md`; không có executable hay thư mục lồng. Giữ giấy phép MIT của
nlohmann/json khi phân phối. Header SDK đi kèm là bản rules-v1 hiện tại.

## Thuật toán

- Minimax: lượt mình lấy điểm lớn nhất, lượt đối phương lấy điểm nhỏ nhất.
- Alpha–beta pruning bỏ các nhánh đã chứng minh không cải thiện kết quả.
- Iterative deepening từ 1 đến 6 ply. Hết thời gian/node thì dùng vòng đã hoàn
  tất; nếu chưa hoàn tất depth 1, dùng action hợp lệ đầu tiên từ server.
- Sắp nước theo chiến thắng căn cứ/diệt loại, ăn quân, chiếm vùng và kết quả
  vòng trước. Không dùng beam search và không giới hạn chỉ một số nước.
- Mỗi nhánh dùng `Rules::legal_actions` và `Rules::apply` của SDK, nên có đúng
  custom movement/filter, năng lượng, cooldown, Khiên và mục tiêu thắng.
- Nhận biết lặp thế ba lần khi server bật chính sách đó; benchmark không có
  lịch sử trận. Không có transposition table hoặc quiescence trong mẫu này.

## Đánh giá

Điểm terminal luôn lớn hơn heuristic: thắng/thua ±1.000.000, ưu tiên thắng sớm
và trì hoãn thua. Heuristic gồm quân còn lại, rủi ro chỉ còn một quân của một
loại (chỉ khi bật thắng diệt loại), năng lượng, Khiên, control/target, khoảng
cách tới vùng và đe dọa ăn quân theo các nước hợp lệ của ruleset. Khoảng cách
Chebyshev chỉ là ước lượng vị trí, không phải độ dài đường đi chính xác cho
custom movement. Điểm đánh giá chưa được tinh chỉnh qua giải đấu.

## Giao thức và thời gian

JSON Lines qua stdin/stdout: `init → ready`, `observation → action`.
Ready xác nhận `ruleset_hash` và capability `rules-v1`; action dùng `action_id`
đúng danh sách server, copy nguyên `match_id`, `turn_id`, `state_version`.
stdout chỉ JSON + LF + flush. Log `depth/nodes/score` ghi stderr.

`THINK_MS = 250`, `MAX_DEPTH = 6`, `NODE_LIMIT = 20000` ở đầu `bot.cpp`.
Clock nội bộ là `steady_clock`, bắt đầu trước parse mỗi observation. Ngân sách
thực tế là `max(0, min(250, timing.budget_ms / 80 - 5))` ms, đọc lại quỹ còn lại ở
mỗi observation. Kiểm tra clock trước mỗi node/action và sau evaluate. Một lần gọi SDK
không bị ngắt giữa chừng nên thời gian có thể vượt nhẹ 250 ms; worker vẫn quyết
định deadline cứng. Server mới có quỹ 1 phút hoặc 5 phút mỗi bot/cả trận; search chia theo timing.budget_ms còn lại, giới hạn 250ms. Startup 2 giây,
RAM 256 MiB, 1 CPU, một process/thread. Không chạy thread, fork hay network.

Muốn tìm sâu hơn, thử tăng THINK_MS lên 1000 rồi nộp version mới và xem P95/max
của benchmark. Mẫu dùng simulator JSON để dễ đọc nên độ sâu thực tế phụ thuộc
số nước và cấu hình máy; không đảm bảo luôn đạt 6 ply hoặc là bot mạnh nhất.
Giới hạn 512 ply được mô phỏng là hòa; giới hạn thời gian toàn trận do server
xử lý. Lịch sử được đặt lại khi gặp observation không liên tiếp.

## Build và đọc code

```bash
g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot
```

`Search::minimax`: thuật toán; `evaluate`: chiến thuật; `ordering`: xếp nước;
`choose`: tăng dần độ sâu; `main`: protocol v3. Không cần HTTP API cho bot.

Đây là bot tham khảo để test submission/benchmark/luyện tập. Tìm kiếm hữu hạn
và heuristic không bảo đảm nước tối ưu toàn cục hoặc luật custom cân bằng.
