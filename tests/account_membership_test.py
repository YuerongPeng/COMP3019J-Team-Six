# -*- coding: utf-8 -*-
"""Tests for the account & membership features (requirement set 1-5).

   1. register           member creates an account and is signed in
   2. change password    signed-in member changes their own password
   3. renew              same card, end extends from current end, credits refill
   4. switch             old card -> replaced, new card from today, old bookings
                         stay on the old card
   5. expiry             timetable shows an expiry notice + grey buttons

Run from the project root:  python tests/account_membership_test.py
"""
import os
import sys
from datetime import date, timedelta

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import (Booking, ClassSession, Membership,  # noqa: E402
                        MembershipPlan, PlanChange, User)
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "account_membership.db")
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


# 1. register ---------------------------------------------------------------
r = client.get("/register")
check("register page 200", r.status_code == 200, str(r.status_code))

r = client.post("/register", data={
    "name": "Xin Hao", "email": "xin@example.com",
    "password": "secret123", "confirm": "secret123"}, follow_redirects=True)
check("register signs in and lands on timetable",
      r.status_code == 200 and "Xin Hao" in r.get_data(as_text=True), "")

with app.app_context():
    u = User.query.filter_by(email="xin@example.com").first()
    check("new user persisted, role member",
          u is not None and u.role == "member" and u.name == "Xin Hao", "")

r = client.post("/register", data={
    "name": "Dup", "email": "xin@example.com",
    "password": "secret123", "confirm": "secret123"}, follow_redirects=True)
check("duplicate email refused", "already registered" in r.get_data(as_text=True), "")

client.post("/logout")


# 2. change password --------------------------------------------------------
r = client.post("/login", data={"email": "xin@example.com", "password": "secret123"})
check("new user can log in", r.status_code == 303, str(r.status_code))

r = client.post("/account/password", data={
    "current": "wrong", "new": "newpass1", "confirm": "newpass1"},
    follow_redirects=True)
check("wrong current password refused", "incorrect" in r.get_data(as_text=True), "")

r = client.post("/account/password", data={
    "current": "secret123", "new": "newpass1", "confirm": "newpass1"},
    follow_redirects=True)
check("password changed", r.status_code == 200, str(r.status_code))

client.post("/logout")
r = client.post("/login", data={"email": "xin@example.com", "password": "secret123"})
check("old password now rejected", r.status_code == 200, str(r.status_code))
r = client.post("/login", data={"email": "xin@example.com", "password": "newpass1"})
check("new password works", r.status_code == 303, str(r.status_code))


# 3. renew (same card, no second card) --------------------------------------
with app.app_context():
    plan = MembershipPlan.query.filter_by(name="Ten-visit pack").first()
    plan_id = plan.id
    # give xin a ten-visit card with 3 credits left, ending in 5 days
    card = Membership(user_id=User.query.filter_by(email="xin@example.com").first().id,
                      plan_id=plan_id, start=date.today(),
                      end=date.today() + timedelta(days=5), credits_remaining=3)
    db.session.add(card)
    db.session.commit()
    card_id = card.id

r = client.post("/account/renew", follow_redirects=True)
check("renew flashes success", "Renewed" in r.get_data(as_text=True), "")

with app.app_context():
    card = db.session.get(Membership, card_id)
    check("renew extends from current end (5->95 days)",
          card.end == date.today() + timedelta(days=5 + 90),
          "end=%s" % card.end)
    check("renew refills credits to limit", card.credits_remaining == 10,
          "credits=%s" % card.credits_remaining)
    other = Membership.query.filter_by(user_id=card.user_id, status="active").all()
    check("renew does not open a second card (still one active card)",
          len(other) == 1, "active cards=%d" % len(other))


# 4. switch (old card replaced, new card from today, bookings stay) ---------
with app.app_context():
    u = User.query.filter_by(email="xin@example.com").first()
    # book xin into a class on the OLD card so we can prove it stays put
    old_card = Membership.query.filter_by(user_id=u.id, status="active").first()
    some = ClassSession.query.filter(ClassSession.start >= date.today()).first()
    db.session.add(Booking(user_id=u.id, class_session_id=some.id,
                           membership_id=old_card.id, status="Booked"))
    db.session.commit()
    old_id = old_card.id
    unlimited = MembershipPlan.query.filter_by(name="Monthly unlimited").first()
    unlimited_id = unlimited.id

r = client.post("/account/switch", data={"plan_id": str(unlimited_id)},
                follow_redirects=True)
check("switch flashes success", "Switched" in r.get_data(as_text=True), "")

with app.app_context():
    old = db.session.get(Membership, old_id)
    check("old card marked replaced", old.status == "replaced", "status=%s" % old.status)
    u = User.query.filter_by(email="xin@example.com").first()
    actives = [m for m in u.memberships if m.status == "active"]
    check("new card is the only active one", len(actives) == 1, "active=%d" % len(actives))
    new = actives[0]
    check("new card has the new plan", new.plan_id == unlimited_id, "plan=%d" % new.plan_id)
    check("new card starts today", new.start == date.today(), "start=%s" % new.start)
    check("new card unlimited credits", new.credits_remaining is None,
          "credits=%s" % new.credits_remaining)
    # bookings made on the old card still point at the old card
    b = Booking.query.filter_by(user_id=u.id, status="Booked").first()
    check("old bookings stay on the old card", b.membership_id == old_id,
          "membership_id=%s" % b.membership_id)
    changes = PlanChange.query.filter_by(membership_id=old_id, kind="switch").all()
    check("switch recorded in history", len(changes) == 1, "rows=%d" % len(changes))


# 5. expiry notice + grey button --------------------------------------------
with app.app_context():
    u = User.query.filter_by(email="xin@example.com").first()
    new = [m for m in u.memberships if m.status == "active"][0]
    new.end = date.today() - timedelta(days=1)  # force expiry
    db.session.commit()

r = client.get("/timetable")
body = r.get_data(as_text=True)
check("timetable shows expiry notice", "expired" in body.lower() or "expired" in body, "")
check("timetable shows a grey reason button", 'class="disabled"' in body, "")

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
