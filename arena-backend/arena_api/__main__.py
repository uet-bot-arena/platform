import argparse
import getpass
import sys

from arena.store import Store
from arena_api.accounts import Accounts
from arena_api.settings import Lease, Settings


def main():
    parser = argparse.ArgumentParser(description="UET Bot Arena web backend")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("migrate", help="Apply SQL migrations while the controller is stopped")
    admin = sub.add_parser("bootstrap-admin", help="Create admin offline; no default account/password")
    admin.add_argument("--username", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="Read one password line from stdin; never from argv")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=4181)
    args = parser.parse_args()
    settings = Settings()
    if args.action == "serve":
        import uvicorn
        uvicorn.run("arena_api.app:create_app", factory=True, host="127.0.0.1", port=args.port, workers=1,
                    access_log=False, proxy_headers=False, limit_concurrency=32,
                    timeout_keep_alive=5, timeout_graceful_shutdown=400)
        return
    lease, store = Lease(settings.data), None
    try:
        store = Store(settings.data, recover_jobs=False)
        if args.action == "migrate":
            from arena_api.migrations import migrate
            migrate(store)
            print("Database migrations applied")
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
