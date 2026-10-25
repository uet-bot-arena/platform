# Ruleset add-on · Experimental · rules-v1 / protocol v3

Tính năng thử nghiệm, mặc định tắt. Luật chưa được chứng nhận cân bằng. SDK/engine
OTT v2 tiêu chuẩn vẫn dùng protocol v2 và runtime native đã ghim.

## BTC: bật và công bố luật

1. Cài rootfs/toolchain và kết nối máy chấm theo [MAY-CHAM.md](../TAI-LIEU.docx).
2. Đăng nhập BTC, mở **Ruleset add-on**, bật công tắc cho phép sử dụng add-on.
3. Chọn Energy + Dash hoặc Domination + Shield, chỉnh filter, mẫu đi và vùng.
4. **Preview & kiểm tra** trả trạng thái và toàn bộ action hợp lệ. Chọn action để
   mô phỏng tối đa 32 lượt; nút về đầu xóa chuỗi preview. Preview không tạo job,
   chạy bot, sửa giải hoặc tính điểm.
5. Lưu draft → **Kiểm tra & công bố** (hoặc nhập JSON và công bố ngay). Luật đã công bố là bất biến; Clone tạo phiên
   bản mới. Ẩn khỏi thư viện ngăn chọn cho job/giải mới, giữ mọi snapshot cũ.
6. Chọn **Tạo giải với luật này**, hoặc bật Ruleset add-on trong form và chọn bản Published.
   Cấu hình ghim ngay khi tạo: definition/hash, engine, SDK, runtime, suite, timing.
7. Đội benchmark đúng ruleset, đăng ký bot; BTC chốt và bắt đầu hai lượt như cũ.

Tắt công tắc ngăn tạo job/giải experimental mới; job đã nhận, giải đã chốt và
replay vẫn dùng snapshot. Không đổi luật giữa trận. Không tự thay luật của giải cũ.
Published chỉ xác nhận schema, giới hạn và mô phỏng, không chứng nhận cân bằng.

Thư viện cho đội có chi tiết thay đổi sinh từ definition, tải starter riêng theo
ruleset, gói tài liệu/JSON Schema và thử legal_actions. Quy trình nhập/xuất và
API tự động hóa: [RULESET-ADDONS.md](RULESET-ADDONS.md).

## Cơ chế xác định

Tọa độ/bố trí 18 quân và quan hệ Búa > Kéo > Lá > Búa giữ nguyên. Mỗi quân có
ID cố định dạng `red-a2`, `blue-i8` theo vị trí **ban đầu**, không đổi khi di chuyển.

- Nước thường: 8 hướng, 1 ô, không tốn năng lượng.
- Modifier thêm mẫu đi cho filter `kinds`, `piece_ids`. Hai filter kết hợp AND;
  trong từng danh sách là OR. Bỏ trường filter con có nghĩa mọi quân; danh sách
  rỗng bị từ chối để tránh áp dụng nhầm. Chọn từng loại trên UI.
- Mẫu `orthogonal`, `diagonal`, `king`: đi 1..range ô, không xuyên quân.
  `knight`: nhảy chữ L, `range=1`, được vượt quân. Mọi nước ăn vẫn phải khắc chế.
- Mỗi modifier có `cost`, `cooldown`. Cooldown N nghĩa sau khi dùng phải hoàn
  thành N lượt tiếp theo của chính đội trước khi dùng lại. Cooldown gắn với quân.
- Khiên: action riêng, tốn một lượt và năng lượng, chặn đúng một lần bị ăn.
  Bị chặn: kẻ tấn công ở nguyên ô, Khiên bị mất, lượt tấn công vẫn tiêu thụ;
  chi phí/cooldown kỹ năng tấn công vẫn áp dụng. Khiên không đổi quan hệ khắc chế.
  Duration N kéo dài qua N lượt tiếp theo của đội sở hữu; hết ở cuối lượt thứ N.
