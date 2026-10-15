"""Persistent accounts, opaque sessions, and admin approval transactions."""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from arena.protocol import Fault
from arena_api.migrations import migrate


def now_ms():
    return time.time_ns() // 1_000_000


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def name_key(name):
    return unicodedata.normalize("NFKC", name).casefold()


class Accounts:
    def __init__(self, store, session_seconds=3600):
        self.store, self.db, self.ttl = store, store.db, session_seconds
        migrate(store)
        self.hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1)
        self.hash_slots = threading.BoundedSemaphore(2)
        self.dummy_hash = self.hash_password(secrets.token_urlsafe(24))

    def hash_password(self, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise Fault("invalid_request", "Password must contain 8..128 characters")
        with self.hash_slots:
            return self.hasher.hash(password)

    def verify(self, encoded, supplied):
        try:
            with self.hash_slots:
                return self.hasher.verify(encoded, supplied)
        except (VerificationError, InvalidHashError):
            return False

    def one(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        row = cursor.fetchone()
        return dict(zip([c[0] for c in cursor.description], row)) if row else None

    def rows(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        keys = [c[0] for c in cursor.description]
        return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def audit(self, actor, action, target):
        # Deliberately records identifiers only, never request bodies or secrets.
        self.db.execute("INSERT INTO audit(actor,action,target,created_ms) VALUES(?,?,?,?)", (actor, action, target, now_ms()))

    @staticmethod
    def public(account):
        return {"id": account["id"], "username": account["username"], "role": account["role"], "team_id": account["team_id"],
                "must_change_password": bool(account["must_change"]), "locked": bool(account["locked"]),
                "delivered": bool(account["delivered"]), "created_ms": account["created_ms"]}

    def rate_limit(self, bucket, limit, window_seconds):
        key, current = token_hash(bucket), now_ms()
        with self.store.lock, self.db:
            self.db.execute("DELETE FROM rate_limits WHERE expires_ms<=?", (current,))
            row = self.one("SELECT * FROM rate_limits WHERE bucket=?", (key,))
            if row and row["count"] >= limit:
                fault = Fault("rate_limited", "Too many attempts; try again after the rate-limit window")
                fault.retry_after = max(1, (row["expires_ms"] - current + 999) // 1000)
                raise fault
            self.db.execute("INSERT INTO rate_limits VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
                            (key, 1, current + window_seconds * 1000))

    def bootstrap_admin(self, username, password):
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,30}", username):
            raise Fault("invalid_request", "Invalid admin username")
        encoded, ident = self.hash_password(password), uuid.uuid4().hex
        try:
            with self.store.lock, self.db:
                self.db.execute("INSERT INTO accounts(id,username,password_hash,role,team_id,must_change,created_ms) VALUES(?,?,?,'admin',NULL,0,?)",
                                (ident, username.casefold(), encoded, now_ms()))
                self.audit("bootstrap-cli", "admin.created", ident)
                return self.public(self.one("SELECT * FROM accounts WHERE id=?", (ident,)))
        except sqlite3.IntegrityError:
            raise Fault("account_conflict", "Username already exists") from None

    def new_session(self, account):
        token, csrf, expires = secrets.token_urlsafe(32), secrets.token_urlsafe(32), now_ms() + self.ttl * 1000
        self.db.execute("DELETE FROM sessions WHERE expires_ms<=?", (now_ms(),))
        self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (token_hash(token), account["id"], csrf, expires))
        return {"token": token, "csrf_token": csrf, "expires_ms": expires, "user": self.public(account)}

    def login(self, username, password, peer):
        username = username.casefold().strip()
        self.rate_limit("login-ip:" + peer, 30, 900)
        self.rate_limit("login-account:" + username, 10, 900)
        with self.store.lock:
            account = self.one("SELECT * FROM accounts WHERE username=?", (username,))
        valid = self.verify(account["password_hash"] if account else self.dummy_hash, password)
        if not valid or not account or account["locked"]:
            raise Fault("unauthorized", "Invalid username or password")
        with self.store.lock, self.db:
            latest = self.one("SELECT * FROM accounts WHERE id=?", (account["id"],))
            if latest["locked"] or latest["password_hash"] != account["password_hash"]:
                raise Fault("unauthorized", "Invalid username or password")
            self.audit(account["id"], "auth.login", account["id"])
            return self.new_session(latest)

    def principal(self, token):
        if not token or len(token) > 128:
            raise Fault("unauthorized", "Login required")
        with self.store.lock:
            row = self.one("SELECT a.*,s.csrf,s.expires_ms FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires_ms>? AND a.locked=0",
                           (token_hash(token), now_ms()))
        if not row:
            raise Fault("unauthorized", "Session expired or revoked")
        return {"user": self.public(row), "csrf_token": row["csrf"], "expires_ms": row["expires_ms"], "token_hash": token_hash(token)}

    def logout(self, principal):
        with self.store.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (principal["token_hash"],))
            self.audit(principal["user"]["id"], "auth.logout", principal["user"]["id"])

    def change_password(self, principal, current, password):
        ident = principal["user"]["id"]
        with self.store.lock:
            account = self.one("SELECT * FROM accounts WHERE id=?", (ident,))
        if not self.verify(account["password_hash"], current):
            raise Fault("unauthorized", "Current password is incorrect")
        if current == password:
            raise Fault("invalid_request", "New password must differ from temporary/current password")
        encoded = self.hash_password(password)
        with self.store.lock, self.db:
            latest = self.one("SELECT * FROM accounts WHERE id=?", (ident,))
            if latest["locked"] or latest["password_hash"] != account["password_hash"] or not self.one("SELECT 1 FROM sessions WHERE token_hash=? AND expires_ms>?", (principal["token_hash"], now_ms())):
                raise Fault("unauthorized", "Session changed; log in again")
            self.db.execute("UPDATE accounts SET password_hash=?,must_change=0 WHERE id=?", (encoded, ident))
            self.db.execute("DELETE FROM sessions WHERE account_id=?", (ident,))
            self.audit(ident, "auth.password_changed", ident)
            return self.new_session(self.one("SELECT * FROM accounts WHERE id=?", (ident,)))

    def register(self, data, peer):
        self.rate_limit("register:" + peer, 5, 3600)
        ident, key = uuid.uuid4().hex, name_key(data["team"])
        try:
            with self.store.lock, self.db:
                if self.one("SELECT id FROM teams WHERE name_key=?", (key,)):
                    raise Fault("registration_conflict", "Team name is already registered")
                self.db.execute("INSERT INTO registrations VALUES(?,?,?,'pending',NULL,?)", (ident, json.dumps(data, ensure_ascii=False), key, now_ms()))
                self.audit("public", "registration.created", ident)
            return {"id": ident, "status": "pending"}
        except sqlite3.IntegrityError:
            raise Fault("registration_conflict", "Team registration is already pending") from None

    def registrations(self, status, limit, offset):
        with self.store.lock:
            rows = self.rows("SELECT * FROM registrations WHERE status=? ORDER BY created_ms DESC,id LIMIT ? OFFSET ?", (status, limit, offset))
        return [{"id": r["id"], "status": r["status"], "team_id": r["team_id"], "created_ms": r["created_ms"], **json.loads(r["data"])} for r in rows]

    def approve(self, ident, username, password, actor):
        password = password or "Uet-" + secrets.token_urlsafe(18)
        encoded, team_id, account_id = self.hash_password(password), uuid.uuid4().hex, uuid.uuid4().hex
        try:
            with self.store.lock, self.db:
                row = self.one("SELECT * FROM registrations WHERE id=?", (ident,))
                if not row:
                    raise Fault("not_found", "Registration not found")
                if row["status"] != "pending":
                    raise Fault("registration_conflict", "Registration has already been reviewed")
                data = json.loads(row["data"])
                self.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,?,?,?,?)", (team_id, data["team"], row["team_key"], data["email"], data["phone"], json.dumps(data["members"], ensure_ascii=False), now_ms()))
                self.db.execute("INSERT INTO accounts(id,username,password_hash,role,team_id,created_ms) VALUES(?,?,?,'team',?,?)", (account_id, username.casefold(), encoded, team_id, now_ms()))
                self.db.execute("UPDATE registrations SET status='approved',team_id=? WHERE id=?", (team_id, ident))
                self.audit(actor, "registration.approved", ident)
                account = self.public(self.one("SELECT * FROM accounts WHERE id=?", (account_id,)))
            return {"account": account, "temporary_password": password, "credential_visibility": "this_response_only"}
        except sqlite3.IntegrityError:
            raise Fault("account_conflict", "Username or team name already exists; approval was rolled back") from None

    def reject(self, ident, actor):
        with self.store.lock, self.db:
            row = self.one("SELECT * FROM registrations WHERE id=?", (ident,))
            if not row:
                raise Fault("not_found", "Registration not found")
            if row["status"] != "pending":
                raise Fault("registration_conflict", "Registration has already been reviewed")
            self.db.execute("UPDATE registrations SET status='rejected' WHERE id=?", (ident,))
            self.audit(actor, "registration.rejected", ident)
        return {"id": ident, "status": "rejected"}

    def team(self, ident):
        with self.store.lock:
            row = self.one("SELECT * FROM teams WHERE id=?", (ident,))
        if not row:
            raise Fault("not_found", "Team not found")
        row.pop("name_key")
        row["members"] = json.loads(row["members"])
        return row

    def accounts(self, limit, offset):
        with self.store.lock:
            rows = self.rows("SELECT * FROM accounts WHERE role='team' ORDER BY created_ms DESC,id LIMIT ? OFFSET ?", (limit, offset))
        return [{**self.public(a), "team": self.team(a["team_id"])} for a in rows]

    def lock_account(self, ident, locked, actor):
        with self.store.lock, self.db:
            account = self.one("SELECT * FROM accounts WHERE id=? AND role='team'", (ident,))
            if not account:
                raise Fault("not_found", "Team account not found")
            if not locked and self.team(account["team_id"])["archived_ms"]:
                raise Fault("admin_conflict", "Restore the deleted team before unlocking its account")
            self.db.execute("UPDATE accounts SET locked=? WHERE id=?", (int(locked), ident))
            self.db.execute("DELETE FROM sessions WHERE account_id=?", (ident,))
            self.audit(actor, "account.locked" if locked else "account.unlocked", ident)
            return self.public(self.one("SELECT * FROM accounts WHERE id=?", (ident,)))

    def reset_password(self, ident, password, actor):
        password = password or "Uet-" + secrets.token_urlsafe(18)
        encoded = self.hash_password(password)
        with self.store.lock, self.db:
            account = self.one("SELECT * FROM accounts WHERE id=? AND role='team'", (ident,))
            if not account:
                raise Fault("not_found", "Team account not found")
            self.db.execute("UPDATE accounts SET password_hash=?,must_change=1,delivered=0 WHERE id=?", (encoded, ident))
            self.db.execute("DELETE FROM sessions WHERE account_id=?", (ident,))
            self.db.execute("UPDATE recoveries SET status='resolved' WHERE account_id=?", (ident,))
            self.audit(actor, "account.password_reset", ident)
            return {"account": self.public(self.one("SELECT * FROM accounts WHERE id=?", (ident,))), "temporary_password": password, "credential_visibility": "this_response_only"}

    def delivered(self, ident, actor):
        with self.store.lock, self.db:
            if not self.one("SELECT 1 FROM accounts WHERE id=? AND role='team'", (ident,)):
                raise Fault("not_found", "Team account not found")
            self.db.execute("UPDATE accounts SET delivered=1 WHERE id=?", (ident,))
            self.audit(actor, "account.delivered", ident)
        return {"ok": True}

    def recover(self, username, email, peer):
        self.rate_limit("recovery:" + peer, 5, 3600)
        with self.store.lock, self.db:
            row = self.one("SELECT a.id FROM accounts a JOIN teams t ON t.id=a.team_id WHERE a.username=? AND t.email=?", (username.casefold().strip(), email.casefold().strip()))
            if row:
                self.db.execute("INSERT OR IGNORE INTO recoveries VALUES(?,?,'pending',?)", (uuid.uuid4().hex, row["id"], now_ms()))
        return {"accepted": True, "message": "If the details match, an administrator will review the request"}

    def recoveries(self, limit, offset):
        with self.store.lock:
            return self.rows("SELECT r.*,a.username,t.name AS team,t.email FROM recoveries r JOIN accounts a ON a.id=r.account_id JOIN teams t ON t.id=a.team_id WHERE r.status='pending' ORDER BY r.created_ms DESC LIMIT ? OFFSET ?", (limit, offset))

    def resolve_recovery(self, ident, actor):
        with self.store.lock, self.db:
            row = self.one('SELECT * FROM recoveries WHERE id=?', (ident,))
            if row is None:
                raise Fault('not_found', 'Recovery request not found')
            if row['status'] == 'pending':
                self.db.execute("UPDATE recoveries SET status='resolved' WHERE id=?", (ident,))
                self.audit(actor, 'recovery.resolved', ident)
            return {'ok': True}

    def audit_log(self, limit, offset):
        with self.store.lock:
            return self.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
