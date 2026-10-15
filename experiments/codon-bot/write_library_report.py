import json
import re
from experiment import ROOT, RESULTS

data = json.loads((RESULTS / 'library-results.json').read_text())
labels = {
    'heapq-dijkstra': ('heapq · heappush/heappop', 'Dijkstra với heap các tuple (cost, node)'),
    'deque-bfs': ('collections.deque', 'BFS với append/popleft'),
    'bisect': ('bisect', 'bisect_left/right và insort'),
    'itertools': ('itertools', 'combinations, permutations, product(repeat=2)'),
    'counter-defaultdict': ('Counter / defaultdict', 'Đếm số nguyên; gom nhóm bằng defaultdict(list)'),
    'lru-cache': ('functools.lru_cache', 'Không có API này trong bản thử; dùng cache dict đã kiểm tra'),
    'math': ('math.isqrt', 'Không có isqrt trong bản thử'),
    'math-gcd-comb': ('math.comb', 'Không có comb trong bản thử'),
    'math-gcd-sqrt': ('math.gcd / sqrt', 'UCLN và căn bậc hai'),
    'dict-memoization': ('Memoization bằng dict', 'Fibonacci đệ quy có cache'),
    'numpy-array': ('NumPy native', 'Array int, nhân từng phần tử và np.sum'),
}
rows = []
lines = ['# Kiểm chứng thư viện thuật toán với Codon', '',
         'Đã compile/run các snippet bằng Codon **0.20.3** (`-release -numerics=py`) và đối chiếu stdout với CPython **3.12.3** trong cùng WSL. Không dùng `from python import`, decorator `@python` hay CPython bridge.', '',
         '**Mỗi kết quả chỉ áp dụng cho đoạn code được kiểm tra**, không chứng nhận toàn bộ thư viện. Kết quả build fail là giới hạn của bản compiler được cố định, không phải khẳng định mọi bản Codon đều thiếu API đó.', '',
         '| API / cách dùng | Kết quả | Phạm vi |', '| --- | --- | --- |']
for probe in sorted(data['probes'], key=lambda p: list(labels).index(p['feature'])):
    title, note = labels[probe['feature']]
    passed = probe['matches_cpython']
    diagnostic = re.sub(r'\x1b\[[0-9;]*m', '', probe['compile_stderr'])
    rows.append({'feature': probe['feature'], 'label': title, 'note': note, 'passed': passed, 'diagnostic': diagnostic[-1000:]})
    lines.append(f"| {title} | {'Native, cùng output' if passed else 'Build fail'} | {note} |")
lines += ['', '## Ý nghĩa cho giải', '',
          '- Đề xuất SDK/allowlist nhỏ, cố định compiler image và phát bộ kiểm tra cùng source cho đội. Cho đội chạy preflight trước hạn nộp để thấy lỗi ở đúng dòng.',
          '- Các thuật toán tự viết bằng list/dict/set cùng kiểu, heap, queue và itertools có thể dùng native qua những API đã kiểm chứng.',
          '- Thư viện PyPI ngoài danh sách này chưa được thử. Codon có cầu nối gọi Python, nhưng phần chạy qua cầu nối vẫn phụ thuộc CPython; không coi toàn bot là native độc lập. NetworkX/SciPy không được chứng nhận native bởi thí nghiệm này.',
          '- NumPy được kiểm tra ở mức array và reduction nhỏ; chưa chứng nhận toàn API hoặc hiệu năng NumPy cho từng bot.',
          '- Đã thấy khác biệt int64 và list trộn kiểu trong thử nghiệm trước: xem REPORT.md. Các bitboard hơn 64 bit cần biểu diễn phù hợp.', '',
          '## Tái lập', '', '```bash', 'python3 library_experiment.py', 'python3 write_library_report.py', '```', '',
          'Code từng snippet, SHA-256, thời gian build, output CPython/native và log lỗi ở `results/library-results.json`.', '',
          'Tài liệu: [stdlib](https://docs.exaloop.io/libraries/stdlib/), [NumPy](https://docs.exaloop.io/libraries/numpy/), [Python bridge](https://docs.exaloop.io/integrations/python/python-from-codon/).', '']
(ROOT / 'LIBRARIES.md').write_text('\n'.join(lines), encoding='utf-8')
target = ROOT.parents[1] / 'uet-bot-arena/dist/library-data.js'
target.write_text('export const libraryChecks = ' + json.dumps({'compiler': data['codon_version'], 'updated_at': data['measured_at'], 'rows': rows}, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')
print('Exported LIBRARIES.md and library-data.js')
