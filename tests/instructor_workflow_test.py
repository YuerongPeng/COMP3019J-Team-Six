# -*- coding: utf-8 -*-
"""Instructor access, availability requests, and staged schedule approvals."""
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import (ClassChangeProposal, ClassSession, Instructor,
                        InstructorAbsenceRequest, Room, User)  # noqa: E402
from app.seed import seed  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

DB = os.path.join(HERE, "instructor_workflow.db")
if os.path.exists(DB):
    os.remove(DB)


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + DB
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True


app = create_app(TestConfig)
with app.app_context():
    db.create_all()
    seed()
    maya = Instructor.query.filter_by(name="Maya Lin").first()
    yoga = (ClassSession.query.filter_by(instructor_id=maya.id)
            .order_by(ClassSession.start).first())
    session_id = yoga.id
    other_instructor_class = ClassSession.query.filter(
        ClassSession.instructor_id != maya.id).first().id
    maya_user_id = maya.user_id
    quinn = Instructor.query.filter_by(name="Quinn He").first()
    quinn.user_id = maya_user_id
    try:
        db.session.commit()
        unique_link_rejected = False
    except IntegrityError:
        db.session.rollback()
        unique_link_rejected = True
    quinn = Instructor.query.filter_by(name="Quinn He").first()
    check_unique = unique_link_rejected and quinn.user_id != maya_user_id
    absence_id = (ClassSession.query.filter_by(instructor_id=maya.id)
                  .filter(ClassSession.id != session_id).first().id)
    unlinked = Instructor(name="Taylor Test", profile="Cycling")
    db.session.add(unlinked)
    db.session.commit()
    unlinked_id = unlinked.id
    original_room_id = yoga.room_id
    new_room_id = Room.query.filter(Room.id != original_room_id).first().id
    db.session.commit()

client = app.test_client()
checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


check("one login cannot link to two instructor records", check_unique)
login_response = client.post("/login", data={"email": "maya@example.com", "password": "password"})
r = client.get("/instructor/classes")
check("instructor sees own classes", r.status_code == 200 and "Yoga" in r.get_data(as_text=True))
check("instructor is redirected to own area after login",
      login_response.status_code == 303 and
      "/instructor/classes" in login_response.headers.get("Location", ""))
r = client.get("/classes/%d/attendance" % session_id)
check("instructor can open own class list", r.status_code == 200 and "Alex Chen" in r.get_data(as_text=True))
r = client.get("/classes/%d/attendance" % other_instructor_class)
check("instructor cannot open another coach's list", r.status_code == 403)
check("instructor cannot access member account pages",
      client.get("/account").status_code == 403 and
      client.get("/my-bookings").status_code == 403)
r = client.post("/classes/%d/cannot-teach" % absence_id)
check("instructor can request cover", r.status_code == 303)
with app.app_context():
    absence = InstructorAbsenceRequest.query.filter_by(class_session_id=absence_id).first()
    check("cover request does not alter the class", absence is not None and
          db.session.get(ClassSession, absence_id).instructor.name == "Maya Lin")
client.post("/logout")

r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
r = client.post("/admin/instructors/accounts", data={
    "instructor_id": str(unlinked_id), "name": "Taylor Test",
    "email": "taylor@example.com", "password": "secret1",
})
check("staff creates and links an instructor login", r.status_code == 303)
with app.app_context():
    linked = Instructor.query.get(unlinked_id)
    check("linked login has instructor role", linked.user.role == "instructor")
r = client.get("/admin/change-requests")
check("staff sees cover requests", r.status_code == 200 and "Maya Lin" in r.get_data(as_text=True))
with app.app_context():
    row = db.session.get(ClassSession, session_id)
    payload = {
        "class_type_id": str(row.class_type_id),
        "instructor_id": str(row.instructor_id),
        "room_id": str(new_room_id),
        "start": row.start.strftime("%Y-%m-%dT%H:%M"),
        "end": row.end.strftime("%Y-%m-%dT%H:%M"),
        "capacity": str(row.capacity),
        "base_class_type_id": str(row.class_type_id),
        "base_instructor_id": str(row.instructor_id),
        "base_room_id": str(row.room_id),
        "base_start": row.start.strftime("%Y-%m-%dT%H:%M"),
        "base_end": row.end.strftime("%Y-%m-%dT%H:%M"),
        "base_capacity": str(row.capacity),
    }
r = client.post("/admin/classes/%d/edit" % session_id, data=payload)
check("staff edit waits for instructor approval", r.status_code == 303)
with app.app_context():
    row = db.session.get(ClassSession, session_id)
    proposal = ClassChangeProposal.query.filter_by(class_session_id=session_id).first()
    proposal_id = proposal.id if proposal else None
    check("timetable source stays unchanged before approval",
          row.room_id == original_room_id and proposal is not None and proposal.status == "Pending")
client.post("/logout")

client.post("/login", data={"email": "alex@example.com", "password": "password"})
r = client.get("/instructor/classes")
check("member cannot open instructor schedule", r.status_code == 403)
client.post("/logout")

client.post("/login", data={"email": "maya@example.com", "password": "password"})
r = client.post("/instructor/proposals/%d/accept" % proposal_id)
check("assigned instructor can accept proposal", r.status_code == 303)
with app.app_context():
    row = db.session.get(ClassSession, session_id)
    proposal = db.session.get(ClassChangeProposal, proposal_id)
    check("acceptance updates original class row", row.room_id == new_room_id and
          proposal.status == "Accepted")
client.post("/logout")

client.post("/login", data={"email": "quinn@example.com", "password": "password"})
r = client.post("/instructor/proposals/%d/accept" % proposal_id)
check("another instructor cannot accept the proposal", r.status_code == 403)

failed = [name for name, ok in checks if not ok]
for name, ok in checks:
    print(("PASS  " if ok else "FAIL  ") + name)
print("\n%d/%d passed" % (len(checks) - len(failed), len(checks)))

with app.app_context():
    db.session.remove()
    db.engine.dispose()
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
sys.exit(1 if failed else 0)
