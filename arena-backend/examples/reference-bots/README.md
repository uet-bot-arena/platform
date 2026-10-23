# Bot tham khảo OTTv2

`bot.cpp` và `bot.py` cùng thực hiện negamax alpha-beta, evaluation và thứ tự nước. Có thêm `MODE` cho random, defensive và MCTS/UCT. Đổi hằng `MODE`, `THINK_MS`, `MAX_DEPTH` ở đầu file, rồi đóng ZIP thành các version khác nhau. Không cần build script. BTC có thể tải từng ZIP vào danh mục bot mẫu và gắn mức khó theo kết quả đấu thực tế.

Alpha-beta dừng theo ngân sách 100 ms mặc định, chọn nước từ vòng iterative deepening hoàn tất gần nhất. MCTS dùng cây UCT và rollout giới hạn. Các bot đọc kích thước bàn/căn cứ/điều kiện thắng từ trạng thái, giữ đúng giao thức JSON Lines v2. Đây là ví dụ để học và đo, chưa phải bot đã tối ưu hoặc chứng nhận độ khó.

[Báo cáo so sánh](../../docs/PYTHON-COMPARISON.md) là số đo lịch sử; harness cũ đã gỡ. Kiểm tương thích ba ngôn ngữ hiện dùng `tests/languages_integration.py` trên sandbox native. Python mặc định PyPy 7.3.23.

Nguồn thuật toán: [Kocsis & Szepesvári, Bandit Based Monte-Carlo Planning](https://www.cs.utexas.edu/~shivaram/readings/b2hd-KocsisSzepesvari2006.html), [nghiên cứu alpha-beta và bảng chuyển vị](https://arxiv.org/abs/1404.1517). Code được viết cho engine của repo; không chép bot của cuộc thi khác.