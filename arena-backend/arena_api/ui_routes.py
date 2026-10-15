"""Serve the API UI on the same origin; uploaded source is never a static root."""
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from starlette.responses import FileResponse, RedirectResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles

from arena import engine
from arena.adjudication import CURRENT
from arena.config import match_profile
from arena_api.dependencies import admin_principal

router = APIRouter(prefix="/api/v1", tags=["platform"])


@router.get("/platform", summary="Thông tin nền tảng công khai cho trang chủ và đăng ký")
def platform():
    return {"name": "UET Bot Arena", "version": "0.6.0", "engine_id": engine.ENGINE_ID, "adjudication": dict(CURRENT),
            "runtimes": ["cpp17"], "codon_enabled": False, "match_options_ms": [60000, 300000],
            "profile": match_profile().public(), "registration_members": {"min": 3, "max": 4}}


@router.get("/rules", summary="Văn bản luật OTTv2 chính thức đã cung cấp", response_class=FileResponse,
            responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}})
def rules():
    return FileResponse(Path(__file__).resolve().parents[1] / "docs" / "OTTV2-RULES.md", media_type="text/plain; charset=utf-8")


@router.get("/admin/dashboard", summary="Tổng quan vận hành cho admin, không trả credential/source")
def admin_dashboard(request: Request, auth=Depends(admin_principal)):
    store = request.app.state.store
    with store.lock:
        def count(table, where=""):
            return store.db.execute("SELECT COUNT(*) FROM " + table + where).fetchone()[0]
        jobs = store.list("job")
        return {"teams": count("teams"), "pending_registrations": count("registrations", " WHERE status='pending'"),
                "pending_recoveries": count("recoveries", " WHERE status='pending'"), "competitions": count("competitions"),
                "active_jobs": sum(j["status"] not in ("completed", "failed", "aborted") for j in jobs),
                "failed_jobs": sum(j["status"] == "failed" for j in jobs)}


def mount_ui(app, settings):
    root = settings.ui_root
    if (root / "live").is_dir():
        app.mount("/_ui", StaticFiles(directory=root / "live"), name="ui")

    @app.get("/", include_in_schema=False)
    def index():
        if not (root / "live" / "index.html").is_file():
            return RedirectResponse("/docs")
        return FileResponse(root / "live" / "index.html")

    @app.get("/styles.css", include_in_schema=False)
    def styles():
        # The hosted demo imports Google Fonts. The local application has a
        # self-only CSP and uses the system font, so omit that external import.
        source = (root / "styles.css").read_text(encoding="utf-8")
        return PlainTextResponse("\n".join(line for line in source.splitlines() if not line.startswith("@import")), media_type="text/css")

    @app.get("/assets/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(root / "assets" / "favicon.svg", media_type="image/svg+xml")
