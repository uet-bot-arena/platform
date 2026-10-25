# Luật chơi OTTv2 (Oẳn Tù Tì v2 — bàn cờ 9×9)

## 1. Tổng quan

OTTv2 là trò cờ chiến thuật đối kháng hai người, lấy quy tắc khắc chế của Oẳn Tù Tì
(Đấm – Lá – Kéo) làm cơ chế ăn quân trên bàn cờ 9×9.

Mỗi bên điều khiển một đội quân gồm ba loại. Mục tiêu: **triệt hạ hoàn toàn một loại
quân của đối phương**, hoặc **đưa quân vào căn cứ đối phương**.

---

## 2. Bàn cờ và bố trí ban đầu

### 2.1. Bàn cờ

* Kích thước **9×9** (81 ô).
* Cột: `a`–`i` (chỉ số 0–8). Hàng: `1`–`9`.
* Ký hiệu ô theo kiểu cờ vua: `a1` là góc dưới-trái, `i9` là góc trên-phải.
* Quy ước trong mã nguồn: `row 0` ứng với hàng 9, `row 8` ứng với hàng 1.
  Do đó `a1 = {row: 8, col: 0}` và `i9 = {row: 0, col: 8}`.

### 2.2. Căn cứ

* **Căn cứ phe Đỏ:** ô `a1`.
* **Căn cứ phe Xanh:** ô `i9`.

Hai ô này để trống khi bắt đầu.

### 2.3. Quân cờ

Mỗi bên có **9 quân**:

| Quân | Ký hiệu | Hình dạng |
|---|---|---|
| Đấm (Búa) | Đ | hình tròn |
| Lá (Bao) | L | hình vuông |
| Kéo | K | hình tam giác |

Mỗi loại 3 quân. Dùng hình dạng khác nhau, không chỉ dựa vào màu, để phân biệt được
nhanh và thân thiện với người mù màu.

### 2.4. Vị trí khởi đầu

**Phe Xanh** (hàng 8–9, `i9` để trống):

| Ô | `b9` | `d9` | `f9` | `h9` | `a8` | `c8` | `e8` | `g8` | `i8` |
|---|---|---|---|---|---|---|---|---|---|
| Quân | Đấm | Lá | Kéo | Đấm | Lá | Kéo | Đấm | Lá | Kéo |

**Phe Đỏ** (hàng 1–2, `a1` để trống):

| Ô | `a2` | `c2` | `e2` | `g2` | `i2` | `b1` | `d1` | `f1` | `h1` |
|---|---|---|---|---|---|---|---|---|---|
| Quân | Kéo | Lá | Đấm | Kéo | Lá | Đấm | Kéo | Lá | Đấm |

Phe Đỏ đi trước.

---

## 3. Di chuyển

* Mỗi lượt chọn **một quân** của phe mình và đi **đúng một ô**.
* **8 hướng**, giống quân Vua trong cờ vua: lên, xuống, trái, phải và 4 đường chéo.
* Ô đích hợp lệ khi:
  1. nằm trong bàn cờ, **và**
  2. đang trống, **hoặc** có quân đối phương mà quân mình ăn được (mục 4).
* **Không** được đi vào ô có quân cùng phe.
* Không có nước đi nào vượt quá 1 ô; không có quân nào "trượt dài".

---

## 4. Ăn quân

Quy tắc khắc chế:

```
Đấm  ăn  Kéo
Kéo  ăn  Lá
Lá   ăn  Đấm
```

* Khi một quân đi vào ô có quân đối phương mà nó khắc chế, quân đối phương bị loại
  khỏi bàn cờ và quân tấn công chiếm ô đó.
* **Cùng loại không ăn nhau.** Đấm không ăn được Đấm, Kéo không ăn được Kéo, Lá không
  ăn được Lá. Hai quân cùng loại chỉ **đứng chặn đường** nhau — không đi vào ô đó được.
* **Không tự sát.** Quân cũng không được đi vào ô có quân đối phương đang khắc chế
  mình (ví dụ Đấm không đi vào ô có Lá).

Tóm lại, một ô có quân đối phương chỉ đi vào được khi quân mình ăn được quân đó.

---

## 5. Điều kiện thắng

Ván kết thúc ngay khi xảy ra một trong các điều kiện sau.

### 5.1. Ăn hết sạch một LOẠI quân của đối phương ★

