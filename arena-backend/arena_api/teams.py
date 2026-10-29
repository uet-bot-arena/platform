"""Season rosters and captain operations; every membership change is one SQL transaction."""
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import uuid

from arena.protocol import Fault
from arena_api.accounts import name_key, now_ms
from arena_api.people import clean_name

HOUR = 3_600_000
TEAM_FIELDS = ('id', 'name', 'created_ms', 'archived_ms', 'revision', 'season_id',
               'is_guest', 'locked_ms', 'recruiting', 'repo_code')


def public_team(team):
    return {key: team[key] for key in TEAM_FIELDS}


class Teams:
    def __init__(self, accounts, rules, clock=now_ms):
        self.a, self.db, self.store, self.rules, self.clock = accounts, accounts.db, accounts.store, rules, clock
        self.season = rules.season_id
        with self.store.transaction():
            self.db.execute("INSERT OR IGNORE INTO seasons(id) VALUES(?)", (self.season,))
            # accounts.team_id is the current-season projection used by existing job APIs.
            self.db.execute("UPDATE accounts SET team_id=(SELECT m.team_id FROM team_members m JOIN teams t ON t.id=m.team_id "
                            "WHERE m.user_id=accounts.id AND m.season_id=? AND t.archived_ms IS NULL) WHERE role<>'admin'", (self.season,))

    def policy(self):
        with self.store.lock:
            return self.a.one("SELECT * FROM seasons WHERE id=?", (self.season,))

    def close_due(self):
        with self.store.transaction():
            policy = self.policy()
            if policy["team_deadline_ms"] is not None and policy["team_deadline_ms"] <= self.clock():
                rows = self.a.rows("SELECT id FROM teams WHERE season_id=? AND locked_ms IS NULL AND archived_ms IS NULL AND "
                                   "(is_guest=1 OR (SELECT COUNT(*) FROM team_members m WHERE m.team_id=teams.id)>=?)",
                                   (self.season, self.rules.team_size))
                for row in rows:
                    self.db.execute("UPDATE teams SET locked_ms=?,recruiting=0,revision=revision+1 WHERE id=?", (self.clock(), row["id"]))
                    self.a.audit("system", "team.deadline_locked", row["id"])

    def person(self, ident):
        with self.store.lock:
            value = self.a.one("SELECT * FROM accounts WHERE id=? AND locked=0", (ident,))
        if not value or value["role"] == "admin":
            raise Fault("forbidden", "Cần tài khoản cá nhân đang hoạt động")
        return value

    def roster(self, team):
        with self.store.lock:
            return self.a.rows("SELECT a.id,a.display_name,a.username,a.cohort,a.role,m.captain,a.class_name,a.language "
                               "FROM team_members m JOIN accounts a ON a.id=m.user_id WHERE m.team_id=? ORDER BY m.captain DESC,m.joined_ms,a.id", (team,))

    def current(self, user):
        self.close_due()
        with self.store.lock:
            person = self.person(user)
            if not person["team_id"]:
                return None
            team = self.a.team(person["team_id"])
            members = self.roster(team["id"])
            captain = next((m["id"] for m in members if m["captain"]), None)
            invite = self.a.one("SELECT code FROM team_invites WHERE team_id=?", (team["id"],))
            policy = self.policy()
            deadline = policy["team_deadline_ms"]
            enabled = (len(members) < self.rules.team_size and not team["is_guest"] and not team["locked_ms"]
                       and (deadline is None or self.clock() < deadline))
            today = datetime.fromtimestamp(self.clock() / 1000, timezone.utc)
            freshman = self.rules.freshman_cohort or today.year - (today.month < 8)
            return public_team(team) | {"roster": [{k: m[k] for k in ("id", "display_name", "role", "captain", "cohort")} for m in members],
                           "captain_id": captain, "member_count": len(team['members']) if team['legacy_roster'] else len(members),
                           "invite_code": invite["code"] if invite and captain == user else None,
                           "invite_enabled": enabled, "team_size": self.rules.team_size,
                           "freshman": len(members) == self.rules.team_size and all(m["role"] == "student" and m["cohort"] == freshman for m in members)}

    def check_editable(self, team, admin=False):
        if team["archived_ms"] or team["season_id"] != self.season:
            raise Fault("admin_conflict", "Đội không thuộc mùa đang mở hoặc đã giải tán")
        deadline = self.policy()["team_deadline_ms"]
        if not admin and (team["locked_ms"] or deadline is not None and self.clock() >= deadline):
            raise Fault("admin_conflict", "Đã hết hạn lập đội; thay đổi thành viên cần ban tổ chức")
        if self.a.one("SELECT 1 FROM competition_entries e JOIN competitions c ON c.id=e.competition_id "
                      "WHERE e.team_id=? AND c.status NOT IN ('draft','open','completed') AND c.archived_ms IS NULL", (team["id"],)):
            raise Fault("admin_conflict", "Đội đang thi đấu; hãy hoàn tất hoặc dừng giải trước khi đổi thành viên")

    def captain_team(self, user):
        with self.store.lock:
            person = self.person(user)
            team = self.a.team(person["team_id"])
            if not self.a.one("SELECT 1 FROM team_members WHERE team_id=? AND user_id=? AND captain=1", (team["id"], user)):
                raise Fault("forbidden", "Chỉ đội trưởng được thực hiện thao tác này")
            return team

    def sync(self, team):
        members = self.roster(team)
        profile = [{"name": m["display_name"] or m["username"], "student_id": m["username"].split("@")[0] if m["role"] == "student" else m["id"][:8]} for m in members]
        self.db.execute("UPDATE teams SET members=?,legacy_roster=0,revision=revision+1,recruiting=CASE WHEN ? >= ? THEN 0 ELSE recruiting END WHERE id=?",
                        (json.dumps(profile, ensure_ascii=False), len(members), self.rules.team_size, team))

    def rotate(self, team):
        # UNIQUE in SQLite handles the exceptionally unlikely code collision.
        for _ in range(10):
            code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
            try:
                self.db.execute("INSERT INTO team_invites VALUES(?,?,?) ON CONFLICT(team_id) DO UPDATE SET code=excluded.code,changed_ms=excluded.changed_ms", (team, code, self.clock()))
                return
            except sqlite3.IntegrityError:
                continue
        raise Fault("admin_conflict", "Chưa tạo được mã mời, hãy thử lại")

    def create(self, user, name, guest=False, admin=False):
        name = clean_name(name, self.rules.name_max)
        try:
            with self.store.transaction():
                person = self.person(user)
                deadline = self.policy()["team_deadline_ms"]
                if not admin and deadline is not None and self.clock() >= deadline:
                    raise Fault("admin_conflict", "Đã hết hạn lập đội")
                if person["team_id"] or self.a.one("SELECT 1 FROM team_members WHERE user_id=? AND season_id=?", (user, self.season)):
                    raise Fault("account_conflict", "Mỗi người chỉ ở một đội trong mùa giải")
                if guest and not person["is_judge"]:
                    raise Fault("forbidden", "Chỉ Giám khảo tạo được đội khách")
                if person["role"] == "guest" and not admin:
                    raise Fault("forbidden", "Tài khoản khách cần ban tổ chức lập đội")
                team = uuid.uuid4().hex
                self.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms,season_id,is_guest,legacy_roster) VALUES(?,?,?,'','','[]',?,?,?,0)",
                                (team, name, name_key(name), self.clock(), self.season, int(guest)))
                self.db.execute("INSERT INTO team_members VALUES(?,?,?,1,?)", (self.season, team, user, self.clock()))
                self.db.execute("UPDATE accounts SET team_id=?,looking=0 WHERE id=?", (team, user))
                self.rotate(team)
                self.sync(team)
                self.a.audit(user, "team.guest_created" if guest else "team.self_created", team)
        except sqlite3.IntegrityError:
            raise Fault("account_conflict", "Tên đội đã được dùng hoặc bạn vừa vào đội khác") from None
        return self.current(user)

    def join(self, user, code):
        invalid = False
        with self.store.transaction():
            person = self.person(user)
            current = self.clock()
            if person["invite_blocked_ms"] > current:
                fault = Fault("rate_limited", "Tạm khóa nhập mã mời do sai quá nhiều lần")
                fault.retry_after = (person["invite_blocked_ms"] - current + 999) // 1000
                raise fault
            if person["team_id"]:
                raise Fault("account_conflict", "Bạn đã có đội trong mùa này")
            found = self.a.one("SELECT t.* FROM team_invites i JOIN teams t ON t.id=i.team_id WHERE i.code=? AND t.season_id=?", (code.strip().upper(), self.season))
            deadline = self.policy()["team_deadline_ms"]
            invalid = (not found or found["archived_ms"] or found["locked_ms"] or found["is_guest"] or deadline is not None and current >= deadline
                       or len(self.roster(found["id"])) >= self.rules.team_size)
            if invalid:
                failures = person["invite_failures"] + 1 if person["invite_window_ms"] > current else 1
                self.db.execute("UPDATE accounts SET invite_failures=?,invite_window_ms=?,invite_blocked_ms=? WHERE id=?",
                                (failures, person["invite_window_ms"] if person["invite_window_ms"] > current else current + HOUR,
                                 current + HOUR if failures > 10 else 0, user))
            else:
                self.check_editable(found)
                self.db.execute("INSERT INTO team_members VALUES(?,?,?,0,?)", (self.season, found["id"], user, current))
                self.db.execute("UPDATE accounts SET team_id=?,looking=0,invite_failures=0,invite_blocked_ms=0 WHERE id=?", (found["id"], user))
                self.sync(found["id"])
                self.a.audit(user, "team.joined", found["id"])
        # Failed guesses must commit, even though the HTTP request fails.
        if invalid:
            raise Fault("invalid_request", "Mã mời không hợp lệ, đội đã đủ người hoặc đã hết hạn")
        return self.current(user)

    def change(self, user, action, target=None, name=None, enabled=None):
        with self.store.transaction():
            person = self.person(user)
            team = self.a.team(person["team_id"])
            if action != "leave":
                self.captain_team(user)
            self.check_editable(team)
            if action == "name":
                name = clean_name(name, self.rules.name_max)
                try:
                    self.db.execute("UPDATE teams SET name=?,name_key=?,revision=revision+1 WHERE id=?", (name, name_key(name), team["id"]))
                except sqlite3.IntegrityError:
                    raise Fault("account_conflict", "Tên đội đã được dùng") from None
            elif action == "rotate":
                self.rotate(team["id"])
            elif action == "recruiting":
                self.db.execute("UPDATE teams SET recruiting=? WHERE id=?", (int(enabled and len(self.roster(team["id"])) < self.rules.team_size and not team["is_guest"]), team["id"]))
            elif action in ("leave", "remove", "captain"):
                self.member_change(team, user if action == "leave" else target, action, user)
            else:
                raise Fault("invalid_request", "Thao tác đội không hợp lệ")
            self.a.audit(user, "team." + action, team["id"])
        return self.current(user)

    def member_change(self, team, target, action, actor):
        member = self.a.one("SELECT * FROM team_members WHERE team_id=? AND user_id=?", (team["id"], target))
        if not member:
            raise Fault("not_found", "Người này không thuộc đội")
        members = self.roster(team["id"])
        if action == "captain":
            self.db.execute("UPDATE team_members SET captain=0 WHERE team_id=?", (team["id"],))
            self.db.execute("UPDATE team_members SET captain=1 WHERE team_id=? AND user_id=?", (team["id"], target))
        else:
            if member["captain"] and len(members) > 1:
                raise Fault("admin_conflict", "Đội trưởng cần chuyển quyền trước khi rời đội")
            self.db.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team["id"], target))
            self.db.execute("UPDATE accounts SET team_id=NULL WHERE id=?", (target,))
            self.db.execute("DELETE FROM sessions WHERE account_id=?", (target,))
            if len(members) == 1:
                self.db.execute("UPDATE teams SET archived_ms=?,recruiting=0 WHERE id=?", (self.clock(), team["id"]))
        self.sync(team["id"])

    def eligible(self, team):
        value = self.a.team(team)
        if value["archived_ms"] or value["season_id"] != self.season:
            raise Fault("competition_conflict", "Đội không thuộc mùa hiện tại")
        # BTC-approved old rosters have named members without individual accounts.
        count = len(value["members"]) if value["legacy_roster"] else len(self.roster(team))
        if not value["is_guest"] and count != self.rules.team_size:
            raise Fault("competition_conflict", "Đội phải đủ bốn người trước khi dự giải")

    def matching(self):
        self.close_due()
        with self.store.lock:
            people = self.a.rows("SELECT id,display_name,class_name,language,introduction,contact FROM accounts "
                                 "WHERE looking=1 AND team_id IS NULL AND locked=0 AND role IN ('student','staff') ORDER BY display_name,id")
            teams = self.a.rows("SELECT t.id,t.name,COUNT(m.user_id) AS count,a.display_name AS captain_name,a.language,a.contact FROM teams t LEFT JOIN team_members m ON m.team_id=t.id "
                                "LEFT JOIN team_members captain ON captain.team_id=t.id AND captain.captain=1 LEFT JOIN accounts a ON a.id=captain.user_id "
                                "WHERE t.season_id=? AND t.recruiting=1 AND t.locked_ms IS NULL AND t.archived_ms IS NULL AND t.is_guest=0 GROUP BY t.id HAVING COUNT(m.user_id)<?", (self.season, self.rules.team_size))
            return {"people": people, "teams": teams, "team_size": self.rules.team_size}

    def set_deadline(self, deadline, revision, actor):
        with self.store.transaction():
            if self.policy()["revision"] != revision:
                raise Fault("admin_conflict", "Hạn lập đội đã đổi; hãy tải lại")
            self.db.execute("UPDATE seasons SET team_deadline_ms=?,revision=revision+1 WHERE id=?", (deadline, self.season))
            self.a.audit(actor, "season.team_deadline", self.season)
        self.close_due()
        return self.policy()

    def proposals(self):
        self.close_due()
        with self.store.lock:
            deadline = self.policy()["team_deadline_ms"]
            if deadline is None or self.clock() < deadline:
                return {"items": [], "remainder": [], "confirm_until_ms": None}
            waiting = self.a.rows("SELECT id,display_name,class_name,language FROM accounts WHERE team_id IS NULL AND looking=1 AND locked=0 AND role IN ('student','staff') ORDER BY class_name,language,id")
            targets = self.a.rows("SELECT id,name,revision FROM teams WHERE season_id=? AND locked_ms IS NULL AND archived_ms IS NULL AND is_guest=0 AND legacy_roster=0 ORDER BY id", (self.season,))
            items = []
            for team in targets:
                roster = self.roster(team["id"])
                need = self.rules.team_size - len(roster)
                if not roster or len(waiting) < need:
                    continue
                first = roster[0]
                waiting.sort(key=lambda p: (p["class_name"] != first["class_name"], p["language"] != first["language"], p["id"]))
                chosen, waiting = waiting[:need], waiting[need:]
                items.append({"team_id": team["id"], "team_name": team["name"], "expected_revision": team["revision"], "people": chosen})
            while len(waiting) >= self.rules.team_size:
                chosen, waiting = waiting[:self.rules.team_size], waiting[self.rules.team_size:]
                items.append({"team_id": None, "team_name": "", "expected_revision": None, "people": chosen})
            for item in items:
                item["id"] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            return {"items": items, "remainder": waiting, "confirm_until_ms": deadline + 48 * HOUR}

    def confirm(self, proposal, name, actor):
        with self.store.transaction():
            available = self.proposals()
            item = next((p for p in available["items"] if p["id"] == proposal), None)
            if not item or self.clock() > available["confirm_until_ms"]:
                raise Fault("admin_conflict", "Đề xuất đã thay đổi hoặc quá 48 giờ; hãy chỉnh đội thủ công")
            ids = [p["id"] for p in item["people"]]
            if item["team_id"] is None:
                team = self.create(ids.pop(0), name, admin=True)["id"]
            else:
                team = item["team_id"]
            for ident in ids:
                self.add_admin(team, ident)
            self.db.execute("UPDATE teams SET locked_ms=?,recruiting=0 WHERE id=?", (self.clock(), team))
            self.a.audit(actor, "team.merge_confirmed", team)
            return self.a.team(team)

    def add_admin(self, team, user):
        person = self.person(user)
        if person["team_id"] or len(self.roster(team)) >= self.rules.team_size:
            raise Fault("admin_conflict", "Người dùng đã có đội hoặc đội đã đủ người")
        self.db.execute("INSERT INTO team_members VALUES(?,?,?,0,?)", (self.season, team, user, self.clock()))
        self.db.execute("UPDATE accounts SET team_id=?,looking=0 WHERE id=?", (team, user))
        self.sync(team)

    def admin_create(self, user, name, actor, key):
        from arena.service import digest
        if not 8 <= len(key) <= 128:
            raise Fault('invalid_request', 'Idempotency-Key of 8..128 characters required')
        operation = 'team.admin_create:' + user
        hashed = digest({'name': name})
        with self.store.transaction():
            old = self.a.one('SELECT * FROM competition_commands WHERE actor=? AND operation=? AND key=?', (actor, operation, key))
            if old:
                if old['digest'] != hashed:
                    raise Fault('idempotency_conflict', 'Key already used for different input')
                return json.loads(old['response'])
            if self.person(user)['team_id']:
                raise Fault('admin_conflict', 'Người dùng đã có đội')
            result = self.create(user, name, admin=True)
            self.a.audit(actor, 'team.admin_create', result['id'])
            self.db.execute('INSERT INTO competition_commands VALUES(?,?,?,?,?)', (actor, operation, key, hashed, json.dumps(result)))
            return result

    def admin_change(self, team, target, action, actor):
        with self.store.transaction():
            value = self.a.team(team)
            self.check_editable(value, admin=True)
            if action == "add":
                self.add_admin(team, target)
            else:
                self.member_change(value, target, action, actor)
            self.a.audit(actor, "team.admin_" + action, team)
            return self.a.team(team)
