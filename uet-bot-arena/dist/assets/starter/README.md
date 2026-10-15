# Bot mẫu cho UI demo

Đây là skeleton để thử JSON Lines qua stdin/stdout, chưa phải Dev Kit hay đặc tả thi chính thức. Cần cập nhật theo protocol và luật do ban tổ chức chốt.

Yêu cầu: Python 3. Không cần cài thư viện ngoài.

Chạy `python bot.py`, sau đó nhập một dòng JSON:

```json
{"type":"observation","turn_id":"t1","state_version":0,"payload":{"state":{},"legal_moves":[{"kind":"move","from":[6,1],"to":[5,1]}]}}
```

Bot trả action đầu tiên trong `legal_moves`. Thay đổi `choose_move` để viết chiến thuật. Dữ liệu state và legal_moves phải được adapter/engine thật cung cấp. Khi không có nước đi, skeleton trả payload null để minh họa; quy định pass/resign cần bổ sung sau khi chốt protocol.

Chỉ in JSON ra stdout. Gửi debug log ra stderr.
