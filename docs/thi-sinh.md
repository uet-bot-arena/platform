# Thí sinh bắt đầu từ đâu

Hướng dẫn này đi từ chuẩn bị tài khoản đến xem bot của đội thi đấu. Luật và kết quả công khai có thể xem ngay trên [website](https://uetbotarena.id.vn); chỉ cần đăng nhập khi dùng chức năng riêng của đội.

## 1. Cần chuẩn bị gì

Bạn cần email trường `@vnu.edu.vn` và một tài khoản GitHub. Nếu muốn thử bot trên máy, cài Python 3.11 trở lên; bot C11/C++17 cần thêm trình biên dịch `gcc`/`g++`. Máy chấm chính thức dùng Python 3.11 (PyPy), C11 và C++17 như trang đăng nhập ghi; chạy local không thay kết quả sandbox của giải.

## 2. Tạo tài khoản và đội

Chọn Đăng ký, nhập email trường, lấy mã 6 số rồi đặt mật khẩu; kiểm Spam nếu chưa thấy thư. Mã hết hạn 10 phút và không phải mật khẩu đăng nhập; xem [bước 4–7 có ảnh](huong-dan-demo.md#bước-4-đăng-ký-bằng-email-trường).

Chưa có đội vẫn Thử nhanh, kiểm 72 mẫu, luyện tập với bot mẫu và xem replay được; chỉ không vào giải. Tổng quan có khung Các bước vào giải; hồ sơ và mã mời nằm ở Cá nhân & đội.

Sau khi đăng nhập, vào Cá nhân & đội để tạo đội hoặc nhập mã mời 8 ký tự do đội trưởng gửi riêng. Đội dự giải có đủ 4 người; mỗi người chỉ thuộc một đội, và người thứ tư vào thì mã không nhận thêm thành viên. Trang Ghép đội giúp tìm người hoặc tìm đội đang tuyển; xem [tạo đội và mời thành viên](huong-dan-demo.md#bước-8-tạo-đội).

## 3. Bot đọc và trả lời thế nào

Bot là chương trình chạy suốt ván, đọc từng dòng JSON từ stdin và in một dòng JSON trả lời lên stdout, có flush. Khi nhận `init`, trả `ready`; khi nhận `observation`, chọn một nước trong `payload.legal_moves` và trả `action`, giữ nguyên `match_id`, `turn_id`, `state_version`. Ghi debug vào stderr, không trộn vào stdout; xem [starter](../starter/README.md), [SDK](../arena-backend/sdk/README.md) và [luật](../luat/OTTV2-RULES.md).

Đây là một lượt trao đổi thật lấy từ `run_local.py --check` với bot bên dưới; chỉ rút gọn mã trận thành `demo`. Bot nhận toàn bộ trạng thái và danh sách nước hợp lệ, rồi chọn c2 → c3:

```json
{"protocol_version":2,"type":"observation","match_id":"demo","turn_id":1,"state_version":0,"payload":{"state":{"board":[[null,{"side":"blue","kind":"rock"},null,{"side":"blue","kind":"paper"},null,{"side":"blue","kind":"scissors"},null,{"side":"blue","kind":"rock"},null],[{"side":"blue","kind":"paper"},null,{"side":"blue","kind":"scissors"},null,{"side":"blue","kind":"rock"},null,{"side":"blue","kind":"paper"},null,{"side":"blue","kind":"scissors"}],[null,null,null,null,null,null,null,null,null],[null,null,null,null,null,null,null,null,null],[null,null,null,null,null,null,null,null,null],[null,null,null,null,null,null,null,null,null],[null,null,null,null,null,null,null,null,null],[{"side":"red","kind":"scissors"},null,{"side":"red","kind":"paper"},null,{"side":"red","kind":"rock"},null,{"side":"red","kind":"scissors"},null,{"side":"red","kind":"paper"}],[null,{"side":"red","kind":"rock"},null,{"side":"red","kind":"scissors"},null,{"side":"red","kind":"paper"},null,{"side":"red","kind":"rock"},null]],"to_move":"red","ply":0,"result":null},"legal_moves":[{"from":"a2","to":"a3"},{"from":"a2","to":"b3"},{"from":"a2","to":"b2"},{"from":"a2","to":"a1"},{"from":"c2","to":"b3"},{"from":"c2","to":"c3"},{"from":"c2","to":"d3"},{"from":"c2","to":"b2"},{"from":"c2","to":"d2"},{"from":"c2","to":"c1"},{"from":"e2","to":"d3"},{"from":"e2","to":"e3"},{"from":"e2","to":"f3"},{"from":"e2","to":"d2"},{"from":"e2","to":"f2"},{"from":"e2","to":"e1"},{"from":"g2","to":"f3"},{"from":"g2","to":"g3"},{"from":"g2","to":"h3"},{"from":"g2","to":"f2"},{"from":"g2","to":"h2"},{"from":"g2","to":"g1"},{"from":"i2","to":"h3"},{"from":"i2","to":"i3"},{"from":"i2","to":"h2"},{"from":"i2","to":"i1"},{"from":"b1","to":"b2"},{"from":"b1","to":"a1"},{"from":"b1","to":"c1"},{"from":"d1","to":"d2"},{"from":"d1","to":"c1"},{"from":"d1","to":"e1"},{"from":"f1","to":"f2"},{"from":"f1","to":"e1"},{"from":"f1","to":"g1"},{"from":"h1","to":"h2"},{"from":"h1","to":"g1"},{"from":"h1","to":"i1"}],"counts":{"red":{"rock":3,"scissors":3,"paper":3},"blue":{"rock":3,"scissors":3,"paper":3}},"bases":{"red":"a1","blue":"i9"}}}
```

Bot trả về:

```json
{"protocol_version":2,"match_id":"demo","type":"action","turn_id":1,"state_version":0,"payload":{"from":"c2","to":"c3"}}
```

Ladder và xếp hạng chính thức mặc định có 10 giây cho mỗi bot trong cả ván, chỉ trừ khi bot đến lượt; khởi động có quỹ riêng 2 giây. Đọc `timing.budget_ms` để chia thời gian cho các nước còn lại, không hiểu đó là 10 giây cho từng nước. Ban tổ chức có thể đặt quỹ khác cho giải nội bộ; xem [quỹ giờ và đối chiếu](thiet-ke.md#6-quỹ-giờ-và-khả-năng-đối-chiếu).

### Bot Python mẫu

Đây là nguyên mã `starter/examples/bot.py`: ưu tiên vào căn cứ, ăn quân rồi tiến gần căn cứ địch. Đặt thành `bot/bot.py` và chỉ giữ một file chính trong `bot/`; bỏ file chính C/C++ khi chọn Python.

```python
"""OTTv2 starter (giao thức v2), Python 3.11 / PyPy, chỉ thư viện chuẩn.

Chỉ cần sửa hàm choose(). Phần main() đã lo việc đọc/ghi JSON đúng giao thức.
"""
import json
import sys


def choose(payload):
    """Trả về một nước trong payload["legal_moves"], ví dụ {"from": "a2", "to": "a3"}."""
    state, moves = payload["state"], payload["legal_moves"]
    if not moves:
        return {"resign": True}
    rows = len(state["board"])
    side = state["to_move"]
    base = payload["bases"]["blue" if side == "red" else "red"]
    base_row, base_col = rows - int(base[1:]), ord(base[0]) - 97

    def score(move):
        cell = move["to"]
        row, col = rows - int(cell[1:]), ord(cell[0]) - 97
        value = 10000 if cell == base else 0                     # vào căn cứ đối phương: thắng
        value += 50 if state["board"][row][col] else 0            # ô có quân địch: nước ăn quân
        value -= max(abs(row - base_row), abs(col - base_col))    # càng gần căn cứ địch càng tốt
        return value

    return max(moves, key=score)


def main():
    for line in sys.stdin:
        message = json.loads(line)
        if message["protocol_version"] != 2:
            raise ValueError("This starter uses SDK v2")
        result = {"protocol_version": 2, "match_id": message["match_id"]}
        if message["type"] == "init":
            result["type"] = "ready"
        else:
            result.update(type="action", turn_id=message["turn_id"], state_version=message["state_version"],
                          payload=choose(message["payload"]))
        print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
```

## 4. Chạy thử trên máy

Lấy repo mẫu về máy và mở terminal tại thư mục chứa `run_local.py`. Với cấu trúc Python ở trên, chạy hai lệnh sau; Linux có thể dùng `python3` nếu không có lệnh `python`.

```sh
python run_local.py --check
python run_local.py
```

Lệnh đầu kiểm build, bắt tay và hai lượt đi; lệnh sau chơi một ván với bot mẫu Python, tối đa 512 lượt và 60 giây mỗi bên. Đây là kiểm nhanh để debug, không phải bộ 72 mẫu của hub; lỗi local được ghi ở `.local-build/red.stderr.log` và `blue.stderr.log`. Xem thêm [chạy trên máy mình](../starter/README.md#chạy-trên-máy-mình).

## 5. Thử nhanh trên web

Không cần có đội để thử nhanh hoặc luyện tập; muốn vào giải phải có đội đủ 4 người. Vào Nộp bot → Thử nhanh không cần GitHub, chọn Python 3 rồi dán mã hoặc chọn file `.py`; C/C++ dùng `.c`/`.cpp`. Bot nhiều file dùng `.zip` tối đa 1 MiB: nén thư mục `bot/` như repo mẫu, giữ một file chính `bot.py`, `bot.c` hoặc `bot.cpp` và file phụ cùng ngôn ngữ nằm phẳng trong đó; chọn đúng Ngôn ngữ trước khi nạp ZIP. Bấm Nạp và kiểm tra, chờ Đạt/Lỗi rồi mở Log kiểm tra của đúng bản để đọc chẩn đoán; xem [bước 15 có ảnh](huong-dan-demo.md#bước-15-thử-nhanh-không-cần-github).

Khi có bản Đạt, vào Luyện tập, chọn bản vừa nạp và bot mẫu làm đối thủ, xác nhận quyền tuyên bố hết giờ rồi tạo trận. Mở replay từ kết quả để xem từng nước; xem [bước 17](huong-dan-demo.md#bước-17-luyện-tập-với-bot-mẫu). Thử nhanh nhận tối đa 10 bản/giờ, file mã tối đa 200.000 byte hoặc ZIP tối đa 1 MiB; bản này chỉ để kiểm và luyện tập, không vào giải.

Khi vào đội, Thử nhanh và Luyện tập chuyển sang dữ liệu của đội; bản cá nhân cũ không chuyển sang đội và không còn hiện ở đây.

## 6. Nộp chính thức qua GitHub

Đội trưởng dùng [uet-bot-arena/ott-bot-starter](https://github.com/uet-bot-arena/ott-bot-starter), chọn Use this template → Create a new repository và đặt Private. Sao chép đúng tên `ott2026-<mã đội>` mà trang đội đưa, không tự đoán mã; mời 3 thành viên qua Settings → Collaborators. Xem [bước 11](huong-dan-demo.md#bước-11-tạo-repo-từ-mẫu).

Cài [GitHub App UET Bot Arena](https://github.com/apps/uet-bot-arena), chọn Only select repositories và chỉ cấp quyền cho repo đó. Đội trưởng dán link ở trang đội hoặc Nộp bot; chỉ gắn một lần, cần giữ repo Private và quyền App khi thi đấu. Xem [bước 12–13](huong-dan-demo.md#bước-12-cho-app-truy-cập-đúng-repo).

Sửa source trong `bot/`, commit rồi push lên `main`; chỉ một file chính `bot.py`, `bot.c` hoặc `bot.cpp`, không nộp binary, thư mục con, symlink, submodule hay con trỏ Git LFS. Hub tự kiểm 72 mẫu và ghi trạng thái Đạt/Lỗi trên commit; theo dõi đúng SHA trong Nộp bot và đọc log khi Lỗi. Xem [bước 14](huong-dan-demo.md#bước-14-theo-dõi-kiểm-tra-72-mẫu).

## 7. Bot vào giải thế nào

Với giải Elo có nhãn Đang nhận bài, đội đủ 4 người, có repo GitHub và bản Đạt đúng cấu hình giải sẽ được tự lấy vào ladder, không bấm đăng ký bot bằng tay. “Đủ điều kiện, chờ đợt sau” nghĩa là đội đã đủ điều kiện nhưng chưa xuất hiện trong đợt đang chạy; bản GitHub được hub kiểm lại theo cấu hình của mỗi giải khi cần.

Đợt mở theo khoảng giờ và phút BTC đặt, từ 5 phút tới 7 ngày, mặc định 2 giờ; các mốc tính từ giờ đợt đầu đã lưu. Xem giờ Việt Nam của đợt kế tiếp trong khung Tham gia trên trang giải. Giải cũ giữ lịch đã đặt; BTC có thể đổi lịch khi giải còn nhận bài và chưa hết hạn. Mỗi đợt lấy bản Đạt mới nhất tính đến mốc mở; push lúc đang đấu được kiểm/luyện tập ngay và vào ladder từ đợt sau, còn đợt trước chưa xong thì bỏ mốc kế tiếp. Xem SHA, đối thủ và kết quả trong Giải của đội ở Tổng quan hoặc Cá nhân và đội; [bước 16 có ảnh](huong-dan-demo.md#bước-16-xem-giải-của-đội).

Giải vòng tròn/Swiss nội bộ có cách vào khác: khi giải Mở đăng ký, đội trưởng mở chi tiết giải, chọn bản kiểm tra Đạt trong Chọn bot dự giải, xác nhận quyền tuyên bố hết giờ và bấm Đăng ký bot dự giải. Bản Đạt phải khớp luật, quỹ giờ và nền tảng máy chấm của giải; nếu thiếu, đi Đến kiểm tra với đúng cấu hình giải. BTC cũng có thể thêm đội có bản Đạt trước khi chốt; xem [thể lệ](giai-dau.md#4-thể-lệ-và-lịch-đấu).

## 8. Hạn nộp và chung kết

Hub dùng lần push cuối lên `main` theo giờ GitHub không muộn hơn hạn, kiểm bản đó; nếu Lỗi thì lùi lịch sử để tìm bản Đạt trong giới hạn của giải. Ngày author/committer tự đặt trong Git không chứng minh thời điểm nộp, và push sau hạn không đổi bản đã khóa. Giữ repo và quyền App để hub đọc lại được lịch sử; xem [chốt bài tại hạn](giai-dau.md#42-chốt-bài-tại-hạn).

Sau hạn, hub tự chạy xếp hạng chính thức: hơn 64 đội thì có vòng chung Elo, sau đó tối đa 64 đội đấu vòng tròn hai ván đổi màu mỗi cặp. Mặc định 16 đội đầu vào chung kết LAN; ngày chung kết đội nộp ZIP trên hub LAN theo hướng dẫn BTC, không dùng Thử nhanh để đăng ký thi. Xem [xếp hạng chính thức](giai-dau.md#43-xếp-hạng-chính-thức) và [chung kết tại hội trường](giai-dau.md#7-chung-kết-tại-hội-trường).

## 9. Khi gặp lỗi

Đọc [bảng tình huống nộp bài](giai-dau.md#3-nộp-bot-qua-github) trước: tên repo, quyền App, file chính và log kiểm tra đều có cách xử lý riêng. Ba tình huống đã thử và gặp thông báo lỗi:

| Tình huống | Cách xử lý |
|---|---|
| ZIP sai cấu trúc ở Thử nhanh | Đọc đúng lỗi dưới biểu mẫu; chỉ giữ một file chính và file phụ cùng ngôn ngữ trong `bot/`, không có thư mục con hoặc file ngoài `bot/`; ZIP tối đa 1 MiB. |
| Bot in dòng không đúng JSON/giao thức | Đọc log của bản Lỗi; giữ phần đọc/ghi JSON của mẫu, chỉ in debug ra stderr và flush câu trả lời. |
| Repo còn cả `bot.py` và `bot.cpp` | Chỉ giữ một file chính đúng ngôn ngữ; `run_local.py --check` sẽ báo thư mục phải có đúng một file chính. |

## 10. Thử ngay

[Giải mở thử nghiệm](https://uetbotarena.id.vn/#/competitions/8bb872268fc24552b3d11374ef93a49f) đang mở nhận bài, tối đa 16 đội, ladder mỗi 1 giờ và hạn nộp 30/10/2026 lúc 12:30 giờ Việt Nam. Bạn có thể thử nhanh/luyện tập trước, hoặc hoàn thành đội và repo GitHub để tự vào ladder; tài khoản thử do nhóm gửi riêng, không dùng bản Thử nhanh để vào giải.
