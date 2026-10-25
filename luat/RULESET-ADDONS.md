# Ruleset add-on

Pipeline add-on ghép các mô-đun `rules-v1` thành cấu hình luật cho từng giải. Tính năng vẫn được đánh dấu **Experimental**; mặc định tắt. UI nằm ở `#/rulesets`, URL cũ `#/experimental` chuyển tới thư viện mới.

## BTC: từ ý tưởng tới giải đấu

1. Mở **Ruleset add-on**, bật **Cho phép sử dụng add-on**. Công tắc chỉ kiểm soát job/giải mới; không sửa trận, giải hoặc replay đã tạo.
2. Chọn mẫu hoặc nhập tệp JSON. Chỉnh năng lượng, vùng, filter quân, mẫu đi, Khiên và điều kiện thắng. Thêm ghi chú để các đội hiểu mục đích thay đổi.
3. Xem thử trên server. Mỗi action được engine kiểm tra lại; giới hạn 32 bước cho mỗi chuỗi. Có thể trở về đầu để thử đường đi khác. Preview không chạy sandbox/bot, không tính điểm.
4. **Lưu bản nháp → Kiểm tra & công bố**. Server kiểm tra schema, giới hạn action/observation và chạy smoke 32 bước với từng màu đi trước. Cảnh báo bất đối xứng được giữ trong chi tiết luật. Smoke không chứng nhận cân bằng hay sức mạnh bot.
5. **Tạo giải với luật này** tự chọn đúng ruleset trong form tạo giải. Các đội đọc trang chi tiết và benchmark đúng hash/cấu hình thời gian trước khi đăng ký bot.

Có thể **Nhập & kiểm tra** một gói JSON và chọn **Công bố ngay**: nhập, kiểm tra và công bố nằm trong cùng transaction. Nếu lỗi, không có bản ghi dở dang. Tất cả mutation dùng Idempotency-Key; cùng key/input trả cùng kết quả, đổi input cần key mới. Chỉnh sửa dùng expected_revision để tránh ghi đè thao tác BTC khác.

**Tạo phiên bản mới** giữ parent_id và tăng version. Bản đã công bố bất biến. Chỉnh tên cũng đổi hash definition. Muốn tái dùng một cấu hình độc lập, xuất/nhập thành ruleset mới; ID/version trong source của gói chỉ là thông tin xuất xứ, không được tin để ghim runtime. **Ẩn khỏi thư viện** ngăn chọn mới, giữ tài liệu của bản đã công bố để đối chiếu giải cũ.

## Mô-đun có thể ghép

| Mô-đun | Cấu hình |
| --- | --- |
| Năng lượng | Ban đầu và cap 0–9; nhận từ vùng cuối lượt |
| Vùng | Tối đa 9 ô energy/control; gain 1–3; không đặt vào căn cứ |
| Mẫu đi | Tối đa 4 modifier; thẳng, chéo, 8 hướng hoặc nhảy chữ L; range 1–3 (knight = 1) |
| Filter quân | kinds và/hoặc piece_ids cố định theo ô ban đầu; kết hợp AND |
| Kỹ năng | Chi phí năng lượng; cooldown 0–8 lượt của đội; Khiên 1–5 lượt |
| Mục tiêu | Căn cứ, hết một loại, hết nước; thêm control_target 0–30 |

Bot luôn có nước thường 8 hướng/1 ô miễn phí; add-on thêm nước đi. Khiên là action riêng, tiêu thụ lượt. Quy tắc RPS và thứ tự thắng nằm trong [giao thức chi tiết](EXPERIMENTAL.md).

Admin thêm **cấu hình** từ các primitive này mà không sửa engine/SDK. Một primitive mới, chẳng hạn đổi loại quân hay dịch chuyển tức thời, cần bổ sung engine, simulator, parity tests và runtime máy chấm tương ứng; không thể nhúng code thực thi vào JSON. Các giới hạn cùng schema có tại `GET /api/v1/rulesets/schema`.

Server kiểm tra thêm quan hệ giữa các trường, tính duy nhất và mức trần 240 action/lượt.

## Gói JSON có thể tái sử dụng

```json
{
  "format": "ottv2.ruleset-addon",
  "format_version": 1,
  "release_notes": "Kéo có dash; kiểm soát vùng c5/g5 để duy trì năng lượng.",
  "definition": {
    "schema_version": 1,
    "name": "Energy + Dash",
    "energy": {"cap": 3, "initial": 1},
    "zones": [
      {"square": "c5", "kind": "energy", "gain": 1},
      {"square": "g5", "kind": "energy", "gain": 1}
    ],
    "modifiers": [{"id": "dash", "filter": {"kinds": ["scissors"]}, "pattern": "orthogonal", "range": 2, "cost": 2, "cooldown": 1}],
    "shield": {"enabled": false, "cost": 2, "duration": 2},
    "victory": {"base_capture": true, "type_eliminated": true, "no_legal_moves": true, "control_target": 0}
  }
}
```

