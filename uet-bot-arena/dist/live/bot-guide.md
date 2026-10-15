# Hướng dẫn viết bot OTTv2 — C++17, protocol v2

Bot là một chương trình C++17 chạy trong worker. Chương trình nhận một dòng JSON qua **stdin**, tính một nước đi, rồi trả một dòng JSON qua **stdout**. Đội nộp source ZIP và dùng trang Test & benchmark để kiểm tra. Mỗi lượt không tạo lại chương trình: cùng một process sống xuyên suốt trận hoặc benchmark.

## 1. Bắt đầu và đóng gói source

1. Đăng nhập tài khoản đội, đổi mật khẩu tạm nếu được yêu cầu.
2. Vào Nộp bot → Tải starter OTTv2. Giải nén ZIP để lấy bot.cpp, json.hpp và giấy phép thư viện JSON.
3. Sửa hàm chọn nước trong bot.cpp. Giữ nguyên phần đọc message và xuất phản hồi cho lần thử đầu tiên.
4. Nén **các file bên trong** thành ZIP; không nén cả thư mục bao ngoài.
5. Nộp với version mới, ví dụ v1.0.0, sau đó v1.0.1. Version đã nộp là bất biến.
6. Chạy benchmark; chỉ khi đạt đủ 72 mẫu mới chọn bot dự giải với đúng quỹ thời gian của giải.

```text
my-bot.zip
├── bot.cpp          bắt buộc, nằm ngay tại gốc ZIP
├── search.hpp       tùy chọn, mã thuật toán của đội
├── json.hpp         có trong starter; worker cũng cung cấp thư viện này
├── README.md        tùy chọn
└── LICENSE.MIT      tùy chọn, giấy phép thư viện JSON
```

ZIP tối đa **10 MiB**, sau giải nén tối đa **20 MiB**, từ 1 đến 64 entry. Tên file phân biệt hoa/thường. Chấp nhận .cpp, .hpp, .h, README.md và LICENSE.MIT. Không dùng thư mục con, symlink, file trùng tên, ZIP mã hóa, binary, Makefile, CMake hoặc script cài thư viện. Chỉ bot.cpp là translation unit được biên dịch; đặt phần phụ trợ trong header và include từ bot.cpp. Các file .cpp khác không được tự động link.

Lệnh build của worker tương đương:

```sh
g++ -std=c++17 -O2 -pipe -Wall -Wextra -I/sdk -I/source /source/bot.cpp -o /out/bot
```

Có thể dùng thư viện chuẩn C++: vector, array, queue, priority_queue, unordered_map, algorithm, bitset, chrono, random… Thư viện ngoài phải tương thích môi trường worker và có source/header hợp lệ trong ZIP; không có bước tải dependency. Backend hiện chạy C++17; Python/Codon chưa được bật.

## 2. Vòng đời và quy tắc stdin/stdout

Luồng giao tiếp là **init → ready → observation → action → observation → action… → đóng process**.

- Mỗi frame là một JSON object UTF-8 kết thúc bằng LF, tức ký tự xuống dòng \n. Đọc bằng getline; xuất bằng dump() rồi \n và flush.
- Mỗi observation chỉ được trả **đúng một action**. Không gửi nước đi trước khi nhận observation, không gửi thêm ready hoặc action dự phòng.
- stdout chỉ dành cho JSON giao thức. In debug, điểm đánh giá hoặc lời chào vào stderr bằng cerr.
- Giới hạn mỗi frame stdout là **65.536 byte, tính cả LF**. Stderr tích lũy tối đa **262.144 byte** cho cả process. Log ít, ngắn và có mục đích.
- Không dùng JSON có key trùng, NaN, Infinity, comment hoặc dữ liệu khác sau object. Không thêm field ngoài schema phản hồi.
- Khi stdin EOF, thoát chương trình. Không đọc bàn cờ từ bàn phím hoặc chờ tương tác của người dùng.
- Sau khi gửi ready/action, worker dừng process bằng SIGSTOP; đến lượt tiếp theo mới SIGCONT. Không chạy nền để suy nghĩ khi đối thủ đi.
- Bot chạy một process, một thread, không có network, fork, tạo thread hoặc gọi process khác. Không đặt thông tin đăng nhập vào source.

