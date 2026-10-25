# UET Bot Arena

Bài tập lớn môn Lập trình mạng — **Nhóm 5**, Trường Đại học Công nghệ, ĐHQGHN.

**Web đang chạy: https://uetbotarena.id.vn** ([bản đang chạy](docs/DEPLOY.md#bản-đang-chạy-mốc-2-từ-24102026): hub trên VPS qua Cloudflare Tunnel, máy chấm là máy Windows của nhóm).

Hệ thống tổ chức giải đấu bot cho trò Oẳn Tù Tì trên bàn cờ (OTTv2). Sinh viên đăng ký bằng email trường, lập đội 4 người và nộp bot C++, C hoặc Python qua repo GitHub private của đội. Hub (FastAPI + SQLite) xếp hàng việc, chạy ladder Elo và vòng tròn, phát trực tiếp và lưu replay; hub không chạy bot.

Máy chấm tự kéo việc từ hub và chạy bot trong sandbox nhẹ, không dùng Docker: AppContainer + Job Object trên Windows, bubblewrap + cgroup v2 trên Linux.

## Cấu trúc

```text
arena-backend/arena/       luật, giao thức, sandbox, máy chấm, hàng đợi, Elo
arena-backend/arena_api/   API FastAPI: tài khoản, đội, giải, GitHub, email, SSE
arena-backend/tests/       test đơn vị, tích hợp, đo tải (load_lab)
uet-bot-arena/dist/live/   giao diện web (HTML/CSS/JS thuần, không cần build)
starter/                   repo mẫu cho đội (uet-bot-arena/ott-bot-starter)
scripts/                   cài hub, cài máy chấm, sao lưu, kiểm bí mật và link
config/                    mẫu cấu hình và file bí mật (giá trị giả)
tools/                     tính lại Elo, mô phỏng thể lệ
docs/                      tài liệu vận hành, thể lệ, báo cáo nghiệm thu
```

## Tài liệu

- Nộp thầy: [bản demo, bằng chứng và cách tự thử](docs/NOP-THAY.md).
- Thí sinh: [nộp bài qua GitHub](docs/NOP-BAI.md), [thể lệ](docs/THE-LE.md), [viết bot](uet-bot-arena/dist/live/bot-guide.md), [luật bàn cờ](docs/OTTV2-RULES.md).
- BTC: [triển khai và sao lưu](docs/DEPLOY.md), [máy chấm](docs/MAY-CHAM.md), [điều hành giải](docs/ADMIN.md), [GitHub App](docs/GITHUB-APP.md), [email](docs/EMAIL.md).
- Kỹ thuật: [kiến trúc](docs/BACKEND.md), [sandbox](docs/SANDBOX.md), [chống phá](docs/BAO-MAT.md), [cỡ máy hub](docs/CO-MAY-HUB.md).
- [Mục lục đầy đủ](docs/TAI-LIEU.md) · [đối chiếu yêu cầu](docs/YEU-CAU.md) · [cài trên máy riêng](INSTALL.md) · [thay đổi](CHANGELOG.md)

## Chạy thử trên máy

Hub cần Linux x86_64 hoặc Ubuntu trong WSL2, Python 3.11+:

```bash
git clone https://github.com/uet-bot-arena/platform.git
cd platform
bash scripts/setup.sh
cd arena-backend && .venv/bin/python -m arena_api bootstrap-admin --username organizer && cd ..
bash scripts/start.sh        # http://127.0.0.1:4181/#/login
```

Hub không tự chấm bài: cài thêm ít nhất một máy chấm theo [MAY-CHAM.md](docs/MAY-CHAM.md). Kiểm thử: `make test` trong `arena-backend/`; `npm run check && node --test tests/*.test.mjs` trong `uet-bot-arena/`.

Thư viện bên thứ ba giữ giấy phép riêng (`arena-backend/sdk/LICENSE.MIT`, `arena-backend/sdk/jsmn.h`).
