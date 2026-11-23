# -*- coding: utf-8 -*-
"""Concurrent membership actions must preserve every paid renewal."""
import os
import sys
import threading
from datetime import date, datetime, timedelta

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.models import (Booking, ClassSession, Membership, MembershipPlan,
                        PlanChange, User)  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "membership_concurrency.db")
if os.path.exists(DB):
    os.remove(DB)


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
    TESTING = True


app = create_app(TestConfig)
with app.app_context():
    db.create_all()
    seed()
    mei = User.query.filter_by(email="mei@example.com").first()
    card = Membership.query.filter_by(user_id=mei.id, status="active").first()
    card_id = card.id
    original_end = card.end
    duration = card.plan.duration_days

barrier = threading.Barrier(2)
results = []


def renew_same_card():
    client = app.test_client()
    client.post("/login", data={"email": "mei@example.com", "password": "password"})
    barrier.wait()
    response = client.post("/account/renew", follow_redirects=True)
    results.append((response.status_code, response.get_data(as_text=True).lower()))


threads = [threading.Thread(target=renew_same_card) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

with app.app_context():
    card = db.session.get(Membership, card_id)
    changes = PlanChange.query.filter_by(
        membership_id=card_id, kind="renew").all()
    from datetime import timedelta
    expected_end = original_end + timedelta(days=duration * 2)
    print("responses:", [status for status, _ in results])
    print("end:", card.end, "expected:", expected_end)
    print("renew rows:", len(changes))
    assert all(status == 200 for status, _ in results)
    assert card.end == expected_end
    assert len(changes) == 2
    print("PASS: two concurrent renewals extended the card twice")

# Two different switches submitted together must still leave one active card.
with app.app_context():
    current = db.session.get(Membership, card_id)
    targets = (MembershipPlan.query
               .filter(MembershipPlan.id != current.plan_id)
               .order_by(MembershipPlan.id).limit(2).all())
    target_ids = [plan.id for plan in targets]

barrier = threading.Barrier(2)
switch_results = []


def switch_plan(plan_id):
    client = app.test_client()
    client.post("/login", data={"email": "mei@example.com", "password": "password"})
    barrier.wait()
    response = client.post("/account/switch", data={"plan_id": str(plan_id)},
                           follow_redirects=True)
    switch_results.append(response.status_code)


threads = [threading.Thread(target=switch_plan, args=(plan_id,))
           for plan_id in target_ids]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

with app.app_context():
    mei = User.query.filter_by(email="mei@example.com").first()
    cards = Membership.query.filter_by(user_id=mei.id).all()
    active = [card for card in cards if card.status == "active"]
    card_ids = [card.id for card in cards]
    switches = (PlanChange.query
                .filter(PlanChange.membership_id.in_(card_ids),
                        PlanChange.kind == "switch").all())
    print("switch responses:", switch_results)
    print("active cards:", len(active), "switch rows:", len(switches))
    assert all(status == 200 for status in switch_results)
    assert len(active) == 1
    assert len(switches) == 2
    print("PASS: concurrent switches left exactly one active membership")

# A duplicated cancellation must refund a pack credit exactly once.
with app.app_context():
    plan = MembershipPlan.query.filter_by(name="Ten-visit pack").first()
    member = User(name="Cancel Test", email="cancel@example.com", role="member",
                  password_hash=generate_password_hash("password"))
    db.session.add(member)
    db.session.flush()
    cancel_card = Membership(
        user_id=member.id, plan_id=plan.id, start=date.today(),
        end=date.today() + timedelta(days=plan.duration_days),
        credits_remaining=5, status="active",
    )
    db.session.add(cancel_card)
    db.session.flush()
    class_row = (ClassSession.query
                 .filter(ClassSession.start > datetime.now())
                 .order_by(ClassSession.start).first())
    booking = Booking(user_id=member.id, class_session_id=class_row.id,
                      membership_id=cancel_card.id, status="Booked")
    db.session.add(booking)
    db.session.commit()
    cancel_booking_id = booking.id
    cancel_card_id = cancel_card.id

barrier = threading.Barrier(2)
cancel_results = []


def cancel_same_booking():
    client = app.test_client()
    client.post("/login", data={"email": "cancel@example.com", "password": "password"})
    barrier.wait()
    response = client.post("/bookings/%d/cancel" % cancel_booking_id,
                           follow_redirects=True)
    cancel_results.append(response.status_code)


threads = [threading.Thread(target=cancel_same_booking) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

with app.app_context():
    booking = db.session.get(Booking, cancel_booking_id)
    cancel_card = db.session.get(Membership, cancel_card_id)
    print("cancel responses:", cancel_results)
    print("cancel status:", booking.status, "credits:", cancel_card.credits_remaining)
    assert all(status == 200 for status in cancel_results)
    assert booking.status == "Cancelled"
    assert cancel_card.credits_remaining == 6
    print("PASS: concurrent cancellation refunded exactly one credit")

with app.app_context():
    db.session.remove()
    db.engine.dispose()
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