Worker có thể thêm các field input như payload.match hoặc payload.adjudication. Đọc các field cần dùng; không copy toàn bộ message đầu vào để làm response.

## 3. Handshake init → ready

Ngay sau khi khởi động, bot nhận init. Ví dụ dưới đây minh họa các field chính; profile thật còn chứa giới hạn tài nguyên.

```json
{"protocol_version":2,"type":"init","match_id":"example-match","payload":{"side":"red","engine":"ottv2-v1","profile":{"match_ms":60000,"turn_ms":60000,"grace_ms":3000},"match":{"kind":"benchmark","adjudication":{"id":"ottv2-match-v2"}}},"timing":{"clock_id":"example-worker","started_mono_ns":"1000000000","deadline_mono_ns":"3000000000","budget_ms":2000}}
```

Lưu match_id rồi trả đúng ba field sau, trong **2 giây**:

```json
{"protocol_version":2,"type":"ready","match_id":"example-match"}
```

Không thực hiện tìm kiếm nước đi hoặc tiền tính toán dài ở handshake. payload.side là màu được cấp; payload.match.kind phân biệt benchmark và match. payload.match.adjudication mô tả policy đã ghim cho phiên chạy. Không hardcode match_id, side hoặc clock_id từ ví dụ. Khi profile có match_ms, turn_ms chỉ giữ để tương thích parser init cũ: worker không cấp lại thời gian mỗi lượt. Đọc timing.budget_ms của observation mới để chia quỹ.

## 4. Đọc observation và trả action

Mỗi lượt nhận một observation. File **ottv2-input.jsonl** tải ở trang tài liệu chứa init và observation mẫu do engine tạo, đủ bàn 81 ô cùng danh sách nước hợp lệ; dùng nó để thử chương trình local. Các timestamp trong file chỉ minh họa định dạng; worker cấp mốc mới khi chạy thật.

| Field đầu vào | Kiểu / ý nghĩa | Bot cần làm |
|---|---|---|
| protocol_version | integer, hiện là 2 | Dùng đúng phiên bản |
| type | observation | Bắt đầu tính nước cho message này |
| match_id | string opaque | Copy nguyên giá trị vào action |
| turn_id | string opaque | Copy nguyên giá trị và kiểu string; không tự tăng |
| state_version | integer không âm | Copy nguyên giá trị và kiểu số |
| payload.state.board | Mảng 9 hàng × 9 cột | Đọc bàn hiện tại; ô trống là null |
| payload.state.to_move | red hoặc blue | Màu đang được đi |
| payload.state.ply | Số nước đã thực hiện | Thông tin trạng thái, không thay turn_id |
| payload.state.result | null hoặc object kết quả | null khi đang chơi |
| payload.legal_moves | Mảng object from/to | Danh sách nước hợp lệ của bên tới lượt |
| payload.counts | Số rock/paper/scissors mỗi màu | Đánh giá nguy cơ mất một loại quân |
| payload.bases | red: a1, blue: i9 | Xác định căn cứ cần bảo vệ/chiếm |
| payload.adjudication | Policy xử lý kết quả | Thông tin luật vận hành đã ghim |
| timing.budget_ms | Ngân sách của lần trao đổi này | Đặt giới hạn tìm kiếm nội bộ có dư |
| timing.clock_id | ID clock worker | Chỉ có ý nghĩa trong worker đó |
| payload.clocks | Object red/blue (ms) | Quỹ còn lại của hai đội, chỉ có ở chế độ đồng hồ cả trận |
| timing.remaining_ms / initial_ms | Số millisecond | Quỹ còn lại / ban đầu của bot; time_control là match, increment_ms bằng 0 |
| timing.started_mono_ns / deadline_mono_ns | Nanosecond dạng string | Thông tin mốc worker; không dùng clock trình duyệt/UTC để quyết định |

