"""Send one isolated SMTP test through configured providers; never prints the code or secrets."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1] / "arena-backend"
sys.path.insert(0, str(ROOT))
from arena.store import Store
from arena_api.mailer import Mailer
from arena_api.outbox import MailQueue
from arena_api.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.email_providers:
        parser.error("Cần cấu hình [[email.providers]] trước khi thử SMTP")
    # An operator's SMTP check must not dispatch anybody else's pending mail or change accounts.
    with tempfile.TemporaryDirectory(prefix="arena-email-smoke-") as directory:
        store = Store(directory, recover_jobs=False)
        try:
            queue = MailQueue(store, settings)
            request = queue.enqueue(args.to)
            Mailer(queue).once()
            row = queue.get(request["id"])
            print(json.dumps({k: row[k] for k in ("status", "provider", "attempts", "sent_ms", "error")}, ensure_ascii=False))
            if row["status"] != "sent":
                raise SystemExit(1)
        finally:
            store.db.close()


if __name__ == "__main__":
    main()
