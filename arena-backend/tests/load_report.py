"""Render the last measured run; contains no invented capacity or latency."""
import json
from datetime import datetime
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "test-results"


def write_report(report):
    load = report.get("load", [])
    names = {"mixed reads": "Đọc API hỗn hợp", "bounded sustained reads": "Duy trì tải ngắn",
             "admission overload": "Vượt giới hạn kết nối", "recovery after overload": "Sau quá tải",
             "API with 12 live SSE viewers": "API + 12 kết nối SSE",
             "standings and replay after restart": "BXH/replay sau restart"}
    rows = []
    for item in load:
        statuses = ", ".join(f"{k}: {v}" for k, v in sorted(item["statuses"].items()))
        rows.append(f"| {names.get(item['name'], item['name'])} | {item['clients']} | {item['requests']} | "
                    f"{item['requests_per_second']} | {item['p95_ms']} | {item['p99_ms']} | {statuses} |")
    resources = report.get("resources", [])
    peak = max((r["peak_rss_kib"] for r in resources), default=0) / 1024
    checks = "\n".join(f"- {'Đạt' if c['passed'] else 'Không đạt'}: `{c['name']}`" for c in report.get("checks", []))
    normal = [item for item in load if not item["expected_overload_503"]]
    count = sum(item["requests"] for item in normal)
    errors = sum(sum(item["unexpected_errors"].values()) for item in load)
    content = f"""# Kết quả kiểm thử tải và sự cố

Kết quả: **{'ĐẠT' if report.get('passed') else 'CHƯA ĐẠT'}** — {len(report.get('checks', []))} kiểm tra đã hoàn tất.

Bắt đầu: {report['started_local']}. Kết thúc: {report.get('completed_local', 'chưa có')} (Asia/Bangkok).

Server CLI thật trên database tạm với 6 đội giả lập; WSL {report['environment']['logical_cpus']} logical CPU, Python {report['environment']['python']}. Một controller, một worker, admission giới hạn 32. Không dùng server 41810 hoặc dữ liệu đội/giải thật. Image: `{report['image_id']}`.

## Tải đo được

| Pha | Client đồng thời | Request | Request/s | P95 ms | P99 ms | HTTP: số lượng |
|---|---:|---:|---:|---:|---:|---|
{chr(10).join(rows)}

Tổng request đọc ngoài pha overload: **{count}**; số lỗi HTTP/transport ngoài dự kiến của toàn bộ các pha đọc: **{errors}**. HTTP 503 ở pha overload là request bị từ chối, không phải request thành công. P95/P99 ở pha đó bao gồm cả phản hồi từ chối; không dùng throughput pha đó để ước lượng công suất xử lý thành công.

Peak RSS controller ghi nhận: **{peak:.1f} MiB** (VmHWM, lấy mẫu theo pha); không bao gồm client test và Docker worker. Các pha dùng client closed-loop trên loopback, dữ liệu nhỏ, không keep-alive; chưa có SLO latency được định nghĩa. Mức tải này không chứng minh số người dùng production hoặc độ ổn định nhiều giờ.

## Kết quả chức năng và phục hồi

{checks}

Thu dọn: `{json.dumps(report.get('cleanup', {}), ensure_ascii=False)}`.

Harness chủ động thu hồi container thuộc thư mục test sau SIGKILL. Số lượng/trạng thái trước khi thu hồi có trong JSON; không dùng bài thử này để khẳng định hệ thống tự thu dọn Docker sau crash.

## Thay đổi phát hiện từ kiểm thử

Client UI đã phân biệt lỗi 503 văn bản do Uvicorn từ chối admission với lỗi nhập liệu, hiển thị “Máy chủ đang bận”. Tải file cũng xử lý được body lỗi không phải JSON. 503 có mã worker_unavailable vẫn báo chẩn đoán Docker. Không tự retry mutation. Bốn kiểm tra hồi quy client đã được thêm; UI được xác nhận trên fixture lỗi riêng.

## Chạy lại và giới hạn

Chạy `make load-test` trong arena-backend trên Linux/WSL. Chi tiết: [LOAD-RESILIENCE.md](../docs/LOAD-RESILIENCE.md).

Chưa kiểm thử soak nhiều giờ, đầy ổ đĩa, mất host/daemon Docker, proxy/TLS, nhiều máy hoặc dữ liệu production lớn. Report JSON gốc: [load-resilience-latest.json](load-resilience-latest.json).
"""
    if report.get("failure"):
        content += "\nLỗi dừng test:\n\n```json\n" + json.dumps(report["failure"], ensure_ascii=False, indent=2) + "\n```\n"
    if report.get("cleanup_failure"):
        content += "\nLỗi thu dọn:\n\n```json\n" + json.dumps(report["cleanup_failure"], ensure_ascii=False, indent=2) + "\n```\n"
    integration_path = OUTPUT / "latest.json"
    if integration_path.is_file():
        integration = json.loads(integration_path.read_text(encoding="utf-8"))
        started_ms = datetime.fromisoformat(report["started_local"]).timestamp() * 1000
        if (integration.get("passed") and integration.get("image_id") == report["image_id"]
                and integration.get("completed_unix_ms", 0) >= started_ms):
            timing = next((c for c in integration["checks"] if "grace_monotonic_ms" in c), None)
            if timing:
                content += (f"\n## Docker integration bổ sung sau đợt tải\n\n"
                    f"Đạt {len(integration['checks'])}/{len(integration['checks'])} mục, gồm hard deadline, off-turn freeze, "
                    f"fork/network/signal bị chặn và replay/SSE resume. Khoảng chờ tuyên bố timeout đo bằng monotonic: "
                    f"**{timing['grace_monotonic_ms']:.3f} ms**; hiệu timestamp UTC hiển thị: "
                    f"{timing['grace_display_clock_ms']} ms. Action trễ không được chấp nhận. "
                    f"Đã bổ sung telemetry grace để tránh dùng UTC làm phép kiểm thời gian. "
                    f"Báo cáo gốc: [latest.json](latest.json).\n")
    path = OUTPUT / "load-resilience-report.md"
    path.write_text(content, encoding="utf-8")
    return path


if __name__ == "__main__":
    report = json.loads((OUTPUT / "load-resilience-latest.json").read_text(encoding="utf-8"))
    print(write_report(report))