Một ô có quân là object như {"side":"red","kind":"rock"}. Các kind là rock (búa), paper (lá/bao), scissors (kéo). Tọa độ from/to là string từ a1 đến i9. **board[0][0] = a9**, board[8][0] = a1; cột 0 là a, cột 8 là i. Chuyển tọa độ: row = 9 - số_hàng, col = ký_tự_cột - 'a'.

Ví dụ observation có match_id = example-match, turn_id = turn-A và state_version = 0; nếu a2 → b3 nằm trong legal_moves, trả:

```json
{"protocol_version":2,"type":"action","match_id":"example-match","turn_id":"turn-A","state_version":0,"payload":{"from":"a2","to":"b3"}}
```

Schema action có **đúng sáu field** trên. payload nước đi chỉ có from và to. Nếu muốn đầu hàng trong trận, payload là {"resign":true}; true phải là boolean, không phải 1 hoặc "true". Benchmark yêu cầu nước đi hợp lệ, nên đầu hàng không được tính đạt mẫu.

Server kiểm tra lại schema, match/turn/version và luật. Nước đi sai không được bỏ qua để xin gửi lại: benchmark dừng ở lỗi; trong trận, bot có thể thua kỹ thuật. Cách bắt đầu an toàn là chấm điểm các nước trong legal_moves, luôn giữ một nước dự phòng hợp lệ.

Benchmark gồm **24 thế cờ × 3 lần**, không phải một ván cờ nối tiếp. Cả 72 mẫu dùng chung quỹ; init/ready và build tính riêng. Hết quỹ trước khi hoàn tất 72 mẫu thì benchmark không đạt. Hai observation liên tiếp có thể là thế cờ hoàn toàn khác; state_version có thể lặp hoặc giảm. Luôn đọc board mới, dùng turn_id từ message mới, không suy ra lượt từ ply hoặc bàn đã lưu ở lượt trước. Cache phải có key theo đầy đủ trạng thái và bên tới lượt.

## 5. Mẫu bot C++ hoàn chỉnh

Mẫu dưới đây giữ vòng giao thức và dùng heuristic: ưu tiên chiếm căn cứ, ăn quân, loại loại quân cuối của đối thủ và tiến gần căn cứ địch. Nó chỉ xét legal_moves, nên bạn có thể thay dần hàm choose_move bằng minimax/alpha-beta mà giữ nguyên phần giao tiếp. Đây là bot khởi đầu để phát triển, không bảo đảm chiến thắng.