- Vùng energy/control thưởng cho **bên vừa hành động**, ở cuối lượt, nếu quân
  bên đó đang chiếm ô. Mỗi vùng cộng gain (1..3); năng lượng không vượt cap.
  Đối thủ chiếm vùng thì nhận thưởng vào cuối lượt của họ. Không có chủ vùng vĩnh viễn.
- Ưu tiên thắng: chiếm căn cứ → hết toàn quân → hết một loại (nếu bật) → điểm
  kiểm soát đạt ngưỡng (nếu bật) → đối thủ hết action (nếu bật).
  Thắng do căn cứ/ăn quân kết thúc trước thưởng vùng. Sau đó áp dụng lặp thế/guard.
- Lặp thế tính bên tới lượt, ID/loại/vị trí quân, năng lượng, điểm và thời hạn
  Khiên/cooldown **còn lại**, không tính số lượt tuyệt đối hoặc lịch sử sự kiện.
- Một action mỗi lượt; không ngẫu nhiên, stack effect, thay loại quân hoặc code
  thực thi do admin tải lên trong rules-v1.

Giới hạn: 9 vùng, 4 modifier, range 1..3, cap 0..9, cooldown 0..8, shield duration
1..5, mục tiêu control 0..30. Definition tối đa 8 KiB; bộ kiểm tra chặn cấu hình
có thể sinh hơn 240 action/lượt để giữ frame worker trong 65.536 byte gồm LF.
Vùng bất đối xứng hoặc filter ID riêng màu hiện cảnh báo, BTC cần thử đổi màu.

## Thử SDK v3 trong giải nội bộ

Tải **SDK Experimental** trên Nộp bot / Tài liệu & thể lệ. ZIP chứa `bot.cpp`,
`experimental.hpp`, `json.hpp`, LICENSE và hướng dẫn. GitHub hiện nhận protocol v2 cho giải online chuẩn; SDK v3 là đường thử nội bộ do BTC nạp, khai `X-Arena-Protocol: 3` ở route import BTC. Không có ZIP upload của sinh viên.
Khai báo này không thay kiểm tra handshake thực tế. Source/binary vẫn được cô lập.

Ở Benchmark/Luyện tập chọn bản Published. Chọn tiêu chuẩn nếu bot có hỗ trợ v2;
bot chỉ SDK v3 phải chọn rõ ruleset add-on, không để trống.
Benchmark luôn 24 trạng thái × 3 = 72 mẫu, nhưng suite/hash phụ thuộc ruleset.
Benchmark của luật khác không đủ điều kiện dự giải. Bot mẫu mở rộng cũng hiểu
init v2 để chơi tiêu chuẩn, không buộc sửa bot/SDK v2 đã nộp trước đây.

## JSON Lines protocol v3

Transport, encoding, flush, turn_id và state_version giữ quy tắc v2.
`init.protocol_version=3`; payload có side, engine, profile, match và `ruleset`:
`{id,version,hash,definition,requires,engine_sha256,sdk_sha256}`.

Bot đọc definition, kiểm tra primitive hỗ trợ rồi trả:

```json
{"protocol_version":3,"type":"ready","match_id":"copy-from-init","ruleset_hash":"copy-from-init.payload.ruleset.hash","capabilities":["rules-v1"]}
```

Observation envelope vẫn có `match_id`, `turn_id` (string opaque),
`state_version` (integer), `timing` và payload. Payload chứa:

- `state`: board/to_move/ply/result; thêm `energy`, `control`, `turns` theo màu.
- Quân: side/kind/id, `cooldowns` (mốc lượt đội được dùng lại), `shield_until`.
- `zones`, `counts`, `bases`, `ruleset_hash`, `adjudication`.
- `legal_actions`: mỗi action có `id`, type, piece_id, from, optional to/ability,
  energy_cost. ID chỉ có giá trị trong observation hiện tại; không suy ra từ tọa độ.

