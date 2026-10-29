import secrets
from fastapi import Request
from arena.protocol import Fault

COOKIE = "arena_web_session"

# These GET operations share the same filtered view for teams and guests.
SPECTATOR_PATHS = {"/api/v1/competitions/{ident}/fixtures/{fixture_id}/live-interest", "/api/v1/competitions/{ident}/elo/export", "/api/v1/competitions", "/api/v1/competitions/{ident}",
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
        if exc.code in ("unauthorized", "password_change_required", "rate_limited"):
            return False
        raise
    return auth["user"]["role"] == "admin"


def principal(request: Request):
    automatic = request.headers.get('X-Arena-Poll') == '1' or 'text/event-stream' in request.headers.get('accept','') or request.url.path.endswith('/watch')
    auth = request.app.state.accounts.principal(request.cookies.get(COOKIE, ""), allow_blocked=request.url.path == "/api/v1/auth/logout", refresh=not automatic)
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


def participant_principal(request: Request):
    value = active_principal(request)
    if value["user"]["role"] == "admin":
        raise Fault("forbidden", "Tài khoản cá nhân hoặc khách của đội được yêu cầu")
    return value


def team_principal(request: Request):
    value = participant_principal(request)
    if not value["user"]["team_id"]:
        raise Fault("forbidden", "Hãy tạo hoặc vào một đội trước")
    with request.app.state.store.lock:
        team = request.app.state.accounts.team(value["user"]["team_id"])
    if team["archived_ms"]:
        raise Fault("forbidden", "Đội đã giải tán hoặc bị lưu trữ")
    return value


def admin_principal(request: Request):
    value = active_principal(request)
    if value["user"]["role"] != "admin":
        raise Fault("forbidden", "Administrator required")
    return value


def practice_principal(request: Request):
    value = active_principal(request)
    if value['user']['role']=='admin':
        return {**value, 'owner_id': 'user:' + value['user']['id']}
    if value['user']['team_id']:
        value = team_principal(request)
    return {**value, 'owner_id': value['user']['team_id'] or 'user:' + value['user']['id']}


def can_manage_graders(request, auth):
    allowed = request.app.state.settings.account_rules.grader_admins
    return auth['user']['role'] == 'admin' and (not allowed or auth['user']['username'].casefold() in allowed)


def grader_admin_principal(request: Request):
    auth = admin_principal(request)
    if not can_manage_graders(request, auth):
        raise Fault('forbidden', 'Chỉ tài khoản quản lý máy chấm được làm việc này')
    return auth


def peer(request):
    import ipaddress
    host = request.client.host if request.client else "unknown"
    try:
        networks = [ipaddress.ip_network(n.strip()) for n in request.app.state.settings.trusted_proxies]
        current = ipaddress.ip_address(host)
        # Walk from the socket peer, stopping at the first untrusted hop.
        for value in reversed(request.headers.get("x-forwarded-for", "").split(",")):
            if not any(current in network for network in networks):
                break
            if not value.strip():
                break
            current = ipaddress.ip_address(value.strip())
        return str(current)
    except ValueError:
        return host


def set_session(request, response, result):
    settings = request.app.state.settings
    response.set_cookie(COOKIE, result["token"], httponly=True, secure=settings.cookie_secure,
                        samesite="strict", path="/api", max_age=settings.admin_session_seconds if result['user']['role']=='admin' else settings.session_seconds)
    return {**{key: value for key, value in result.items() if key != "token"},
            'can_manage_graders': can_manage_graders(request, result)}
