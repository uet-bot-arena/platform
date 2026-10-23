# UET Bot Arena — thi đấu bot Oẳn Tù Tì (OTTv2)

Nền tảng cho các đội nộp bot **Python, C hoặc C++** và cho bot đấu với nhau trên bàn cờ Oẳn Tù Tì. Máy chấm từ xa biên dịch và chạy bot trong sandbox native, làm trọng tài từng nước, xếp lịch giải (ladder Elo, vòng tròn hoặc Swiss), tính điểm, phát trực tiếp và lưu replay. Giao diện web và API chạy cùng một server.

Tài liệu: [Cài đặt chi tiết](INSTALL.md) · [Hướng dẫn viết bot](uet-bot-arena/dist/live/bot-guide.md) · [Thể thức giải](arena-backend/docs/COMPETITIONS.md) · [Giao thức bot](arena-backend/docs/PROTOCOL.md) · [Bảo mật](arena-backend/docs/SECURITY.md)

Vận hành Mốc 1: [triển khai](docs/DEPLOY.md), [máy chấm](docs/MAY-CHAM.md), [email](docs/EMAIL.md), [tài khoản và đội](docs/TAI-KHOAN-VA-DOI.md), [nộp bài GitHub](docs/NOP-BAI.md), [GitHub App](docs/GITHUB-APP.md), [13 tình huống chống phá](docs/BAO-MAT.md), [thay đổi](CHANGELOG.md).

## Tính năng

- **Thông báo và hiển thị:** BTC đăng/ẩn thông báo; banner công khai tự cập nhật trong một phút. Giao diện theo hệ điều hành, có nút Sáng/tối lưu lựa chọn. Trang Giới hạn & báo lỗi công khai.
- **Tài khoản cá nhân:** sinh viên/cán bộ tự xác minh email trường, lập đội bốn người bằng mã mời. Đội trưởng quản lý roster và gắn repo private; thành viên cùng push bot, benchmark, luyện tập. BTC nâng cán bộ lên Giám khảo để tạo đội khách không xếp hạng. Người xem không cần đăng nhập.
- **Nộp bot:** GitHub App đọc bot/ tại SHA đã ghim của repo private, tự kiểm 72 mẫu, ghi status cuối và chốt theo lịch sử push GitHub. ZIP chỉ còn để BTC nạp dữ liệu nội bộ.
- **Hàng đợi bền trong SQLite, nhiều worker chạy song song**, ưu tiên trận chính thức → benchmark → luyện tập; mỗi trận chạy trên một CPU riêng.
- **Giải online:** ladder Elo theo giờ Việt Nam; sau hạn 20 đợt chung rồi top64 vòng tròn hai màu tính điểm, chọn top16. Giải ≤64 đội đá vòng tròn luôn; [thể lệ](docs/THE-LE.md).
- **Giải nội bộ:** vòng tròn hai lượt (≤ 32 đội) hoặc Swiss (tới 4096 đội); mỗi cặp đấu 2 ván đổi màu.
- **Luyện tập** giữa các phiên bản của đội hoặc với bot mẫu của BTC (không tính điểm).
- **Luật tùy chỉnh theo giải:** bàn 7–15 hàng/cột, vị trí căn cứ, thắng khi diệt sạch 1 hoặc 2 loại quân, bật/tắt thắng bằng chiếm căn cứ.

## Luật và thể lệ

- Bàn mặc định 9×9, mỗi bên 9 quân (3 Búa, 3 Lá/Bao, 3 Kéo), căn cứ Đỏ a1 / Xanh i9, Đỏ đi trước. Mỗi nước đi 1 ô theo 8 hướng; Búa ăn Kéo, Kéo ăn Lá, Lá ăn Búa; cùng loại chặn nhau.
- Thắng khi chiếm căn cứ đối phương, diệt sạch một loại quân của đối phương, hoặc đối phương bí nước.
- **Quỹ giờ cả trận mỗi bot: ladder 10 giây, Bullet 60 giây hoặc thường 300 giây, không cộng giây**, như nhau cho mọi ngôn ngữ và mọi kích thước bàn. Deadline do đồng hồ monotonic của worker quyết định; hết quỹ là thua. Khoảng chờ 3 giây chỉ để server tuyên bố hết giờ, không nhận nước đi muộn.
- Hòa khi cùng thế cờ lặp lại 3 lần, khi đạt 512 nước (bàn 9×9; bàn khác tăng theo số ô) hoặc trận quá 20 phút.
- **Vòng tròn chính thức online: thắng 1, hòa ½, thua 0, Elo giữ nguyên.** Thể thức nội bộ cũ dùng thắng 3 điểm, hòa 1, thua 0. Swiss: đội được nghỉ (bye) nhận 6 điểm như thắng cả vòng, bằng điểm xét Buchholz. Vòng tròn: bằng điểm thì đồng hạng.
- Bot lỗi (crash, sai giao thức, nước sai luật, hết giờ) bị xử thua; lỗi hạ tầng không tính kết quả và tự chạy lại trong giới hạn K, quá K mới cần BTC xử lý.