Importer cũng nhận definition JSON cũ trực tiếp. Gói tối đa 32 KiB, definition tối đa 8 KiB. Các trường/code/pattern không hỗ trợ bị từ chối với đường dẫn lỗi. JSON Schema được kèm trong gói tài liệu.

## Đội thi: hiểu luật và tối ưu bot

**Ruleset add-on → Chi tiết luật** cho biết ghi chú BTC, thay đổi so với parent (hoặc OTTv2), vùng, filter, chi phí, hồi chiêu, mục tiêu, hash và giao thức. Bảng thay đổi được sinh từ definition, không phụ thuộc BTC nhớ viết đủ ghi chú. Có thể đọc luật và preview cả khi BTC tạm tắt tạo job mới.

- **Gói luật & tài liệu**: ruleset.json, contract.json, definition.schema.json, README, PROTOCOL và starter.zip.
- **Starter nộp bot**: ZIP phẳng với bot.cpp, experimental.hpp, json.hpp, README/LICENSE, BTC nạp nội bộ với X-Arena-Protocol: 3; GitHub hiện nhận protocol v2. Gói tài liệu chứa JSON không phải ZIP nộp source.
- **Thử nước đi hợp lệ**: chọn action ở từng thế, xem bàn cờ/năng lượng/cooldown thay đổi và action hợp lệ kế tiếp. Preview không gọi bot hoặc sửa dữ liệu thi đấu.
- **Benchmark với luật này**: chọn đúng ruleset_id; chọn ngân sách thời gian khớp giải. Kết quả benchmark khác hash không đủ điều kiện đăng ký giải.

Đây vẫn là giao thức JSON Lines stdin/stdout; bot không gọi API HTTP. `init.payload.ruleset.definition` là cấu hình đầy đủ, ready cần echo ruleset_hash và `capabilities:["rules-v1"]`. Mỗi observation gửi `payload.legal_actions`. Bot chọn ID từ danh sách hiện tại, sao chép match_id/turn_id/state_version, trả `payload:{"action_id":"..."}` và flush.

ID có thể được dùng lại ở thế khác nên không được cache ID từ lượt trước. Controller kiểm tra envelope và tính lại legality trước khi áp dụng.

SDK v3 hỗ trợ simulator để minimax/MCTS tự tính năng lượng, kiểm soát, cooldown và Khiên theo definition. Các đội cần điều chỉnh đánh giá trạng thái, ưu tiên mục tiêu và quản lý thời gian theo luật; nền tảng không bắt buộc một thuật toán. Quỹ hiện tại là 60 s/300 s mỗi bot cả trận, không increment, chỉ trừ trong lượt. Đọc `payload.clocks` và `timing.remaining_ms`; giải cũ giữ timing đã ghim.

## API quản lý pipeline

Các endpoint dưới đây dành cho web/tooling, cần đăng nhập. POST cần CSRF; mutation cần Idempotency-Key (8–128 ký tự).

| Method | Endpoint | Chức năng |
| --- | --- | --- |
| GET | /api/v1/rulesets | Luật công bố cho đội; enabled/revision/modules |
| GET | /api/v1/rulesets/schema | JSON Schema của definition |
| GET | /api/v1/rulesets/{id} | Definition, contract, changelog sinh tự động, ví dụ protocol |
| POST | /api/v1/rulesets/{id}/preview | `{actions:["a0",...]}`; tối đa 32 bước, engine tính lại mỗi bước |
| GET | /api/v1/rulesets/{id}/package.json | Gói để BTC tái nhập |
| GET | /api/v1/rulesets/{id}/kit.zip | Tài liệu đầy đủ và starter.zip |
| GET | /api/v1/rulesets/{id}/starter.zip | ZIP source hợp lệ, SDK v3 |
| GET | /api/v1/admin/rulesets | Cả draft/published/archived, presets/modules |
| POST | /api/v1/admin/rulesets/import | `{package:{...},publish:true/false}`; kiểm tra + tạo/công bố nguyên tử |
| POST | /api/v1/admin/rulesets | `{definition:{...},release_notes:"..."}`; tạo draft |
| POST | /api/v1/admin/rulesets/{id}/edit | definition/release_notes/expected_revision |
| POST | /api/v1/admin/rulesets/{id}/release | expected_revision; kiểm tra và công bố bản đã lưu |
| POST | /api/v1/admin/rulesets/{id}/clone | expected_revision; tạo bản mới |
| POST | /api/v1/admin/rulesets/{id}/archive | expected_revision; ẩn lựa chọn mới |

Đội không đọc được draft, không sửa/công bố luật. Bản đã published rồi archived vẫn đọc được qua ID để đối chiếu giải cũ. Các endpoint validate/publish riêng và `/admin/experimental`, `/experimental/guide`, `/experimental/starter.zip` giữ tương thích.

## Kiểm chứng

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_addons.py' -v
.venv/bin/python -m unittest discover -s tests -v
```

Kiểm thử pipeline dùng DB/tài khoản tạm: import/export, idempotency/rollback, phân quyền/CSRF, release và diff/immutability, preview ở trạng thái công tắc tắt, giới hạn bước, và BTC nạp ZIP starter qua validator. Không sửa tài khoản hay giải local hiện có.
