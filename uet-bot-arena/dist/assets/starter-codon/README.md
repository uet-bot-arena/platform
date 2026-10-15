# Python + Codon starter · lựa chọn thử nghiệm

C++17 vẫn là mặc định của nền tảng. Đội chủ động đồng ý thử Python + Codon mới chọn profile này. **Không cam kết tương thích toàn bộ Python hoặc mọi thư viện PyPI.**

Skeleton dùng cùng observation/action như starter C++, chưa phải SDK/luật OTTv2 chính thức. Xem bảng API đã compile/run trong mục Test & benchmark. Cấu trúc JSON của Codon khác dict/list CPython; chuyển dữ liệu tại boundary rồi viết thuật toán trên list/dict cùng kiểu. Chú ý giới hạn số nguyên 64 bit.

## Chạy local và build native

```sh
python3 bot.py
codon build -release -numerics=py -exe -o bot bot.py
./bot
```

Prototype đã thử bằng **Codon 0.20.3** trên Linux x86_64/Ubuntu WSL. Compiler/runtime production sẽ do ban tổ chức cố định. Dependency Python riêng phải được kiểm chứng trước, không cài tùy ý qua requirements.txt.

Observation mẫu, một dòng:

```json
{"type":"observation","turn_id":"t1","state_version":0,"payload":{"state":{},"legal_moves":[{"kind":"move","from":[6,1],"to":[5,1]}]}}
```

Bot trả action đầu tiên, giữ nguyên turn_id/state_version. Danh sách trống trả payload null; pass/resign chưa được đặc tả. Stdout chỉ chứa action, log ra stderr. Bot sống suốt phiên, nhận nhiều lượt qua stdin.

## Nếu Codon không phù hợp

Dùng starter C++, chuyển thuật toán sang source C++, chọn C++17 và nộp một phiên bản mới để build/test lại. Đổi mục runtime không tự chuyển code Python thành C++, và không đổi runtime của bản đã chốt giữa trận.

Đóng gói `bot.py`, `requirements.txt`, `README.md` ở gốc ZIP, không kèm executable. UI hiện chỉ lưu metadata; worker build/validate/benchmark chưa được nối với source đội thi.