```cpp
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <string>
#include "json.hpp"
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

json choose_move(const json& payload, Clock::time_point deadline) {
    const auto& legal = payload.at("legal_moves");
    if (legal.empty()) return json{{"resign", true}};
    const auto& state = payload.at("state");
    const auto& board = state.at("board");
    const std::string side = state.at("to_move");
    const std::string enemy = side == "red" ? "blue" : "red";
    const std::string base = payload.at("bases").at(enemy);
    const int base_row = 9 - (base[1] - '0'), base_col = base[0] - 'a';
    json best = legal.at(0); // Always keep a legal fallback.
    int best_score = -10000000;
    for (const auto& move : legal) {
        if (Clock::now() >= deadline) break;
        const std::string to = move.at("to");
        const int row = 9 - (to[1] - '0'), col = to[0] - 'a';
        int score = -10 * std::max(std::abs(row - base_row), std::abs(col - base_col));
        const auto& target = board.at(row).at(col);
        if (!target.is_null()) {
            score += 500;
            const std::string kind = target.at("kind");
            if (payload.at("counts").at(enemy).at(kind).get<int>() == 1)
                score += 10000;
        }
        if (to == base) score += 1000000;
        if (score > best_score) { best_score = score; best = move; }
    }
    return best;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string line, match;
    while (std::getline(std::cin, line)) {
        const auto received = Clock::now(); // After getline, before JSON parsing.
        try {
            const auto message = json::parse(line);
            if (message.at("protocol_version") != 2) return 2;
            if (message.at("type") == "init") {
                match = message.at("match_id").get<std::string>();
                std::cout << json{{"protocol_version",2},{"type","ready"},
                    {"match_id",match}}.dump() << '\n' << std::flush;
            } else if (message.at("type") == "observation") {
                if (match.empty() || message.at("match_id") != match) return 3;
                const int budget = message.at("timing").at("budget_ms").get<int>();
                const int slice = budget / 80;
                const int reserve = std::max(5, slice / 20);
                // Share the bank across future turns (or independent benchmark cases).
                const int think = std::max(0, std::min(250, slice - reserve));
                const auto deadline = received + std::chrono::milliseconds(think);
                const auto action = choose_move(message.at("payload"), deadline);
                std::cout << json{{"protocol_version",2},{"type","action"},
                    {"match_id",message.at("match_id")},
                    {"turn_id",message.at("turn_id")},
                    {"state_version",message.at("state_version")},
                    {"payload",action}}.dump() << '\n' << std::flush;
            }
        } catch (const std::exception& error) {
            std::cerr << "Cannot process input: " << error.what() << '\n';
            return 1;
        }
    }
    return 0;
}
```

Copy vào bot.cpp trong SDK rồi thử local (Linux/WSL):

```sh
g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot
./bot < ottv2-input.jsonl > output.jsonl 2> debug.log
```

output.jsonl phải có hai dòng: ready, rồi action. match_id phải khớp example-match, turn_id là turn-A, state_version là số 0. Nước đi phải nằm trong legal_moves của file input. Debug chỉ nằm ở debug.log. Thử local kiểu này kiểm tra định dạng và xử lý input; worker benchmark mới kiểm tra sandbox, freeze, ngân sách và các thế cờ của server.

## 6. Quản lý thời gian và tài nguyên

| Giới hạn | Giá trị hiện tại |
|---|---|
| Quỹ thời gian cả trận / mỗi đội | Bullet 60 giây · Đấu thường 300 giây (mặc định); không increment |
| Benchmark | 72 mẫu dùng chung một quỹ 60 hoặc 300 giây |
| init → ready | 2 giây |
| Build | 30 giây, RAM container build 1.024 MiB |
| Bot | RAM container 256 MiB, 1 CPU, 1 process/thread |
| stdout frame | 65.536 byte gồm LF |
| stderr cả process | 262.144 byte |
| Vận hành trận v2 | Lặp thế 3 lần, 512 ply hoặc 20 phút thì hòa |

**Action phải được worker đọc đủ tới LF trước deadline cứng**, không chỉ được bot gọi cout trước deadline. Thời gian serialize input, gửi/nhận pipe và việc OS xếp lịch có ảnh hưởng; parse JSON, chọn nước và serialize output của bot cũng trừ vào quỹ thời gian của bot. Thời gian đối thủ suy nghĩ, engine xử lý và chờ SSE không trừ quỹ. Luôn chừa dư và flush. Khoảng chờ **3 giây không phải thời gian nghĩ thêm**: action muộn bị từ chối; sau đó server mới đại diện đối thủ tuyên bố hết giờ.

Mẫu dùng steady_clock, bắt đầu sau khi getline nhận xong observation và trước khi parse, rồi chia quỹ còn lại cho các nước tiếp theo, giới hạn tìm kiếm 250ms và chừa 5% ngân sách nước đó hoặc tối thiểu 5ms. Không dùng hết timing.budget_ms cho một nước: ở chế độ mới đây là toàn bộ quỹ còn lại. timing.remaining_ms chính xác hơn; budget_ms làm tròn xuống millisecond để tương thích SDK cũ. Đây là mức dự phòng khởi đầu, không phải bảo đảm chống mọi nhiễu host. Không đặt mốc trước getline: thời gian chờ đối thủ và freeze sẽ làm hết ngân sách giả. Không lấy UTC, Date.now hoặc clock trình duyệt để so với deadline worker; các timestamp monotonic dạng string không bảo đảm cùng epoch với steady_clock của thư viện C++.

