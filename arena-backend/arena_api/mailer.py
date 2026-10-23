"""Separate SMTP worker: python -m arena_api.mailer [--once | --init-key]."""
import argparse
from email.message import EmailMessage
from email.utils import formataddr, formatdate
import json
import logging
import os
import secrets
import signal
import smtplib
import ssl
import threading

from arena.conf import ConfigError, Secrets, secrets_path
from arena.protocol import Fault
from arena.store import Store
from arena_api.outbox import MailQueue
from arena_api.settings import Lease, Settings

log = logging.getLogger("arena.mailer")


def message(provider, job, code, current, ttl_seconds=600):
    mail = EmailMessage()
    mail["From"] = formataddr((provider.sender_name, provider.sender))
    mail["To"] = job["recipient"]
    mail["Subject"] = "Mã xác minh UET Bot Arena: " + code
    mail["Date"] = formatdate(current / 1000, usegmt=True)
    mail["Message-ID"] = f"<{job['id']}.{secrets.token_hex(8)}@{provider.sender.rsplit('@', 1)[1]}>"
    ttl = f"{ttl_seconds // 60} phút" if ttl_seconds % 60 == 0 else f"{ttl_seconds} giây"
    text = f"Mã xác minh UET Bot Arena của bạn là: {code}\n\nMã có hiệu lực {ttl} kể từ lúc gửi.\nNếu bạn không đăng ký, hãy bỏ qua thư này.\n"
    mail.set_content(text)
    mail.add_alternative(f"<html lang='vi'><body><p>Mã xác minh UET Bot Arena của bạn:</p><p><strong>{code}</strong></p>"
                         f"<p>Mã có hiệu lực {ttl} kể từ lúc gửi.</p><p>Nếu bạn không đăng ký, hãy bỏ qua thư này.</p></body></html>", subtype="html")
    return mail


def smtp_send(provider, credentials, mail):
    context = ssl.create_default_context()
    client = smtplib.SMTP_SSL(provider.host, provider.port, timeout=provider.timeout_seconds, context=context) if provider.tls == "ssl" else smtplib.SMTP(provider.host, provider.port, timeout=provider.timeout_seconds)
    with client:
        client.ehlo()
        if provider.tls == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if provider.user_secret:
            client.login(credentials.get(provider.user_secret), credentials.get(provider.password_secret))
        refused = client.send_message(mail)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


class Mailer:
    def __init__(self, queue, send=smtp_send):
        self.queue, self.send = queue, send
        # Fail before claiming anything if the installation has no durable HMAC key.
        key = queue.settings.secrets.get("verification_hmac_key", "mailer")
        if len(key) < 32:
            raise ConfigError("verification_hmac_key cần ít nhất 32 ký tự")

    def once(self):
        queue = self.queue
        job = queue.claim()
        if not job:
            return False
        code, error, available = None, None, []
        try:
            for provider in queue.settings.email_providers:
                attempt, due = queue.reserve(job, provider)
                if not attempt:
                    available.append(due)
                    continue
                if code is None:
                    code = queue.new_code(job)
                try:
                    self.send(provider, queue.settings.secrets, message(provider, job, code, queue.clock(), queue.settings.verification.code_ttl_seconds))
                except (smtplib.SMTPException, OSError) as exc:
                    auth_error = isinstance(exc, smtplib.SMTPAuthenticationError)
                    error = "smtp_auth" if auth_error else "smtp_" + str(exc.smtp_code) if isinstance(exc, smtplib.SMTPResponseException) else "smtp_connection"
                    queue.attempt_done(job, provider, attempt, error, auth_error)
                    available.append(queue.availability(provider)["available_ms"])
                    log.warning("Không gửi được qua %s: %s", provider.name, error)
                    continue
                queue.attempt_done(job, provider, attempt)
                return True
            attempts = queue.get(job["id"])["attempts"]
            backoff = min(300_000, 5_000 * 2 ** min(attempts, 6))
            due = max(queue.clock() + backoff, min(available, default=queue.clock() + 60_000))
            queue.defer(job, due, error or (None if available else "no_provider"))
        except Fault as exc:
            if exc.code != "mail_claim_lost":
                raise
        return True


def init_key():
    path, _ = secrets_path()
    existing = Secrets.load(path)
    if existing.has("verification_hmac_key"):
        print("Khóa HMAC đã có; giữ nguyên.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\nverification_hmac_key = " + json.dumps(secrets.token_urlsafe(32)) + "\n")
    print("Đã tạo verification_hmac_key trong file bí mật; không in giá trị.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Nhận tối đa một yêu cầu rồi thoát")
    parser.add_argument("--init-key", action="store_true", help="Tạo khóa HMAC nếu chưa có, không đổi khóa cũ")
    args = parser.parse_args()
    if args.init_key:
        init_key()
        return
    settings = Settings()
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    # A separate lock from the hub; the queue still uses SQL claims to fence abandoned work.
    lease = Lease(settings.data / "mailer")
    store = Store(settings.data, recover_jobs=False)
    try:
        worker = Mailer(MailQueue(store, settings))
        while not stopping.is_set():
            worked = worker.once()
            if args.once:
                break
            if not worked:
                stopping.wait(1)
    finally:
        store.db.close()
        lease.close()


if __name__ == "__main__":
    try:
        main()
    except (ConfigError, Fault) as exc:
        raise SystemExit(str(exc)) from None
