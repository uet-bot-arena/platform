"""Mô hình dung lượng cho N = 1000/2000 bài nộp, dùng số đo thật từ results/*.json.

    python tests/load_lab/capacity_model.py [--build-s 8] [--bench-s 9] [--match-s 3] [--per-machine 10]

Không phải mô phỏng chính xác: là phép tính "cần bao nhiêu giờ máy" cho từng thể thức,
để chọn hướng kiến trúc. Số đo mặc định được ghi đè bằng kết quả lab nếu có.
"""
import argparse
import glob
import json
import math
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "test-results" / "load-lab" / "results"


def latest(pattern):
    files = sorted(glob.glob(str(RESULTS / pattern)))
    return json.loads(Path(files[-1]).read_text(encoding="utf-8")) if files else None


def formats(n):
    rounds = math.ceil(math.log2(n)) + 2
    return [
        ("Vòng tròn 2 lượt (repo hiện tại)", n * (n - 1)),
        (f"Swiss {rounds} vòng, mỗi cặp đánh 2 ván đổi màu", n * rounds),
        ("Chia bảng 8 đội (vòng tròn 2 lượt) + loại trực tiếp top 2", (n // 8) * 56 + 2 * (n // 4)),
        ("Ladder: mỗi bài đấu 20 ván với đối thủ ngẫu nhiên", n * 20 // 2),
    ]


def hours(jobs, seconds, workers):
    return jobs * seconds / workers / 3600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-s", type=float)
    ap.add_argument("--bench-s", type=float)
    ap.add_argument("--match-s", type=float)
    ap.add_argument("--per-machine", type=int, help="số trận song song hiệu quả trên 1 máy")
    args = ap.parse_args()
    e2e, scaling, league = latest("e2e-2*.json"), latest("algo-scaling-*.json"), latest("algo-league-*.json")
    build = args.build_s or ((e2e or {}).get("submission_phase", {}).get("build_s", {}).get("median")) or 8.0
    bench = args.bench_s or ((e2e or {}).get("submission_phase", {}).get("benchmark_samples_s", {}).get("median")) or 3.0
    match = args.match_s or ((league or {}).get("match_s", {}).get("median")) or 3.0
    best = max((scaling or {}).get("runs", []), key=lambda r: r["matches_per_hour"], default=None)
    single = min((scaling or {}).get("runs", []), key=lambda r: r["workers"], default=None)
    per_machine = args.per_machine or (round(best["matches_per_hour"] / single["matches_per_hour"], 1) if best and single else 8)
    worst = 2 * 60 + 3          # cả hai bot dùng hết quỹ Bullet 60 s + 3 s chờ
    print(f"Số đo dùng: build {build}s, chạy benchmark {bench}s, trận median {match}s, trận xấu nhất (Bullet) {worst}s,"
          f" 1 máy ≈ {per_machine}x thông lượng của 1 worker\n")
    for n in (1000, 2000):
        print(f"=== N = {n} bài nộp ===")
        sub_h = hours(n, build + bench + 2, 1)
        print(f"  Kiểm bài (build+benchmark), 1 worker như repo: {sub_h:.1f} giờ; 1 máy {per_machine}x: {sub_h / per_machine:.1f} giờ")
        print(f"  {'Thể thức':<58}{'số trận':>10}{'1 worker':>12}{'1 máy':>10}{'1 máy, trận xấu nhất':>22}")
        for name, games in formats(n):
            one = hours(games, match, 1)
            m1 = one / per_machine
            print(f"  {name:<58}{games:>10,}{one:>11.1f}h{m1:>9.1f}h{hours(games, worst, per_machine):>21.1f}h")
        print()


if __name__ == "__main__":
    main()