Nếu dùng iterative deepening, giữ best_move của **độ sâu đã hoàn tất**, kiểm deadline trong các vòng lặp/nhánh tìm kiếm, dừng trước hạn và trả nước dự phòng. Giới hạn kích thước transposition table, tránh recursion quá sâu và cấp phát vô hạn. Đừng sleep để tận dụng sát deadline.

## 7. Hiểu benchmark và đăng ký bot dự giải

Nộp ZIP thành công chỉ xác nhận source đã được lưu và ZIP hợp lệ. Chưa có nghĩa là code đã build được hoặc bot đã đạt chuẩn. Job thường đi qua queued → building → validating → benchmarking → completed; failed/aborted cần xem lỗi.

- **Build:** biên dịch bot.cpp; lỗi compiler nằm trong job/report.
- **Validating:** chạy process, gửi init và chờ ready.
- **Benchmarking:** gửi 72 observation độc lập, kiểm từng action đúng schema, đúng lượt, hợp lệ và kịp giờ. Không có lượt warmup riêng.
- **Completed + passed: true + đủ 72 mẫu:** đạt public smoke suite cho profile đó. Một job completed của trận luyện tập không phải chứng nhận benchmark.
- **Median/P95/max:** wall time của các mẫu đã được nhận; P95 mô tả phần đuôi thời gian trong bộ mẫu này. Khi lỗi giữa chừng, số mẫu và thống kê có thể chỉ là báo cáo một phần.
- **CPU / peak memory:** đo toàn container, bao gồm trusted supervisor; không phải CPU/RSS riêng của bot. Build/startup là phép đo riêng, không cộng vào median lượt.
- **Build cache:** chạy lại cùng artifact có thể dùng binary đã build, nên wall time build trong manifest không phải thời gian biên dịch lại của job mới.

Sau khi đạt, vào Giải đấu, chọn đúng version và benchmark có cùng quỹ thời gian/profile/runtime của giải. Server kiểm source, binary, image, SDK, compiler và provenance; BTC chốt xong thì binary giữ nguyên xuyên giải. Trận luyện tập giúp kiểm chiến thuật hai màu; benchmark public không chứng minh bot xử lý đúng hoặc thắng ở mọi thế cờ.

## 8. Tra lỗi và cách sửa

Giữ **mã job**, **mã lỗi** và chi tiết trong báo cáo khi nhờ BTC hỗ trợ. Không cần gửi mật khẩu. Phân biệt lỗi nộp source, lỗi chương trình bot và lỗi hạ tầng:

