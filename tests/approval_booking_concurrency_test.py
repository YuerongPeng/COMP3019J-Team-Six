# -*- coding: utf-8 -*-
"""An approval and another booking must not leave one member double-booked."""
import os
import sys
import threading

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import Booking, ClassChangeProposal, ClassSession, User  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "approval_booking_concurrency.db")
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
    alex = User.query.filter_by(email="alex@example.com").first()
    source = (ClassSession.query.join(Booking)
              .filter(Booking.user_id == alex.id,
                      Booking.status == "Booked")
              .order_by(ClassSession.start).first())
    target = (ClassSession.query
              .filter(ClassSession.id != source.id,
                      ClassSession.start > source.end)
              .order_by(ClassSession.start.desc()).first())
    source_id = source.id
    target_id = target.id
    payload = {
        "class_type_id": str(source.class_type_id),
        "instructor_id": str(source.instructor_id),
        "room_id": str(source.room_id),
        "start": target.start.strftime("%Y-%m-%dT%H:%M"),
        "end": target.end.strftime("%Y-%m-%dT%H:%M"),
        "capacity": str(source.capacity),
        "base_class_type_id": str(source.class_type_id),
        "base_instructor_id": str(source.instructor_id),
        "base_room_id": str(source.room_id),
        "base_start": source.start.strftime("%Y-%m-%dT%H:%M"),
        "base_end": source.end.strftime("%Y-%m-%dT%H:%M"),
        "base_capacity": str(source.capacity),
    }

staff = app.test_client()
staff.post("/login", data={"email": "staff@example.com", "password": "password"})
response = staff.post("/admin/classes/%d/edit" % source_id, data=payload)
assert response.status_code == 303

with app.app_context():
    proposal = ClassChangeProposal.query.filter_by(
        class_session_id=source_id, status="Pending").first()
    assert proposal is not None
    proposal_id = proposal.id
    approver_email = proposal.target_instructor.user.email

barrier = threading.Barrier(2)
results = []


def approve():
    client = app.test_client()
    client.post("/login", data={"email": approver_email, "password": "password"})
    barrier.wait()
    response = client.post("/instructor/proposals/%d/accept" % proposal_id,
                           follow_redirects=True)
    results.append(("approve", response.status_code))


def book_other_class():
    client = app.test_client()
    client.post("/login", data={"email": "alex@example.com", "password": "password"})
    barrier.wait()
    response = client.post("/classes/%d/book" % target_id,
                           follow_redirects=True)
    results.append(("book", response.status_code))


threads = [threading.Thread(target=approve), threading.Thread(target=book_other_class)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

with app.app_context():
    alex = User.query.filter_by(email="alex@example.com").first()
    booked = [booking for booking in alex.bookings if booking.status == "Booked"]
    overlaps = []
    for index, first in enumerate(booked):
        for second in booked[index + 1:]:
            if (first.session.start < second.session.end
                    and second.session.start < first.session.end):
                overlaps.append((first.class_session_id, second.class_session_id))
    proposal = db.session.get(ClassChangeProposal, proposal_id)
    print("results:", results)
    print("proposal:", proposal.status)
    print("overlaps:", overlaps)
    assert not overlaps, "approval and booking created overlapping classes: %r" % (overlaps,)
    assert (proposal.status != "Accepted"
            or not any(booking.class_session_id == target_id for booking in booked))
    print("PASS: concurrent approval and booking did not double-book the member")

with app.app_context():
    db.session.remove()
    db.engine.dispose()
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