Trả action bằng ID, giữ đúng envelope và kiểu dữ liệu:

```json
{"protocol_version":3,"type":"action","match_id":"copy","turn_id":"copy","state_version":0,"payload":{"action_id":"a17"}}
```

Hoặc payload `{"resign":true}`. Server tính lại legality; không tin bot tự báo
chi phí, capture, trạng thái hay thời gian. Không trả nguyên object legal_action.

## SDK mô phỏng

`arena_experimental::Rules rules(init["payload"]["ruleset"]["definition"]);`

- `rules.legal_actions(state)` → danh sách action, cùng thứ tự/ID với server.
- `rules.apply(state, {{"action_id", id}})` → bản sao trạng thái kế tiếp.
- `rules.evaluate_objectives(state, side)` → heuristic mẫu; đội được thay thế.

Bot mẫu duyệt một bước và chấm trạng thái sau action; kiểm tra legal_actions của
SDK khớp server mỗi observation. Đội có thể dùng simulator trong minimax/MCTS.
SDK apply mô phỏng **luật bàn cờ**; thuật toán tìm kiếm cần tự theo dõi lặp thế,
512 ply và clock/wall guard của controller nếu muốn dự đoán hòa chính xác.

## Timing, lỗi và replay

Startup 2 s; build 30 s; quỹ cả trận 60 s (Bullet) hoặc 300 s (đấu thường) mỗi bot, chỉ trừ khi đến lượt, không increment; một CPU cho bot, RAM theo hiệu chuẩn native ở [SANDBOX](../TAI-LIEU.docx). Kiểm tra 72 mẫu có 500 ms riêng mỗi mẫu. Giải cũ đã ghim giữ ngân sách lượt cũ.
Engine tính action ngoài ngân sách; serialize/gửi/parse ở bot và nhận đủ LF nằm
trong ngân sách worker monotonic. Deadline cứng `t1 < D`; chờ thêm ít nhất 3 s
để server tuyên bố timeout; không nhận action muộn. Không có time modifier.
Không cần API HTTP hoặc clock sync từ bot; clock trình duyệt chỉ hiển thị.

- `invalid_ruleset`: đường dẫn trường cấu hình và lý do; sửa rồi preview lại.
- `experimental_disabled`: BTC bật công tắc trước khi tạo job/giải mới.
- `ruleset_conflict`: cần Published hoặc Clone thay vì sửa luật đã công bố.
- `unsupported_protocol`: nộp version mới khai báo SDK v3 và dùng starter mở rộng.
- `protocol_error`: ready thiếu capability/hash, response sai schema hoặc v2 bot
  trả response trong trận v3. SDK không hỗ trợ cần báo stderr và dừng sớm.
- `illegal_action`: ID lạ/hết hạn; dùng legal_actions của observation đang xử lý.
- `qualification_required`: benchmark chưa đủ 72 mẫu/khác ruleset hoặc runtime.

Live/replay nhận snapshot + trace (`move`, `ability_used`, `shield_broken`,
`energy_gained`, `control_gained`). Bàn cờ patch tại chỗ, có vùng, tài nguyên và
Khiên. Replay đọc log bất biến, không chạy lại bot hay áp dụng luật mới vào trận cũ.

## Kiểm thử

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/experimental_parity.py
.venv/bin/python tests/experimental_integration.py
```

Parity chạy simulator C++ trong bwrap, đối chiếu legal actions và next state
với engine Python. Integration dùng tài khoản/DB tạm, upload thật, benchmark,
hai lượt chính thức, SSE/replay và kiểm tra tắt tính năng/luật bất biến.

Harness tải cũ đã gỡ ở gói 3b; kiểm tải cho kiến trúc máy chấm từ xa tiếp tục theo PLAN_M1.md gói 8–10. Các số đo lịch sử không dùng nghiệm thu M1.

Kết quả kiểm chứng local: [EXPERIMENTAL-VALIDATION.md](../TAI-LIEU.docx).
