import secrets
from fastapi import Request
from arena.protocol import Fault

COOKIE = "arena_web_session"

# These GET operations share the same filtered view for teams and guests.
SPECTATOR_PATHS = {"/api/v1/competitions", "/api/v1/competitions/{ident}",
                   "/api/v1/competitions/{ident}/fixtures", "/api/v1/competitions/{ident}/standings",
                   "/api/v1/competitions/{ident}/watch",
                   "/api/v1/competitions/{ident}/fixtures/{fixture_id}/events"}


def spectator_admin(request: Request):
    """An optional session can grant admin visibility, never block public viewing.

    Expired/revoked cookies act as guests on these read-only endpoints. Private
    endpoints still use active_principal and reject the same cookies.
    """
    if not request.cookies.get(COOKIE):
        return False
    try:
        auth = active_principal(request)
    except Fault as exc:
        if exc.code in ("unauthorized", "password_change_required"):
            return False
        raise
    return auth["user"]["role"] == "admin"


def principal(request: Request):
    auth = request.app.state.accounts.principal(request.cookies.get(COOKIE, ""))
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(csrf.encode(), auth["csrf_token"].encode()):
            raise Fault("forbidden", "Valid X-CSRF-Token required")
    return auth


def active_principal(request: Request):
    value = principal(request)
    if value["user"]["must_change_password"]:
        raise Fault("password_change_required", "Change the temporary password before using team APIs")
    return value


def team_principal(request: Request):
    value = active_principal(request)
    if value["user"]["role"] != "team":
        raise Fault("forbidden", "Team account required")
    return value


def admin_principal(request: Request):
    value = active_principal(request)
    if value["user"]["role"] != "admin":
        raise Fault("forbidden", "Administrator required")
    return value


def peer(request):
    # Never trust user-supplied X-Forwarded-For. Gateway limits are separate.
    return request.client.host if request.client else "unknown"


def set_session(request, response, result):
    settings = request.app.state.settings
    response.set_cookie(COOKIE, result["token"], httponly=True, secure=settings.cookie_secure,
                        samesite="strict", path="/api", max_age=settings.session_seconds)
    return {key: value for key, value in result.items() if key != "token"}