## Cài và chạy

Cần **Linux x86_64 hoặc Ubuntu trong WSL2**, Python 3.11+ cho hub. Máy chấm Windows dùng AppContainer không cần WSL; máy chấm Linux dùng bubblewrap/cgroup v2. Xem [cài máy chấm](docs/MAY-CHAM.md).

```bash
git clone https://github.com/khoinguyencode/platform.git
cd platform
bash scripts/setup.sh                       # venv, dependency, cài dependency hub, tạo DB
cd arena-backend && .venv/bin/python -m arena_api bootstrap-admin --username organizer && cd ..
bash scripts/start.sh                       # http://127.0.0.1:4181/#/login
```

Cài và kết nối máy chấm trước khi mở giải; chọn nền tảng giải khớp máy chấm. `GET /api/v1/health/ready` kiểm hub/DB/scheduler. Dữ liệu (DB, source, binary) nằm ở `arena-backend/.platform-state/`; backup khi server đã dừng.

Cấu hình bằng biến môi trường trước khi chạy `scripts/start.sh`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `--slots` (máy chấm) | số CPU − 2, tối thiểu 1 | Số trận/benchmark chạy song song trên máy chấm |
| `grader.max_attempts` | 3 | Giới hạn tự chạy lại khi lỗi hạ tầng |
| `ARENA_CONNECTION_LIMIT` | 1024 | Số kết nối HTTP/SSE đồng thời |
| `ARENA_API_ORIGIN` | `http://127.0.0.1:<port>` | Địa chỉ công khai của web |
| `ARENA_TRUSTED_PROXIES` | (trống) | CIDR của reverse proxy, ví dụ `127.0.0.1/32` |

Khi mở ra mạng: đặt server sau HTTPS reverse proxy theo mẫu `arena-backend/deploy/`, đặt `ARENA_API_ORIGIN=https://…`, `ARENA_API_COOKIE_SECURE=1`, `ARENA_TRUSTED_PROXIES`, và chạy worker trên máy không chứa dữ liệu quan trọng ([SECURITY.md](arena-backend/docs/SECURITY.md)).

## Kiểm thử

```bash
cd arena-backend
make test                 # unit + API
make integration          # bwrap thật: build, benchmark, trận, giải, replay
make experimental-test    # ruleset add-on SDK v3
.venv/bin/python tests/languages_integration.py                          # C / C++ / Python đấu chéo
.venv/bin/python tests/load_lab/e2e_submissions.py --teams 100 --tournament 8   # nhiều đội nộp bài cùng lúc
cd ../uet-bot-arena && npm run check && node --test tests/*.test.mjs     # giao diện
```

Kết quả kiểm thử ghi vào `arena-backend/test-results/` (không đưa lên Git).

## Cấu trúc

```text
arena-backend/arena/       engine luật, giao thức, sandbox native, worker, hàng đợi
arena-backend/arena_api/   FastAPI: tài khoản, giải đấu, Swiss, bot mẫu, SSE
arena-backend/sdk/         bot mẫu C++ / C / Python cho đội
arena-backend/tests/       unit, integration native, load_lab (thử tải nhiều đội)
uet-bot-arena/dist/live/   giao diện web (HTML/CSS/JS thuần, không cần build)
```

Dự án fork từ [Dashlane04/platform](https://github.com/Dashlane04/platform). Thư viện bên thứ ba giữ nguyên giấy phép của chúng (`sdk/LICENSE.MIT`, `sdk/jsmn.h`).
