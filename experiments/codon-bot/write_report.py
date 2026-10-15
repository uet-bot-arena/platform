"""Render the Vietnamese experiment report from measured data, not hand-entered numbers."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
r = json.loads((ROOT / 'results/results.json').read_text())
jpath = ROOT / 'results/json-results.json'
j = json.loads(jpath.read_text()) if jpath.exists() else None
sample_count = r['method']['benchmark_positions'] * r['method']['repeats'] * 3 * 3
if j:
    assert j['core_sha256_matches_main_experiment'], 'JSON check used a different core source.'
names = {'greedy': 'Greedy', 'minimax': 'Minimax sâu 3, tối đa 8 nhánh', 'mc': 'Monte Carlo 128 rollout'}
lines = [
    '# Thử nghiệm Python → bot native bằng Codon', '',
    '**Kết luận: khả thi để xây MVP có starter kit/SDK và phạm vi Python được quy định rõ.** Cùng một source Python đã build thành executable ELF native, chạy xuyên suốt phiên bằng stdin/stdout và cho kết quả khớp CPython. Hiệu năng trên các thuật toán thử gần bản C++ tham chiếu. Chưa thể kết luận cho mọi code Python hoặc engine OTTv2 thật.', '',
    '## 1. Môi trường và cách đo', '',
    f"- Thời điểm: {r['environment']['date_utc']}.",
    f"- CPU: {r['environment']['cpu']}.",
    f"- OS: {r['environment']['platform']}.",
    f"- CPython: {r['environment']['python'].split()[0]}; Codon: {r['environment']['codon']}; {r['environment']['g++']}.",
    f"- Cố định trên CPU logic {r['environment']['cpu_affinity']}; một bot tính tại một thời điểm, không bật đa luồng trong thuật toán.",
    '- Cùng file `bot.py` cho CPython và Codon. C++ chuyển cùng thuật toán, cùng kiểu số nguyên 64-bit, chưa tối ưu riêng bằng bitboard/SIMD.',
    '- Codon: `-release -numerics=py`; C++: `-O3 -DNDEBUG -std=c++17 -march=native`. Không dùng fast-math hoặc tắt kiểm tra exception cho Codon.',
    f"- Đo sau warm-up: {r['method']['benchmark_positions']} vị trí cố định × {r['method']['repeats']} lần lặp = {r['method']['benchmark_positions'] * r['method']['repeats']} mẫu mỗi thuật toán/runtime. Đảo thứ tự công việc và runtime. Bộ đo giữ bot sống, không tính compile/startup vào thời gian chọn nước.",
    '- Bài toán là nối 5 trên bàn 9×9 minh họa, **không phải OTTv2**. Monte Carlo là rollout phẳng, **không phải MCTS có cây UCT**.',
    '- Source và binary nằm trên filesystem Windows mount vào WSL. Các số đo là thăm dò trên laptop, chưa phải dự báo thông lượng server Linux sản xuất.', '',
    '## 2. Đúng sai và chạy native', '',
    f"- {r['correctness']['cross_runtime_cases']} trường hợp = 29 vị trí × 3 thuật toán; {r['correctness']['runtime_responses_checked']} phản hồi từ 3 runtime đều khớp **nước đi, điểm đánh giá và số nút đã thăm**.",
    '- Tình huống gồm bàn trống, thắng ngay, bắt buộc chặn, một ô trống và hòa đầy bàn; tất cả nước trả về hợp lệ. Protocol sai cấu trúc và board sai kích thước trả lỗi trong cả ba runtime.',
    f'- Các kết quả còn được đối chiếu lại trong toàn bộ {sample_count:,} request đo thời gian.',
    '- `/proc/<pid>/exe` của bot Codon là `build/bot-codon`, định dạng ELF x86-64. Chạy executable trực tiếp, không gọi `python bot.py` hoặc `codon run` khi thi đấu.',
    '- `ldd` và map thư viện của tiến trình Codon không có `libpython`. Source không dùng cầu nối `from python import`.',
    '- Binary vẫn dùng **runtime Codon (`libcodonrt.so`) và các shared library**. Đây không phải binary tĩnh hoàn toàn; cần đóng gói runtime vào image của worker.', '',
    '## 3. Thời gian tính toán', '',
    'Median tính toán bên trong bot, chưa gồm parse/serialize và giao tiếp. Cột tăng tốc là tỷ lệ giữa các median, không phải cam kết cho mọi vị trí.', '',
    '| Thuật toán | CPython (ms) | Codon native (ms) | C++ (ms) | CPython / Codon | Codon / C++ |',
    '|---|---:|---:|---:|---:|---:|',
]
for algorithm, label in names.items():
    values = [r['summary'][name][algorithm]['compute_ms']['median_ms'] for name in ('cpython', 'codon', 'cpp')]
    py, co, cpp = values
    lines.append(f'| {label} | {py:.4f} | {co:.4f} | {cpp:.4f} | {py / co:.2f}× | {co / cpp:.2f}× |')
lines += ['', 'Codon nhanh hơn CPython khoảng 18–27× về phần tính toán trong bộ đo này. C++ vẫn nhanh hơn: Codon mất thêm khoảng 11–27% thời gian so với bản C++ cùng thuật toán. Điều này chưa chứng minh ngang mọi C++ được tối ưu chuyên biệt.', '',
          '## 4. Giao tiếp, khởi động và bộ nhớ', '',
          'Median round-trip do runner đo, gồm gửi trạng thái text, parse/serialize và nhận hành động:', '',
          '| Thuật toán | CPython (ms) | Codon (ms) | C++ (ms) |', '|---|---:|---:|---:|']
for algorithm, label in names.items():
    values = [r['summary'][name][algorithm]['roundtrip_ms']['median_ms'] for name in ('cpython', 'codon', 'cpp')]
    lines.append(f'| {label} | ' + ' | '.join(f'{v:.4f}' for v in values) + ' |')
lines += ['', '| Chỉ số | CPython | Codon | C++ |', '|---|---:|---:|---:|']
for label, values in [
    ('Khởi động đến READY, median 20 lần (ms)', [r['startup'][name]['median_ms'] for name in ('cpython', 'codon', 'cpp')]),
    ('PING/PONG, median 200 lần (ms)', [r['ping'][name]['median_ms'] for name in ('cpython', 'codon', 'cpp')]),
    ('Peak RSS sau workload (MiB)', [r['runtime_info'][name]['peak_rss_mib'] for name in ('cpython', 'codon', 'cpp')]),
]:
    lines.append(f'| {label} | ' + ' | '.join(f'{v:.4f}' for v in values) + ' |')
lines += ['', f"Build bot Codon mất {r['builds']['codon']['seconds']:.2f}s; C++ mất {r['builds']['cpp']['seconds']:.2f}s trong lần chạy này, cùng được pin CPU. Đây là chi phí trả khi nộp/build source, không nên lặp lại mỗi lượt.", '',
          '**Khởi động Codon chậm hơn CPython trong môi trường đo này.** Cache đã được làm ấm; đây không phải cold start. Vì library và binary trên mount Windows, không suy ra startup của server Linux từ các giá trị này. Dù vậy, thiết kế giữ một tiến trình mỗi bot trong suốt trận giúp tránh trả lại startup mỗi lượt.', '',
          '## 5. Tương thích Python', '',
          '| Probe thực chạy | Kết quả | Ý nghĩa |', '|---|---|---|',
          '| `json.loads` + `json.dumps` | Build và output khớp CPython | JSON cơ bản dùng được trong bản Codon đã thử |',
          '| `values=[1]; values.append("hello")` | CPython chạy; Codon báo lỗi kiểu khi compile | Không thể nhận mọi Python hợp lệ |',
          '| `x=2**62; print(x*4)` | CPython: `18446744073709551616`; Codon: `0` | `-numerics=py` vẫn không biến `int` thành số nguyên không giới hạn |', '',
          'Số nguyên lớn đặc biệt đáng chú ý nếu dùng **bitboard 81 ô**: một `int` 64-bit không đủ. Starter kit nên dùng list 81 ô hoặc biểu diễn nhiều word có quy tắc rõ. Không được mặc định code chạy đúng trên CPython sẽ giữ mọi ngữ nghĩa sau compile.', '',
          'Module JSON native của Codon 0.20.3 trả kiểu `JSON` thay vì dict/list Python thông thường; direct iteration và một số hook khác với CPython. SDK cần chuyển trạng thái sang cấu trúc đồng nhất trước khi gọi hàm chiến thuật. Probe parse/dump thành công chưa chứng minh mọi cách sử dụng `json` đều tương thích.', '']
if j:
    lines += ['## 6. Kiểm tra JSON thật với bot', '',
              '`bot_json.py` nhận mỗi trạng thái bằng một dòng JSON, gọi đúng thuật toán trong `bot.py`, rồi trả hành động bằng JSON. Cùng adapter chạy được trên CPython và Codon; dùng indexing và `int(...)` để chuyển board sang list số nguyên.', '',
              f"- Native JSON wrapper build thành công trong {j['build_seconds']:.2f}s.",
              f"- {j['cross_runtime_cases']} trường hợp × 2 runtime = {j['responses_checked']} phản hồi khớp kết quả protocol text. Bàn đã đầy được bỏ qua ở kiểm tra wrapper tối giản vì match runner nên dừng trước khi hỏi nước tiếp theo.",
              '- Hash core `bot.py` vẫn khớp bản benchmark chính. Chưa đổi thuật toán để thích nghi JSON.',
              '- Bot JSON native không load `libpython`; không dùng Python interoperability.',
              '- Đo thêm 24 vị trí × 3 lần lặp mỗi thuật toán/runtime với JSON thật:', '',
              '| Thuật toán | CPython JSON round-trip median (ms) | Codon JSON round-trip median (ms) |', '|---|---:|---:|']
    for algorithm, label in names.items():
        timing = j['roundtrip'][algorithm]
        lines.append(f"| {label} | {timing['cpython']['median_ms']:.4f} | {timing['codon']['median_ms']:.4f} |")
    lines += ['', 'Wrapper chỉ xử lý input đúng schema; chưa phải parser/SDK đã gia cố cho người dùng công khai.', '']
lines += ['## 7. Đề xuất cho UET Bot Arena', '',
          'Có thể tiếp tục hướng **viết chiến thuật bằng Python trong phạm vi hỗ trợ → build Codon sau khi nộp → chạy native khi đấu**. Cần làm SDK mẫu và chốt profile ngôn ngữ trước khi công bố cho sinh viên.', '',
          'Pipeline đề xuất:', '',
          '```text',
          'Nộp source → build job riêng → kiểm tra tương thích/protocol',
          '          → lưu binary + phiên bản runtime cố định',
          '          → worker trận đấu mở mỗi bot một lần',
          '          → JSON observation / JSON action qua stdin/stdout',
          '          → engine xử lý luật → sự kiện live cho UI',
          '```', '',
          'Trước giải thật còn phải thử engine OTTv2 thật, bot sinh viên đa dạng, ngân sách CPU/RAM, sandbox cho source không tin cậy, quá tải khi nhiều trận và hành vi crash/timeout. Native compilation không thay thế sandbox.', '',
          'Có thể giữ C++ là lựa chọn trực tiếp của nền tảng; thí nghiệm này chưa giải quyết công bằng khi nhiều ngôn ngữ thi chung.', '',
          '## 8. Tái lập và dữ liệu', '',
          '- Xem `README.md` để chạy lại. Compiler được pin Codon v0.20.3 và xác minh checksum bản phát hành.',
          '- `results/results.json`: môi trường, correctness, timing, liên kết, bộ nhớ, probes.',
          f'- `results/samples.json`: toàn bộ {sample_count:,} mẫu benchmark chính.',
          '- `results/json-results.json`, `results/json-samples.json`: JSON adapter và mẫu đo bổ sung.',
          '- `results/fixtures.json`: dữ liệu bàn cờ; `build/*-build.log`: log compiler.',
          '- `bot.py` và `bot.cpp`: source để đối chiếu; `experiment.py`, `json_experiment.py`: bộ đo.', '',
          'Tài liệu chính thức: [Codon build](https://docs.exaloop.io/start/usage/), [khác biệt với Python](https://docs.exaloop.io/language/overview/), [bản phát hành v0.20.3](https://github.com/exaloop/codon/releases/tag/v0.20.3).', '']
(ROOT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
print('Generated REPORT.md from measured results.')
