# UET Bot Arena · platform

Nền tảng thi đấu bot OTTv2 trên bàn cờ 9×9. Backend biên dịch source C++17 trong Docker, chạy benchmark, điều phối giải vòng tròn hai lượt và phục vụ UI cùng origin. Live/replay cập nhật bàn cờ bằng SSE; các trang danh sách không tự reload định kỳ.

**[Hướng dẫn cài đặt đầy đủ → INSTALL.md](INSTALL.md)** · **[Hướng dẫn viết bot](uet-bot-arena/dist/live/bot-guide.md)** · **[Ruleset add-on](arena-backend/docs/RULESET-ADDONS.md)**

## Tính năng hiện có

- Đăng ký đội; BTC duyệt/cấp tài khoản, đổi/reset mật khẩu, khóa/khôi phục và nhật ký thao tác.
- Nộp ZIP source C++17, verify archive, biên dịch trong worker cô lập và lưu phiên bản bất biến.
- Benchmark 72 mẫu; báo thời gian, CPU, bộ nhớ, startup/build và lỗi protocol dễ tra cứu.
- Luyện tập, giải hai lượt đổi màu, chốt binary đã benchmark, live/replay, tự chuyển trận và khách xem giải công khai.
- Thắng 3 điểm, hòa 1 điểm, thua 0 điểm; bằng điểm đồng hạng. Biểu đồ chung cho top 10.
- Quỹ cả trận mỗi bot: Bullet 60 giây hoặc thường 300 giây, không increment. Deadline do worker monotonic quyết định; khoảng chờ 3 giây không nhận nước đi muộn.
- **Ruleset add-on (Experimental):** ghép năng lượng/vùng/mẫu đi/Khiên/mục tiêu, nhập/xuất JSON, kiểm tra & công bố, changelog và playground legal_actions cho đội. SDK v3; mỗi giải giữ một snapshot luật cố định.

## Chạy nhanh

Yêu cầu **Linux x86_64 hoặc Ubuntu trong WSL2**, Python 3.11+, Docker chạy Linux containers với cgroup v2. Windows dùng Docker Desktop và bật WSL integration cho Ubuntu. Node.js chỉ cần cho kiểm thử UI/prototype, không cần để chạy web chính.

```bash
git clone https://github.com/Dashlane04/platform.git
cd platform
bash scripts/setup.sh
# Chọn mật khẩu của riêng bạn tại prompt ẩn; không có tài khoản/mật khẩu mặc định.
cd arena-backend
.venv/bin/python -m arena_api bootstrap-admin --username organizer
cd ..
bash scripts/start.sh
```

Mở **http://127.0.0.1:41810/#/login**. Đăng nhập BTC bằng username/mật khẩu vừa tạo. `GET /api/v1/health/ready` phải trả `ready: true` trước khi nộp/chạy bot. Xem [INSTALL.md](INSTALL.md) cho cài dependency, test hai đội, đổi cổng, lỗi thường gặp và cách dừng/chạy lại.

Không cần `npm install`, không cần frontend server riêng. `uet-bot-arena/dist/live/` là source HTML/CSS/ES module được viết trực tiếp và cần được giữ trong Git. `npm run dev` mở prototype cũ, không phải UI/API thật.

## Cấu trúc repo

```text
arena-backend/
  arena/          Engine, JSON Lines protocol, Docker supervisor, queue và benchmark
  arena_api/      FastAPI, tài khoản, migrations, giải đấu và catalog add-on
  sdk/            Bot C++ mẫu, JSON header, SDK v3 simulator và license của thư viện
  examples/       Alpha-beta, minimax SDK v3, client và gói ruleset JSON
  tests/          Unit/API, Docker integration, kiểm thử tải và sự cố
  docs/           Luật, protocol, timing, quản trị và vận hành
uet-bot-arena/
  dist/live/      UI chính nối API thật
  dist/           Prototype UI cũ và asset/source starter tham chiếu
  tests/          Kiểm thử UI và các fixture độc lập
experiments/
  codon-bot/      Nghiên cứu Python/Codon, tách khỏi runtime thi đấu
scripts/          Setup và startup cho checkout mới
```

## Kiểm thử

```bash
cd arena-backend
make test
make integration          # Build image và kiểm thử Docker/API/giải thật bằng dữ liệu tạm
make experimental-test   # SDK v3, add-on, simulator parity và giải add-on
cd ../uet-bot-arena
npm run check
node --test tests/*.test.mjs
```

Kiểm thử tải/sự cố: `make load-test`, `make guest-load-test`, `make experimental-load-test`; đọc phạm vi trong [LOAD-RESILIENCE.md](arena-backend/docs/LOAD-RESILIENCE.md) trước khi chạy. Kết quả sinh vào `test-results/`, không đưa lên Git.

## Dữ liệu và phạm vi bản hiện tại

Repo có source, SDK, migrations, ví dụ và test; không có DB, tài khoản, session, source các đội đã nộp, binary, ZIP sinh sẵn hoặc log trên máy phát triển. Người cài tạo DB và BTC mới. Dữ liệu chạy web mặc định ở `arena-backend/.platform-state/`; không xóa thư mục này nếu cần giữ giải/tài khoản.

C++17 là runtime thi đấu hiện bật. Python/Codon chỉ là nghiên cứu/prototype, chưa bật để nhận bot thật. Ruleset add-on vẫn Experimental và chưa được chứng nhận cân bằng. Mỗi giải hiện dùng một luật, chưa có các vòng thay luật trong cùng giải.

Bản này phục vụ thử local. Trước khi nhận source không tin cậy qua Internet cần host/VM worker riêng, HTTPS cùng origin, kiểm tra cấu hình bảo mật, giới hạn tài nguyên và backup/restore. Xem [SECURITY.md](arena-backend/docs/SECURITY.md), [BACKEND.md](arena-backend/docs/BACKEND.md) và các template trong `arena-backend/deploy/`.

Thư viện JSON được vendored kèm giấy phép tại [sdk/LICENSE.MIT](arena-backend/sdk/LICENSE.MIT). Repo public chưa gán giấy phép chung cho toàn bộ dự án; giấy phép của các thành phần bên thứ ba được giữ nguyên.