| Mã / tình huống | Ý nghĩa | Cách xử lý |
|---|---|---|
| invalid_archive | ZIP sai cấu trúc, thiếu bot.cpp, file bị cấm, ZIP hỏng hoặc vượt giới hạn | Nén các file ở gốc; bỏ binary/thư mục; kiểm 10 MiB ZIP, 20 MiB giải nén, tối đa 64 entry |
| invalid_request khi nộp version | Version không đúng định dạng | Dùng v1.0.0 hoặc v1.0.1, không dùng tên tùy ý |
| version_conflict | Đội đã dùng version đó | Tăng version; source cũ không bị ghi đè |
| unsupported_runtime | Ngôn ngữ chưa được bật | Nộp C++17 theo starter hiện tại |
| quota_exceeded | Đạt quota hoặc queue đầy | Đợi job đang chạy; mỗi đội tối đa 4 job chưa kết thúc, beta tối đa 20 source và 200 job; liên hệ BTC nếu chạm quota tổng |
| build_error | Compiler không tạo được binary hợp lệ | Đọc dòng file:line trong log; kiểm include, cú pháp, hàm main và liên kết; chỉ bot.cpp được compile |
| build_timeout | Build quá 30 giây | Giảm template/độ phức tạp compile; kiểm mã sinh hoặc header quá lớn |
| build_output_limit | Log compiler quá lớn | Sửa nguyên nhân lỗi đầu tiên; tránh lỗi template lặp và warning hàng loạt |
| timeout ở startup | Không trả ready kịp 2 giây | ACK ngay sau init; bỏ tiền tính toán dài; thêm LF và flush |
| timeout trong lượt | Worker chưa đọc đủ action trước deadline | Giảm search, kiểm deadline thường xuyên, chừa dư, thêm LF/flush; không dùng 3 giây chờ như thời gian nghĩ thêm |
| protocol_error | JSON/schema/type sai hoặc thừa phản hồi | stdout chỉ chứa ready/action; đúng protocol_version = 2; không thừa field, debug hoặc response |
| stale_action | match_id, turn_id hoặc state_version sai giá trị/kiểu | Copy trực tiếp từ observation hiện tại; không hardcode hay tự tăng turn_id |
| illegal_action | Nước đi không đúng luật, hoặc đầu hàng trong benchmark | Chọn từ legal_moves; kiểm tọa độ a1–i9 và row 0 = hàng 9 |
| crash | Bot thoát/đóng pipe khi chưa trả xong | Kiểm truy cập mảng, null JSON, exception, stack/RAM; xem stderr và exit code nếu có |
| output_limit | Frame stdout hoặc tổng stderr quá giới hạn | Chỉ xuất một action nhỏ; giảm log stderr |
| worker_unavailable / worker_error | Docker, supervisor hoặc transport gặp sự cố | Gửi mã job cho BTC; không tự coi đây là thua do nghĩ quá giờ |
| artifact_missing / artifact_corrupt / provenance_mismatch | Artifact chốt bị mất, hỏng hoặc không khớp | BTC kiểm tra/khôi phục artifact đã xác minh; đội không tự thay binary sau chốt |
| server_restart / server_shutdown | Controller dừng khi job chưa xong | Kiểm trạng thái; chạy lại benchmark khi server sẵn sàng, trận giải để BTC xử lý attempt |
| qualification_required | Benchmark không đạt hoặc không đúng cấu hình giải | Chạy đủ 72 mẫu với đúng version và ngân sách của giải, rồi chọn lại entry |

Trong trận v2, lỗi bot ở startup có thể bị xử thua kỹ thuật; cả hai lỗi startup thì mỗi đội một trận thua, 0đ. Lỗi bot trong lượt thường xử thua lượt trận đó, không cho sửa action tại chỗ. Lỗi hạ tầng không tự trao chiến thắng cho đối thủ; BTC xem diagnostic/retry. Xem Luật OTTv2 và quy tắc vận hành của giải đang chọn để biết policy đã ghim.

## 9. Checklist trước khi chốt bot

1. ZIP phẳng, bot.cpp ở gốc, version chưa trùng, không có binary/script.
2. ready chỉ ba field, action chỉ sáu field; một JSON object + LF + flush mỗi response.
3. stdout sạch; stderr gọn; EOF thì thoát.
4. Copy đúng match_id/turn_id/state_version, giữ nguyên kiểu dữ liệu.
5. Dùng board mới và legal_moves mới mỗi observation, kể cả benchmark đổi thế không tuần tự.
6. Có fallback hợp lệ, deadline nội bộ và giới hạn RAM/search.
7. Benchmark passed: true, 72 mẫu, đúng quỹ thời gian của giải; xem P95/max và báo cáo một phần nếu lỗi.
8. Đã thử luyện tập với cả Đỏ và Xanh; đọc đúng tọa độ và nhận diện căn cứ.
9. Chọn đúng version/benchmark trước khi BTC chốt; giữ source/version để tái lập kết quả.
