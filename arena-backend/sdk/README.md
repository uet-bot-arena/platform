# OTTv2 C++17 starter · protocol v2

Đọc **Tài liệu & thể lệ → Hướng dẫn viết bot C++17** trên web để xem từng field input/output, mẫu hoàn chỉnh, cách đóng gói, deadline và sửa lỗi. Bản source của hướng dẫn: [BOT-GUIDE](../../uet-bot-arena/dist/live/bot-guide.md). Bot giữ format JSON Lines qua stdin/stdout đang dùng.

ZIP gồm bot.cpp, json.hpp (nlohmann/json 3.11.3), LICENSE.MIT và README.md. Biên dịch local:

```bash
g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot
```

Chỉ nộp source, không nộp executable. Backend build cố định C++17/O2 trong container và cùng image dùng chạy bot. Bot sống xuyên trận, stdin/stdout JSON Lines v2, init→ready rồi observation→action. Copy nguyên match_id, turn_id và state_version. stdout chỉ JSON + LF và flush; log stderr. EOF thì thoát.

Chỉnh choose_move để viết chiến thuật. Bot mẫu ưu tiên chiếm căn cứ, ăn quân rồi tiến về căn cứ; đây không phải bot mạnh hay baseline chuẩn hóa hiệu năng.

Quân rock/paper/scissors, side red/blue, board[row][col] 9×9; row0=hàng9, row8=hàng1. Legal moves có from/to dạng a1..i9. Đỏ trước. Worker cấp quỹ cả trận 60s (Bullet) hoặc 300s (đấu thường) mỗi bot, chỉ trừ khi đến lượt, không increment; các giải cũ giữ cấu hình mỗi lượt, startup 2s, RAM 256MiB, single process/thread. Off-turn bot bị freeze. 3s bù chỉ chờ tuyên bố hết giờ, không cho phép action muộn. Hãy dùng steady_clock nội bộ và chừa dư cho JSON/pipe/scheduler; timestamp của bot không có quyền xử thua. `timing.budget_ms` là toàn quỹ còn lại, không dùng hết cho một nước; nên chia cho các nước tương lai, ví dụ giới hạn 250ms/lượt. Benchmark 72 mẫu chia chung một quỹ.

Web controller gửi thêm `init.payload.match.kind` (`benchmark` hoặc `match`) và `adjudication`; observation cũng có `payload.adjudication`. V2: lặp bàn/bên tới lượt 3 lần hoặc đạt 512 ply/20 phút là hòa; startup lỗi do bot là thua kỹ thuật, hai bot cùng lỗi thì cả hai thua. Không suy ra điều này cho giải cũ v1. Chi tiết: [MATCH-POLICY.md](../docs/MATCH-POLICY.md). Response schema v2 giữ nguyên; bỏ qua input bổ sung nếu không dùng.

Header JSON đi kèm có giấy phép MIT riêng, giữ LICENSE.MIT khi phân phối. API/schema/clock chi tiết ở docs/PROTOCOL.md và docs/TIMING.md của arena-backend. Starter v2 không tương thích wire protocol v1 của demo frontend cũ.
