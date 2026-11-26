# -*- coding: utf-8 -*-
"""Notification feature checks.

A schedule change that takes effect (instructor, room, time or class type) leaves
one notification for every booked member and for the staff. A declined change
notifies the staff only. Notifications show with an unread badge and are cleared
on the Notifications page.

Run from the project root:  python tests/notification_test.py
"""
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import (Booking, ClassChangeProposal, ClassSession,  # noqa: E402
                        Instructor, Notification, User)
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "notification.db")
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


def edit_payload(session_id, **changes):
    with app.app_context():
        r = db.session.get(ClassSession, session_id)
        data = {
            "class_type_id": str(r.class_type_id),
            "instructor_id": str(r.instructor_id),
            "room_id": str(r.room_id),
            "start": r.start.strftime("%Y-%m-%dT%H:%M"),
            "end": r.end.strftime("%Y-%m-%dT%H:%M"),
            "capacity": str(r.capacity),
            "base_class_type_id": str(r.class_type_id),
            "base_instructor_id": str(r.instructor_id),
            "base_room_id": str(r.room_id),
            "base_start": r.start.strftime("%Y-%m-%dT%H:%M"),
            "base_end": r.end.strftime("%Y-%m-%dT%H:%M"),
            "base_capacity": str(r.capacity),
        }
        for key, value in changes.items():
            data[key] = value
        return data


def notif_count(user_id):
    with app.app_context():
        return Notification.query.filter_by(user_id=user_id).count()


# find the class alex is booked in, and an instructor who can replace the current one
with app.app_context():
    alex = User.query.filter_by(email="alex@example.com").first()
    staff = User.query.filter_by(email="staff@example.com").first()
    booking = Booking.query.filter_by(user_id=alex.id, status="Booked").first()
    class_id = booking.class_session_id
    row = db.session.get(ClassSession, class_id)
    original_instructor = row.instructor.name
    original_instructor_id = str(row.instructor_id)
    original_email = row.instructor.user.email
    replacement = Instructor.query.filter(
        Instructor.id != row.instructor_id,
        Instructor.skills.any(class_type_id=row.class_type_id),
    ).first()
    replacement_id = str(replacement.id)
    replacement_email = replacement.user.email

    check("alex has a booked class to change", class_id is not None, "")
    check("a replacement instructor exists", replacement is not None, "")

# staff changes the instructor -> creates a proposal for the new instructor
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
check("staff login 303", r.status_code == 303, str(r.status_code))

r = client.post("/admin/classes/%d/edit" % class_id,
                data=edit_payload(class_id, instructor_id=replacement_id),
                follow_redirects=True)
check("instructor change sent for approval",
      "sent to" in r.get_data(as_text=True).lower(), "")

# nothing is notified until the change actually takes effect
check("member has no notification before approval", notif_count(alex.id) == 0, "")
check("staff has no notification before approval", notif_count(staff.id) == 0, "")

# the new instructor accepts -> the class changes and everyone is notified
client.post("/logout")
client.post("/login", data={"email": replacement_email, "password": "password"})
with app.app_context():
    proposal = ClassChangeProposal.query.filter_by(
        class_session_id=class_id, status="Pending").first()
    proposal_id = proposal.id
r = client.post("/instructor/proposals/%d/accept" % proposal_id, follow_redirects=True)
check("instructor accepted the change",
      "change accepted" in r.get_data(as_text=True).lower(), "")

with app.app_context():
    row = db.session.get(ClassSession, class_id)
    check("class instructor actually changed",
          row.instructor.name != original_instructor, "still %s" % row.instructor.name)
    member_notices = Notification.query.filter_by(user_id=alex.id).all()
    check("one notification for the booked member",
          len(member_notices) == 1, "count=%d" % len(member_notices))
    check("member notification mentions the instructor",
          member_notices and "instructor is now" in member_notices[0].message,
          member_notices[0].message if member_notices else "")
    check("member notification starts unread",
          member_notices and member_notices[0].read is False, "")
    notice_id = member_notices[0].id if member_notices else None

    staff_notices = Notification.query.filter_by(user_id=staff.id).all()
    check("one notification for staff",
          len(staff_notices) == 1, "count=%d" % len(staff_notices))
    check("staff notification mentions the instructor",
          staff_notices and "instructor is now" in staff_notices[0].message, "")

# alex sees the badge and the message
client.post("/logout")
client.post("/login", data={"email": "alex@example.com", "password": "password"})
r = client.get("/my-bookings")
body = r.get_data(as_text=True)
check("member nav shows the unread badge", 'class="badge"' in body, "")
check("member badge count is 1", ">1<" in body, "")

r = client.get("/notifications")
check("member notifications page 200", r.status_code == 200, str(r.status_code))
check("member notifications page shows the message",
      "instructor is now" in r.get_data(as_text=True), "")

r = client.post("/notifications/%d/read" % notice_id, follow_redirects=True)
with app.app_context():
    n = db.session.get(Notification, notice_id)
    check("mark one read", n.read is True, "read=%s" % n.read)

# staff sees the badge, the page, and can mark all read
client.post("/logout")
client.post("/login", data={"email": "staff@example.com", "password": "password"})
r = client.get("/admin/classes")
check("staff nav shows the unread badge", 'class="badge"' in r.get_data(as_text=True), "")
r = client.get("/notifications")
check("staff notifications page 200", r.status_code == 200, str(r.status_code))
check("staff notifications page shows the message",
      "instructor is now" in r.get_data(as_text=True), "")

r = client.post("/notifications/read-all", follow_redirects=True)
with app.app_context():
    unread = Notification.query.filter_by(user_id=staff.id, read=False).count()
    check("staff mark all read", unread == 0, "unread=%d" % unread)

# a declined change notifies the staff only
with app.app_context():
    member_before = notif_count(alex.id)
    staff_before = notif_count(staff.id)
r = client.post("/admin/classes/%d/edit" % class_id,
                data=edit_payload(class_id, instructor_id=original_instructor_id),
                follow_redirects=True)
client.post("/logout")
client.post("/login", data={"email": original_email, "password": "password"})
with app.app_context():
    proposal = ClassChangeProposal.query.filter_by(
        class_session_id=class_id, status="Pending").first()
    proposal_id = proposal.id
r = client.post("/instructor/proposals/%d/decline" % proposal_id, follow_redirects=True)
check("instructor declined the change",
      "declined" in r.get_data(as_text=True).lower(), "")

with app.app_context():
    check("member got no notification from the decline",
          notif_count(alex.id) == member_before, "")
    check("staff got a decline notification",
          notif_count(staff.id) == staff_before + 1, "")
    latest = Notification.query.filter_by(user_id=staff.id).order_by(
        Notification.id.desc()).first()
    check("decline notification text is clear",
          latest is not None and "declined" in latest.message, "")

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
