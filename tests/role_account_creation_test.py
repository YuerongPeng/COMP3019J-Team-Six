# -*- coding: utf-8 -*-
"""Tests for role-based account creation (new features).

   1. staff adds a new instructor record    -> appears "Not linked"
   2. staff links a login to that instructor -> role=instructor, activated
   3. staff creates a new staff account      -> role=staff, can log in
   4. register only ever creates member      -> role cannot be chosen

Run from the project root:  python tests/role_account_creation_test.py
"""
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import Instructor, User  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "role_account.db")
if os.path.exists(DB):
    os.remove(DB)


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


app = create_app(TestConfig)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


with app.app_context():
    db.create_all()
    seed()

client = app.test_client()

# staff signs in
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
check("staff login 303", r.status_code == 303, str(r.status_code))

# 1. staff adds a new instructor record -------------------------------------
r = client.get("/admin/instructors/accounts")
check("instructor accounts page 200", r.status_code == 200, str(r.status_code))

r = client.post("/admin/instructors/accounts",
                data={"action": "add_instructor", "instructor_name": "Nina Park",
                      "profile": "Pilates and barre"},
                follow_redirects=True)
body = r.get_data(as_text=True)
check("new instructor added and shown", "Nina Park" in body, "")

with app.app_context():
    nina = Instructor.query.filter_by(name="Nina Park").first()
    check("instructor record persisted, not linked", nina is not None and nina.user_id is None,
          "user_id=%s" % (nina.user_id if nina else None))
    nina_id = nina.id

# duplicate name refused
r = client.post("/admin/instructors/accounts",
                data={"action": "add_instructor", "instructor_name": "Nina Park"},
                follow_redirects=True)
check("duplicate instructor name refused", "already exists" in r.get_data(as_text=True), "")

# 2. staff links a login to that instructor ---------------------------------
r = client.post("/admin/instructors/accounts",
                data={"action": "link_account", "instructor_id": str(nina_id),
                      "name": "Nina Park", "email": "nina@example.com",
                      "password": "temp12345"},
                follow_redirects=True)
check("instructor login created and linked", "created and linked" in r.get_data(as_text=True), "")

with app.app_context():
    nina = db.session.get(Instructor, nina_id)
    check("instructor now linked to a user", nina.user_id is not None, "user_id=%s" % nina.user_id)
    u = User.query.filter_by(email="nina@example.com").first()
    check("linked user has instructor role", u is not None and u.role == "instructor",
          "role=%s" % (u.role if u else None))

# the new instructor can sign in
client.post("/logout")
r = client.post("/login", data={"email": "nina@example.com", "password": "temp12345"})
check("new instructor can log in", r.status_code == 303, str(r.status_code))
client.post("/logout")

# 3. staff creates a new staff account --------------------------------------
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
r = client.get("/admin/staff")
check("staff accounts page 200", r.status_code == 200, str(r.status_code))

r = client.post("/admin/staff",
                data={"name": "Dana Lee", "email": "dana@example.com", "password": "secret99"},
                follow_redirects=True)
check("staff account created", "created" in r.get_data(as_text=True), "")

with app.app_context():
    dana = User.query.filter_by(email="dana@example.com").first()
    check("new staff has staff role", dana is not None and dana.role == "staff",
          "role=%s" % (dana.role if dana else None))

client.post("/logout")
r = client.post("/login", data={"email": "dana@example.com", "password": "secret99"})
check("new staff can log in", r.status_code == 303, str(r.status_code))

# new staff can access staff-only pages
r = client.get("/admin/staff")
check("new staff reaches staff page", r.status_code == 200, str(r.status_code))

# duplicate staff email refused
r = client.post("/admin/staff",
                data={"name": "Dup", "email": "dana@example.com", "password": "secret99"},
                follow_redirects=True)
check("duplicate staff email refused", "already in use" in r.get_data(as_text=True), "")
client.post("/logout")

# 4. register never lets the caller choose a role ---------------------------
for role in ("", "instructor", "staff", "admin"):
    r = client.post("/register", data={
        "name": "Role %s" % (role or "blank"),
        "email": "role-%s@example.com" % (role or "blank"),
        "password": "secret123", "confirm": "secret123",
        "role": role}, follow_redirects=True)
    with app.app_context():
        u = User.query.filter_by(email="role-%s@example.com" % (role or "blank")).first()
        check("register with role=%r still creates member" % role,
              u is not None and u.role == "member",
              "role=%s" % (u.role if u else None))
    client.post("/logout")

# ------------------------------------------------------------------ report
failed = [x for x in results if not x[1]]
for name, ok, detail in results:
    print(("PASS  " if ok else "FAIL  ") + name + (("   (" + detail + ")") if detail and not ok else ""))
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))

with app.app_context():
    db.session.remove()
    db.engine.dispose()
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
sys.exit(1 if failed else 0)
