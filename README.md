# UET Bot Arena

Bài tập lớn Lập trình mạng — Nhóm 5.

Web: **https://uetbotarena.id.vn**

UET Bot Arena tổ chức giải bot cờ Oẳn Tù Tì (OTTv2) cho sinh viên. Hub trên VPS điều hành giải, không chạy bot. Máy chấm Windows/Linux của nhóm kết nối ra hub để nhận việc và chạy bot trong sandbox.

## Tính năng

- **Khách:** Xem giải công khai, lịch thi đấu, bảng xếp hạng và ma trận kết quả, replay từng nước, trận trực tiếp và nhánh chung kết mà không cần tài khoản.
- **Thí sinh:** Đăng ký bằng email `@vnu.edu.vn` và mã 6 số, lập đội 4 người bằng mã mời hoặc tìm thành viên trên trang Ghép đội; nộp bot Python/C11/C++17 từ repo riêng tư qua GitHub App, tự kiểm 72 mẫu và báo Đạt/Lỗi trên commit. Thử nhanh cho dán code/chọn file để kiểm và luyện tập, tối đa 10 bản/giờ; có luyện tập với bot mẫu.
- **Thể lệ:** Ladder Elo mở đợt vào giờ cố định (mặc định 2 giờ), chốt bài tại hạn theo lịch sử push GitHub, rồi xếp hạng chính thức: vòng chung Elo khi có hơn 64 đội, tiếp đến vòng tròn 2 ván/cặp và chọn 16 đội chung kết. Hệ thống còn có vòng tròn/Swiss nội bộ và xuất CSV.
- **Ban tổ chức:** Duyệt đội/tài khoản, tạo, mở hoặc tạm dừng giải và đăng thông báo; quản lý máy chấm bằng mã riêng, link tải có chữ ký hạn 7 ngày và thu hồi mã. Nhật ký hệ thống ghi tài khoản, vai trò và IP.
- **Chấm phân tán:** Lease, heartbeat và fencing cho phép giao lại việc khi máy chấm chết, không tính điểm hai lần. Bot chạy trong AppContainer + Job Object trên Windows hoặc bubblewrap + cgroup v2 + seccomp trên Linux.
- **Chung kết LAN:** Chạy `install-hub.sh --lan`, các đội tự nạp ZIP; 16 đội chia 4 bảng rồi tự đấu loại trực tiếp, có màn hình chiếu cho BTC. Kết quả và replay được xuất từ hub LAN rồi nạp một chiều lên web thật.
- **Vận hành:** Cloudflare Tunnel + nginx, SQLite WAL, publisher tạo snapshot, mailer SMTP và backup hằng ngày kéo ra ngoài VPS. Giao diện có tiếng Việt/Anh và chế độ sáng/tối.

Chỉ tài khoản BTC được phân quyền quản lý máy chấm được tạo, thu hồi hoặc xóa máy chấm và lấy gói cài; các tài khoản BTC khác xem được trang Máy chấm nhưng không đổi được.

## Công nghệ

Python 3.12 + FastAPI + SQLite (`arena-backend/`), JavaScript thuần không bước build (`uet-bot-arena/dist/`), nginx, cloudflared.

## Chạy kiểm thử

Backend trong Linux/WSL:

```sh
cd arena-backend && make setup && make test
```

UI:

```sh
cd uet-bot-arena && npm run check && node --test tests/*.test.mjs
```

Cài hub/máy chấm xem [cài đặt và vận hành](docs/thiet-ke.md#10-cài-đặt-và-vận-hành) và [mạng LAN ngày chung kết](docs/thiet-ke.md#11-mạng-lan-ngày-chung-kết).

## Tài liệu

- [Thí sinh bắt đầu từ đâu](docs/thi-sinh.md): chuẩn bị tài khoản, viết bot, thử và nộp vào giải.
- [Giải đấu](docs/giai-dau.md): luật, tài khoản, nộp bài, thể lệ và chung kết.
- [Thiết kế kỹ thuật](docs/thiet-ke.md): kiến trúc, sandbox, chịu lỗi, số đo và vận hành.
- [Hướng dẫn demo](docs/huong-dan-demo.md): các bước sử dụng có ảnh theo từng vai trò và ngày chung kết.

## Dùng thử

- Khách: xem giải công khai, lịch, bảng điểm và replay mà không cần tài khoản.
- Đội thử: dùng Thử nhanh → Luyện tập, hoặc nộp qua GitHub vào Giải mở thử nghiệm theo hướng dẫn thí sinh.
- BTC thử: tạo giải vòng tròn với đội DEMO theo [hướng dẫn demo](docs/huong-dan-demo.md).
- Tài khoản thử do nhóm gửi kèm link.

| Thư mục | Nội dung |
|---|---|
| `arena-backend/` | Luật, máy chấm, API, cơ sở dữ liệu và kiểm thử |
| `uet-bot-arena/` | Giao diện web |
| `docs/` | Tài liệu giải đấu, thiết kế và hướng dẫn |
| `luat/` | Văn bản luật mà web phục vụ |
| `starter/` | Repo mẫu cho thí sinh |
| `scripts/`, `config/` | Cài đặt, sao lưu và cấu hình mẫu |
| `tools/` | Tính lại kết quả và công cụ hỗ trợ |
