"""Personal account enrolment; email proofs and password changes commit together."""
import re
import sqlite3
import uuid

from arena.protocol import Fault
from arena_api.accounts import now_ms
from arena_api.outbox import university_email


def clean_name(value, maximum):
    value = value.strip()
    if not 2 <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise Fault("invalid_request", f"Tên cần từ 2 đến {maximum} ký tự, không chứa ký tự điều khiển")
    return value


class People:
    def __init__(self, accounts, mail, rules):
        self.accounts, self.mail, self.rules = accounts, mail, rules

    def finish(self, request_id, proof, password, name, reset=False):
        a = self.accounts
        name = clean_name(name, self.rules.name_max) if not reset else None
        encoded = a.hash_password(password)
        try:
            with self.mail.transaction():
                ticket = self.mail.consume(request_id, proof, "reset" if reset else "verify")
                email = university_email(ticket["recipient"], self.rules)
                current = a.one("SELECT * FROM accounts WHERE email=?", (email,))
                if reset:
                    if not current or current["locked"]:
                        raise Fault("invalid_verification", "Không thể đặt lại mật khẩu cho tài khoản này")
                    ident = current["id"]
                    a.db.execute("UPDATE accounts SET password_hash=?,must_change=0 WHERE id=?", (encoded, ident))
                    a.db.execute("DELETE FROM sessions WHERE account_id=?", (ident,))
                else:
                    if current:
                        raise Fault("account_conflict", "Email đã có tài khoản. Hãy đăng nhập hoặc đặt lại mật khẩu.")
                    student = bool(re.fullmatch(self.rules.student_email, email, re.IGNORECASE))
                    number = email.split("@", 1)[0]
                    cohort = self.rules.cohort_base + int(number[:2]) if student and number[:2].isdigit() else None
                    ident = uuid.uuid4().hex
                    a.db.execute("INSERT INTO accounts(id,username,email,password_hash,role,must_change,delivered,created_ms,display_name,cohort) "
                                 "VALUES(?,?,?,?,?,0,1,?,?,?)", (ident, email, email, encoded, "student" if student else "staff", now_ms(), name, cohort))
                a.audit(ident, "auth.email_reset" if reset else "auth.enrolled", ident)
                # Reset revokes every old session and requires a fresh login.
                return a.public(a.one("SELECT * FROM accounts WHERE id=?", (ident,)))
        except sqlite3.IntegrityError:
            raise Fault("account_conflict", "Tài khoản đã tồn tại; hãy đăng nhập") from None

    def profile(self, ident):
        a = self.accounts
        with a.store.lock:
            row = a.one("SELECT * FROM accounts WHERE id=?", (ident,))
            if not row:
                raise Fault("not_found", "Không tìm thấy người dùng")
            return a.public(row) | {key: row[key] for key in ("class_name", "language", "introduction", "contact", "looking")}

    def update(self, ident, body):
        a = self.accounts
        name = clean_name(body["display_name"], self.rules.name_max)
        for key in ("class_name", "introduction", "contact"):
            if any(ord(c) < 32 for c in body[key]):
                raise Fault("invalid_request", "Thông tin hồ sơ không được chứa ký tự điều khiển")
        with a.store.lock, a.db:
            a.db.execute("UPDATE accounts SET display_name=?,class_name=?,language=?,introduction=?,contact=?,looking=? WHERE id=?",
                         (name, body["class_name"].strip(), body["language"], body["introduction"].strip(), body["contact"].strip(), int(body["looking"]), ident))
            a.audit(ident, "person.profile_updated", ident)
        return self.profile(ident)

    def judge(self, ident, enabled, actor):
        a = self.accounts
        with a.store.lock, a.db:
            person = a.one("SELECT * FROM accounts WHERE id=? AND role='staff'", (ident,))
            if not person:
                raise Fault("invalid_request", "Chỉ tài khoản cán bộ được nâng Giám khảo")
            a.db.execute("UPDATE accounts SET is_judge=? WHERE id=?", (int(enabled), ident))
            a.audit(actor, "person.judge_granted" if enabled else "person.judge_revoked", ident)
        return self.profile(ident)
