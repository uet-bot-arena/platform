import argparse
import getpass
import os
import sys

from arena.conf import ConfigError
from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.settings import Lease, Settings


def main():
    parser = argparse.ArgumentParser(description="UET Bot Arena web backend")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("migrate", help="Apply SQL migrations while the controller is stopped")
    sub.add_parser('cleanup', help='Dọn retention khi hub đã dừng; hub đang chạy dùng POST /api/v1/admin/retention/run')
    publish = sub.add_parser('publish', help='Process riêng, chỉ đọc DB, xuất snapshot công khai')
    publish.add_argument('--once',action='store_true')
    admin = sub.add_parser("bootstrap-admin", help="Create admin offline; no default account/password")
    admin.add_argument("--username", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="Read one password line from stdin; never from argv")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, help="Ghi đè hub.port trong file cấu hình")
    serve.add_argument('--access-log', action='store_true', help='Ghi request/status vào journald để đếm tải; không ghi body/header')
    serve.add_argument('--read-only',action='store_true',help='Chỉ GET allowlist, SQLite mode=ro, không chạy việc nền')
    args = parser.parse_args()
    if getattr(args, "port", None):
        # The app factory builds its own Settings; pass the override through the environment.
        os.environ["ARENA_PORT"] = str(args.port)
    try:
        settings = Settings()
    except ConfigError as exc:
        parser.exit(2, f"Lỗi cấu hình: {exc}\n")
    if args.action == "serve":
        import uvicorn
        uvicorn.run("arena_api.read_worker:create_app" if args.read_only else "arena_api.app:create_app", factory=True, host=settings.bind, port=settings.port, workers=1,
                    access_log=args.access_log, proxy_headers=False, limit_concurrency=settings.connection_limit,
                    timeout_keep_alive=5, timeout_graceful_shutdown=400)
        return
    if args.action == 'publish':
        from arena_api.publisher import run
        run(settings, args.once)
        return
    lease, store = Lease(settings.data), None
    try:
        store = Store(settings.data, recover_jobs=False)
        if args.action == "migrate":
            from arena_api.migrations import migrate
            migrate(store)
            print("Database migrations applied")
        elif args.action == 'cleanup':
            import json
            from arena.service import Service
            from arena_api.retention import Retention
            from arena_api.migrations import migrate
            migrate(store)
            if store.db.execute('PRAGMA auto_vacuum').fetchone()[0] != 2:
                store.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
                store.db.execute('VACUUM')
            service = Service(store, start_worker=False, grader=settings.grader)
            print(json.dumps(Retention(service, settings.retention).run(), ensure_ascii=False))
        else:
            password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("Admin password (8..128 characters): ")
            if not args.password_stdin and password != getpass.getpass("Confirm password: "):
                parser.error("Passwords do not match")
            Accounts(store, settings.session_seconds).bootstrap_admin(args.username, password)
            print("Administrator created. No password is printed or stored in plaintext.")
    finally:
        if store:
            store.db.close()
        lease.close()


if __name__ == "__main__":
    main()
