# C++17 starter · UET Bot Arena

C++ là ngôn ngữ mặc định. Đây là skeleton JSON Lines qua stdin/stdout để thử protocol, **chưa phải SDK OTTv2 chính thức hoặc engine kiểm tra luật**.

## Build và chạy

```sh
g++ -std=c++17 -O2 -Wall -Wextra bot.cpp -o bot
./bot
```

Windows MinGW có thể dùng `-o bot.exe`, sau đó chạy `.\bot.exe`.

Nhập một observation trên một dòng:

```json
{"type":"observation","turn_id":"t1","state_version":0,"payload":{"state":{},"legal_moves":[{"kind":"move","from":[6,1],"to":[5,1]}]}}
```

Bot trả action đầu tiên trong legal_moves. Khi danh sách trống, skeleton trả payload null; quy định pass/resign cần chốt trong protocol thi thật. Chỉ in action JSON ra stdout, debug log ra stderr. Tiến trình giữ sống để nhận nhiều lượt.

Sửa `choose_move` để viết chiến thuật. State và legal_moves do adapter/engine cung cấp, không được coi action là hợp lệ chỉ vì bot tự tạo ra nó. Giữ nguyên turn_id và state_version.

## Đóng gói

Nộp `bot.cpp`, `json.hpp`, `LICENSE.MIT` và `README.md` ở gốc ZIP. Không kèm binary. Bản UI chỉ lưu metadata; backend build/validate/benchmark cần tích hợp riêng. Compiler/image/flags và giới hạn tài nguyên của giải chưa được chốt; lệnh trên dùng để thử local.

`json.hpp` là nlohmann/json **v3.11.3**, MIT, copyright Niels Lohmann và contributors; license đầy đủ kèm trong `LICENSE.MIT`. Nguồn: https://github.com/nlohmann/json/releases/tag/v3.11.3

SHA-256 header: `9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6`.
