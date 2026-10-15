export const libraryChecks = {
  "compiler": "0.20.3",
  "updated_at": "2026-10-10T08:09:56.297141+00:00",
  "rows": [
    {
      "feature": "heapq-dijkstra",
      "label": "heapq · heappush/heappop",
      "note": "Dijkstra với heap các tuple (cost, node)",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "deque-bfs",
      "label": "collections.deque",
      "note": "BFS với append/popleft",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "bisect",
      "label": "bisect",
      "note": "bisect_left/right và insort",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "itertools",
      "label": "itertools",
      "note": "combinations, permutations, product(repeat=2)",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "counter-defaultdict",
      "label": "Counter / defaultdict",
      "note": "Đếm số nguyên; gom nhóm bằng defaultdict(list)",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "lru-cache",
      "label": "functools.lru_cache",
      "note": "Không có API này trong bản thử; dùng cache dict đã kiểm tra",
      "passed": false,
      "diagnostic": "probe_lru_cache.py:1 (23-32): error: cannot import name 'lru_cache' from 'functools'\n"
    },
    {
      "feature": "math",
      "label": "math.isqrt",
      "note": "Không có isqrt trong bản thử",
      "passed": false,
      "diagnostic": "probe_math.py:1 (23-28): error: cannot import name 'isqrt' from 'math'\n"
    },
    {
      "feature": "math-gcd-comb",
      "label": "math.comb",
      "note": "Không có comb trong bản thử",
      "passed": false,
      "diagnostic": "probe_math_gcd_comb.py:1 (23-27): error: cannot import name 'comb' from 'math'\n"
    },
    {
      "feature": "math-gcd-sqrt",
      "label": "math.gcd / sqrt",
      "note": "UCLN và căn bậc hai",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "dict-memoization",
      "label": "Memoization bằng dict",
      "note": "Fibonacci đệ quy có cache",
      "passed": true,
      "diagnostic": ""
    },
    {
      "feature": "numpy-array",
      "label": "NumPy native",
      "note": "Array int, nhân từng phần tử và np.sum",
      "passed": true,
      "diagnostic": ""
    }
  ]
};
