"""Durable email requests and verification proofs; only the mailer knows plaintext codes."""
import hashlib
import hmac
import re
import secrets
import time
import uuid

from arena.protocol import Fault

HOUR, DAY = 3_600_000, 86_400_000


def now_ms():
    return time.time_ns() // 1_000_000


def university_email(value, rules):
    email = value.strip().casefold()
    if len(email) > 120 or not re.fullmatch(r"[^@\s<>]+@[a-z0-9.-]+\.[a-z]{2,}", email) or rules.block_plus and "+" in email:
        raise Fault("invalid_request", "Địa chỉ email không hợp lệ")
    if re.fullmatch(rules.student_email, email, re.IGNORECASE):
        return email
    if email.rsplit("@", 1)[1] in rules.staff_domains:
        return email
    raise Fault("invalid_request", "Hãy dùng email trường được ban tổ chức cho phép")


class MailQueue:
    def __init__(self, store, settings, clock=now_ms):
        self.store, self.db, self.settings, self.clock = store, store.db, settings, clock
        with store.lock, self.db:
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS email_outbox(
                id TEXT PRIMARY KEY, purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
                recipient TEXT NOT NULL, user_id TEXT, created_ms INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','sent','failed','dropped')),
                provider TEXT, attempts INTEGER NOT NULL DEFAULT 0, sent_ms INTEGER, error TEXT,
                next_attempt_ms INTEGER NOT NULL, claim_token TEXT, claim_until_ms INTEGER,
                code_hash TEXT, expires_ms INTEGER, wrong_codes INTEGER NOT NULL DEFAULT 0,
                verified_ms INTEGER, proof_hash TEXT, consumed_ms INTEGER);
              CREATE INDEX IF NOT EXISTS email_outbox_due ON email_outbox(status,next_attempt_ms);
              CREATE INDEX IF NOT EXISTS email_outbox_recipient ON email_outbox(recipient,purpose,created_ms);
              CREATE TABLE IF NOT EXISTS email_attempts(
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider TEXT NOT NULL,
                started_ms INTEGER NOT NULL, status TEXT NOT NULL, error TEXT);
              CREATE INDEX IF NOT EXISTS email_attempts_quota ON email_attempts(provider,started_ms);
              CREATE TABLE IF NOT EXISTS email_provider_state(
                name TEXT PRIMARY KEY, blocked_until_ms INTEGER NOT NULL, error TEXT);
            """)

    def transaction(self):
        return self.store.transaction()

    def one(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        row = cursor.fetchone()
        return dict(zip([c[0] for c in cursor.description], row)) if row else None

    def get(self, ident):
        row = self.one("SELECT * FROM email_outbox WHERE id=?", (ident,))
        if not row:
            raise Fault("not_found", "Không tìm thấy yêu cầu xác minh")
        return row

    def drop_old(self):
        current = self.clock()
        self.db.execute("UPDATE email_outbox SET status='dropped',error='request_expired',code_hash=NULL "
                        "WHERE status IN ('queued','failed') AND created_ms<=? "
                        "AND (claim_token IS NULL OR claim_until_ms<=?)",
                        (current - self.settings.verification.drop_after_seconds * 1000, current))

    def enqueue(self, recipient, purpose="verify", user_id=None):
        if purpose not in ("verify", "reset") or not re.fullmatch(r"[^@\s<>]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", recipient):
            raise Fault("invalid_request", "Địa chỉ hoặc mục đích gửi thư không hợp lệ")
        recipient, current = recipient.casefold(), self.clock()
        with self.transaction():
            self.drop_old()
            old = self.one("SELECT created_ms FROM email_outbox WHERE recipient=? AND purpose=? ORDER BY created_ms DESC LIMIT 1", (recipient, purpose))
            wait = self.settings.verification.resend_seconds * 1000
            if old and old["created_ms"] + wait > current:
                fault = Fault("rate_limited", "Vui lòng chờ trước khi gửi lại mã")
                fault.retry_after = (old["created_ms"] + wait - current + 999) // 1000
                raise fault
            ident = uuid.uuid4().hex
            self.db.execute("INSERT INTO email_outbox(id,purpose,recipient,user_id,created_ms,status,next_attempt_ms) VALUES(?,?,?,?,?,'queued',?)",
                            (ident, purpose, recipient, user_id, current, current))
        return self.status(ident)

    def provider_ready(self, provider):
        return not provider.user_secret or all(self.settings.secrets.has(key) for key in (provider.user_secret, provider.password_secret))

    def availability(self, provider):
        current = self.clock()
        day, hour = current // DAY * DAY, current // HOUR * HOUR
        used = self.one("SELECT COUNT(*) AS day,COALESCE(SUM(started_ms>=?),0) AS hour "
                        "FROM email_attempts WHERE provider=? AND started_ms>=?", (hour, provider.name, day))
        state = self.one("SELECT blocked_until_ms,error FROM email_provider_state WHERE name=?", (provider.name,)) or {}
        ready = self.provider_ready(provider)
        available = max(current, state.get("blocked_until_ms", 0))
        if used["day"] >= provider.daily_limit:
            available = max(available, day + DAY)
        if used["hour"] >= provider.hourly_limit:
            available = max(available, hour + HOUR)
        if not ready:
            available = max(available, current + 60_000)
        return {"name": provider.name, "ready": ready, "available_ms": available,
                "daily_limit": provider.daily_limit, "hourly_limit": provider.hourly_limit,
                "daily_used": used["day"], "hourly_used": used["hour"],
                "daily_remaining": max(0, provider.daily_limit - used["day"]),
                "hourly_remaining": max(0, provider.hourly_limit - used["hour"]), "error": state.get("error")}

    def status(self, ident):
        with self.transaction():
            self.drop_old()
            row = self.get(ident)
            due = max(row["next_attempt_ms"], min((self.availability(p)["available_ms"] for p in self.settings.email_providers), default=self.clock() + 60_000))
            return {k: row[k] for k in ("id", "purpose", "status", "sent_ms", "expires_ms", "verified_ms")} | {
                "estimated_sent_ms": due if row["status"] in ("queued", "failed") else None,
                "resend_at_ms": row["created_ms"] + self.settings.verification.resend_seconds * 1000,
                "resend_after_seconds": max(0, (row["created_ms"] + self.settings.verification.resend_seconds * 1000 - self.clock() + 999) // 1000),
                "attempts_remaining": max(0, self.settings.verification.max_attempts - row["wrong_codes"])}

    def claim(self):
        current = self.clock()
        with self.transaction():
            self.drop_old()
            row = self.one("SELECT * FROM email_outbox WHERE status IN ('queued','failed') AND next_attempt_ms<=? "
                           "AND (claim_token IS NULL OR claim_until_ms<=?) ORDER BY created_ms,id LIMIT 1", (current, current))
            if not row:
                return None
            token = secrets.token_hex(16)
            lease_ms = (60 + sum(p.timeout_seconds for p in self.settings.email_providers)) * 1000
            self.db.execute("UPDATE email_outbox SET claim_token=?,claim_until_ms=? WHERE id=?", (token, current + lease_ms, row["id"]))
            return row | {"claim_token": token}

    def owned(self, job):
        row = self.get(job["id"])
        if row["claim_token"] != job["claim_token"] or row["status"] not in ("queued", "failed"):
            raise Fault("mail_claim_lost", "Yêu cầu gửi thư đã được tiến trình khác nhận")
        return row

    def reserve(self, job, provider):
        with self.transaction():
            self.owned(job)
            available = self.availability(provider)
            if available["available_ms"] > self.clock():
                return None, available["available_ms"]
            ident = uuid.uuid4().hex
            self.db.execute("INSERT INTO email_attempts VALUES(?,?,?,?,'sending',NULL)", (ident, job["id"], provider.name, self.clock()))
            self.db.execute("UPDATE email_outbox SET attempts=attempts+1,provider=? WHERE id=?", (provider.name, job["id"]))
            return ident, self.clock()

    def code_hash(self, row, code):
        key = self.settings.secrets.get("verification_hmac_key", "mã xác minh").encode()
        if len(key) < 32:
            raise Fault("email_unavailable", "Khóa verification_hmac_key cần ít nhất 32 ký tự")
        payload = "|".join((row["id"], row["recipient"], row["purpose"], code)).encode()
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def new_code(self, job):
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.transaction():
            row = self.owned(job)
            self.db.execute("UPDATE email_outbox SET code_hash=?,expires_ms=NULL,verified_ms=NULL,proof_hash=NULL,wrong_codes=0 WHERE id=?",
                            (self.code_hash(row, code), row["id"]))
        return code

    def attempt_done(self, job, provider, attempt, error=None, auth_error=False):
        with self.transaction():
            self.db.execute("UPDATE email_attempts SET status=?,error=? WHERE id=?", ("failed" if error else "sent", error, attempt))
            if auth_error:
                self.db.execute("INSERT OR REPLACE INTO email_provider_state VALUES(?,?,?)", (provider.name, self.clock() + HOUR, error))
            if not error:
                self.owned(job)
                current = self.clock()
                self.db.execute("UPDATE email_outbox SET status='sent',sent_ms=?,expires_ms=?,error=NULL,claim_token=NULL,claim_until_ms=NULL WHERE id=?",
                                (current, current + self.settings.verification.code_ttl_seconds * 1000, job["id"]))

    def defer(self, job, due, error):
        with self.transaction():
            self.owned(job)
            self.db.execute("UPDATE email_outbox SET status=?,next_attempt_ms=?,error=?,claim_token=NULL,claim_until_ms=NULL WHERE id=?",
                            ("failed" if error else "queued", due, error, job["id"]))

    def verify(self, ident, code):
        invalid = False
        token = secrets.token_urlsafe(32)
        with self.transaction():
            row = self.get(ident)
            current = self.clock()
            if (row["status"] != "sent" or row["expires_ms"] <= current or row["consumed_ms"] is not None
                    or row["wrong_codes"] >= self.settings.verification.max_attempts):
                invalid = True
            elif not re.fullmatch(r"[0-9]{6}", code) or not hmac.compare_digest(row["code_hash"], self.code_hash(row, code)):
                self.db.execute("UPDATE email_outbox SET wrong_codes=wrong_codes+1 WHERE id=?", (ident,))
                invalid = True
            else:
                # A newer delivered code supersedes this one; queued mail cannot revoke a valid code.
                newer = self.one("SELECT id FROM email_outbox WHERE recipient=? AND purpose=? AND status='sent' AND created_ms>? LIMIT 1",
                                 (row["recipient"], row["purpose"], row["created_ms"]))
                invalid = bool(newer)
                if not invalid:
                    self.db.execute("UPDATE email_outbox SET verified_ms=?,proof_hash=? WHERE id=?", (current, hashlib.sha256(token.encode()).hexdigest(), ident))
        if invalid:
            raise Fault("invalid_verification", "Mã không đúng hoặc đã hết hạn; hãy yêu cầu mã mới")
        return self.status(ident) | {"verification_token": token}

    def summary(self):
        with self.transaction():
            self.drop_old()
            counts = dict(self.db.execute("SELECT status,COUNT(*) FROM email_outbox GROUP BY status"))
            retried = self.db.execute("SELECT COUNT(*) FROM email_outbox WHERE attempts>1").fetchone()[0]
            providers = []
            for provider in self.settings.email_providers:
                item = self.availability(provider)
                tally = dict(self.db.execute("SELECT status,COUNT(*) FROM email_attempts WHERE provider=? AND started_ms>=? GROUP BY status",
                                             (provider.name, self.clock() // DAY * DAY)))
                item.update(sent=tally.get("sent", 0), failed=tally.get("failed", 0))
                item["low_quota"] = min(item["daily_remaining"] / provider.daily_limit, item["hourly_remaining"] / provider.hourly_limit) < .2
                providers.append(item)
            return {"counts": {s: counts.get(s, 0) for s in ("queued", "sent", "failed", "dropped")}, "retried": retried, "providers": providers}

    def consume(self, ident, token, purpose):
        """Call inside the account transaction so a failed signup/reset cannot spend the proof."""
        with self.transaction():
            row = self.get(ident)
            newer = self.one("SELECT id FROM email_outbox WHERE recipient=? AND purpose=? AND status='sent' AND created_ms>? LIMIT 1",
                             (row["recipient"], row["purpose"], row["created_ms"]))
            if (row["purpose"] != purpose or row["status"] != "sent" or not row["verified_ms"]
                    or row["expires_ms"] <= self.clock() or row["consumed_ms"] is not None or newer
                    or not hmac.compare_digest(row["proof_hash"] or "", hashlib.sha256(token.encode()).hexdigest())):
                raise Fault("invalid_verification", "Xác minh đã hết hạn hoặc đã dùng; hãy nhận mã mới")
            self.db.execute("UPDATE email_outbox SET consumed_ms=? WHERE id=?", (self.clock(), ident))
            return row