Nếu **toàn bộ quân của một loại** của đối phương bị ăn hết (cả 3 quân Đấm, hoặc cả 3
quân Lá, hoặc cả 3 quân Kéo) thì bên còn lại **thắng ngay lập tức**, không phụ thuộc
số quân còn lại của đối phương.

Ví dụ: Xanh còn 6 quân nhưng đã mất cả 3 quân Kéo → Đỏ thắng.

Đây là điều kiện thắng đặc trưng của OTTv2, nên giao diện luôn hiển thị số quân còn
lại theo từng loại của cả hai bên, và cảnh báo khi một loại chỉ còn 1 quân.

### 5.2. Chiếm căn cứ đối phương ★

* Phe Đỏ đưa được một quân bất kỳ vào ô `i9` → **Đỏ thắng**.
* Phe Xanh đưa được một quân bất kỳ vào ô `a1` → **Xanh thắng**.

Quân đứng trên căn cứ **của chính mình** thì không có ý nghĩa gì, và có thể dùng để
phòng ngự chặn ô đó.

### 5.3. Các điều kiện mở rộng

Không có trong đề bài gốc, được thêm để ván đấu luôn kết thúc dứt điểm:

* **Bí nước:** đến lượt một bên mà bên đó không còn nước đi hợp lệ nào → bên đó thua.
* **Hết thời gian (cập nhật thể thức bot):** mỗi đội có quỹ riêng cho cả trận:
  Ladder và xếp hạng chính thức mặc định 10 giây mỗi bot (`official.match_ms=10000`); luyện tập/giải nội bộ có thể chọn Bullet 1 phút hoặc thường 5 phút. Chỉ trừ khi đến lượt, không cộng giây.
  Hết quỹ thì xử thua. Các giải đã chốt cấu hình mỗi lượt trước đây giữ nguyên. Quyền tuyên bố hết giờ thuộc về **đối thủ** của
  bên đang tới lượt, và chỉ sau một khoảng bù 3 giây, để lỗi mạng không làm xử thua oan.
* **Đầu hàng:** người chơi tự nhận thua.

### 5.4. Thứ tự ưu tiên

Khi nhiều điều kiện cùng thoả, xét theo thứ tự:

1. Đầu hàng
2. Hết thời gian
3. Chiếm căn cứ
4. Ăn hết sạch toàn bộ quân
5. Ăn hết sạch một loại quân
6. Bí nước

---

## 6. Gợi ý chiến thuật

1. **Đếm theo loại, không đếm tổng.** Mất 3 quân Kéo là thua, dù còn 6 quân khác. Khi
   một loại chỉ còn 1 quân, quân đó thành mục tiêu sống còn — hãy rút về hoặc bọc lót.
2. **Ngược lại, hãy tập trung diệt một loại của đối phương** thay vì ăn quân dàn trải.
3. **Bọc lót theo cặp.** Đặt Đấm cạnh Kéo, Kéo cạnh Lá: quân địch lao vào ăn một con sẽ
   bị con còn lại trừng phạt.
4. **Giữ căn cứ.** Đưa một quân đứng lên ô `a1` / `i9` của mình để bít đường thắng nhanh
   của đối phương; đối phương sẽ phải ăn được quân đó mới vào được.
5. **Kiểm soát trung tâm.** Quân ở hàng 4–6 có đủ 8 hướng nên linh hoạt nhất.

---

## 7. Đối chiếu với mã nguồn

Luật được thực thi bởi [`arena/engine.py`](../arena-backend/arena/engine.py). UI tham chiếu luật trong [`live/rules.js`](../uet-bot-arena/dist/live/rules.js); engine phía server quyết định kết quả.

| Luật | Hàm |
|---|---|
| Khắc chế ăn quân | `canCapture()`, hằng `BEATS` |
| 8 hướng, đi 1 ô | `DIRECTIONS`, `getValidMoves()` |
| Kiểm tra nước đi hợp lệ | `validateMove()` |
| Bố trí ban đầu | `createInitialBoard()` |
| Đếm quân theo loại | `countPieces()` → `byType` |
| Loại quân đã bị diệt sạch | `findEliminatedTypes()` |
| Điều kiện thắng | `checkGameOver()` |

Các luật trên được kiểm ở [test_core.py](../arena-backend/tests/test_core.py), chạy `make test` tại `arena-backend/`. Tên hàm trong bảng trên thuộc bản JavaScript cũ; engine hiện tại ở [arena/engine.py](../arena-backend/arena/engine.py).
