# OTTv2 Alpha–Beta starter (C++17, protocol v2)

Nộp ZIP phẳng chứa `bot.cpp`, `json.hpp`, `LICENSE.MIT`, `README.md`.
Đặt version mới, ví dụ `v2.0.0`, rồi chạy benchmark. Không nộp binary.

## Thuật toán

* Negamax (minimax cho trò chơi tổng bằng 0) với alpha–beta pruning.
* Iterative deepening từ độ sâu 1 đến 8 ply. Chỉ dùng kết quả của vòng tìm kiếm
  đã hoàn tất; khi hết ngân sách quay về nước tốt nhất của vòng trước.
* Transposition table 262.144 ô, Zobrist hash 64 bit, lưu độ sâu, nước tốt nhất
  và cận điểm. Điểm thắng được chuẩn hóa theo ply để tái sử dụng giữa các lượt.
* Xếp nước: nước thắng ngay, ăn quân cuối của một loại, ăn quân, tiến về căn cứ;
  ưu tiên nước tìm được ở vòng trước hoặc trong transposition table.
* Quiescence tối đa 2 ply khi chạm chân trời: xét ăn quân/chiếm căn cứ. Nếu có
  nguy cơ đối thủ thắng ngay bằng căn cứ hoặc diệt loại, xét mọi nước phòng thủ.
* Luật mô phỏng: 8 hướng, 1 ô, không ăn cùng loại, đúng quan hệ khắc chế;
  thắng khi chiếm căn cứ, diệt một loại hoặc khiến đối thủ bí nước.

## Hàm đánh giá theo OTTv2

Không chỉ cộng số quân. Mất quân thứ hai của một loại bị phạt mạnh hơn mất quân
thứ nhất; quân cuối cùng đang bị đe dọa bị phạt rất cao. Tính thêm khoảng cách
Chebyshev tới căn cứ đối phương, độ linh hoạt, trung tâm và quân bọc lót có thể
ăn lại kẻ săn quân mình. Bọc lót không giảm phạt đối với quân cuối của một loại:
ván đã kết thúc trước khi có cơ hội ăn lại.

Đây là heuristic tự thiết kế, chưa được huấn luyện hoặc tinh chỉnh qua giải đấu.
Tìm kiếm có giới hạn độ sâu/thời gian nên không đảm bảo nước tối ưu toàn cục.
Không cắt nhánh theo beam và không tự coi lặp thế là hòa (luật không có điều đó).

## Thời gian và bộ nhớ

`THINK_MS = 250` ở đầu `bot.cpp` là ngân sách mặc định. Ngân sách thực tế là
`max(0, min(250, budget_ms * 3 / 5 - 20))` millisecond, tính bằng `steady_clock`
từ trước khi parse observation. Kiểm tra clock mỗi 128 node. Việc kiểm tra theo
đợt và serialize/flush có overhead; deadline trên worker luôn có quyền quyết định.
Ngân sách 250 ms giúp benchmark 72 mẫu hoàn thành nhanh trên máy local; muốn
tìm sâu hơn có thể tăng lên 1000 rồi nộp version mới và đo lại P95/max.

Một luồng, một process sống xuyên trận; không thread/fork/network. Transposition
table khoảng 8 MiB trên ABI phổ biến, cùng các cấu trúc nhỏ khác, dưới giới hạn
256 MiB của worker. stdout chỉ JSON Lines; thông tin depth/nodes/score ghi stderr.
Ở root luôn chọn từ `legal_moves` server cung cấp. Khi không có nước thì resign.
Không phụ thuộc turn_id tăng dần hoặc state_version duy nhất, phù hợp benchmark.

## Đọc code

`Position::moves/play`: mô phỏng luật; `evaluate`: heuristic; `Search::negamax`:
alpha–beta + cache; `quiet`: mở rộng chiến thuật; `choose`: iterative deepening;
`main`: handshake, observation và action protocol v2.

Build local: `g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot`.
Header nlohmann/json đi kèm dùng MIT; giữ `LICENSE.MIT` khi phân phối.
