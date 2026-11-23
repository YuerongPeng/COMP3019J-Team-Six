# -*- coding: utf-8 -*-
"""Twenty checks over the routes, the roles and the rules.

Run from the project root:  python tests/smoke_test.py

Builds a throw-away database, seeds it, drives the routes with Flask's test client
and prints one line per check. Exit code 0 means everything passed.
"""
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from datetime import datetime, timedelta  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import (Booking, ClassChangeProposal, ClassSession, Membership,
                        User)  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "smoke.db")
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


def edit_payload(session_id, **changes):
    """Build a valid edit form, including the optimistic-concurrency stamps."""
    with app.app_context():
        row = db.session.get(ClassSession, session_id)
        data = {
            "class_type_id": str(row.class_type_id),
            "instructor_id": str(row.instructor_id),
            "room_id": str(row.room_id),
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
    for key, value in changes.items():
        data[key] = value
    return data


with app.app_context():
    db.create_all()
    rows = seed()
    check("seed loads 15 rows", rows == 15, "seed()=%s" % rows)

client = app.test_client()

# ------------------------------------------------------------------ read-only
r = client.get("/")
check("GET / redirects", r.status_code == 302 and "/timetable" in r.headers.get("Location", ""),
      r.headers.get("Location", ""))

r = client.get("/timetable")
check("timetable 200", r.status_code == 200 and "Yoga" in r.get_data(as_text=True),
      str(r.status_code))

with app.app_context():
    target = [s for s in ClassSession.query.all() if s.booked_count == 13][0]
    target_id = target.id
    target_start = target.start
    check("near-full class has 1 place", target.places_left == 1, "places=%d" % target.places_left)

r = client.get("/classes/%d" % target_id)
check("class page 200", r.status_code == 200, str(r.status_code))

r = client.get("/classes/999")
check("unknown class 404", r.status_code == 404, str(r.status_code))

# --------------------------------------------------------------------- member
r = client.post("/login", data={"email": "alex@example.com", "password": "password"})
check("login 303", r.status_code == 303, str(r.status_code))

r = client.get("/my-bookings")
check("my bookings 200", r.status_code == 200, str(r.status_code))

r = client.get("/admin/plans")
check("member blocked from admin 403", r.status_code == 403, str(r.status_code))

r = client.post("/classes/%d/book" % target_id)
check("book the last place 303", r.status_code == 303, str(r.status_code))

with app.app_context():
    after = db.session.get(ClassSession, target_id)
    check("places left now 0", after.places_left == 0, "places=%d" % after.places_left)

r = client.post("/classes/%d/book" % target_id, follow_redirects=True)
body = r.get_data(as_text=True).lower()
check("second booking refused", "full" in body or "already" in body, "no refusal text")

with app.app_context():
    alex = User.query.filter_by(email="alex@example.com").first()
    booking = Booking.query.filter_by(class_session_id=target_id, status="Booked",
                                      user_id=alex.id).first()
    booking_id = booking.id

r = client.post("/bookings/%d/cancel" % booking_id, follow_redirects=True)
check("cancel frees the place", "cancel" in r.get_data(as_text=True).lower(), "")

r = client.post("/logout")
check("logout 303", r.status_code == 303, str(r.status_code))

# give alex two live bookings, so a move can clash him with himself
r = client.post("/login", data={"email": "alex@example.com", "password": "password"})
r = client.post("/classes/%d/book" % target_id)
check("member holds a booking for the clash test", r.status_code == 303, str(r.status_code))
r = client.post("/logout")

# ---------------------------------------------------------------------- staff
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
check("staff login 303", r.status_code == 303, str(r.status_code))

r = client.get("/admin/classes/%d/roster" % target_id)
check("roster 200", r.status_code == 200, str(r.status_code))

# capacity below the number already booked must be refused
r = client.post("/admin/classes/%d/edit" % target_id,
                data=edit_payload(target_id, capacity="2"), follow_redirects=True)
check("capacity below booked refused", "below the" in r.get_data(as_text=True).lower()
      and "already booked" in r.get_data(as_text=True).lower(),
      "no refusal text")

# moving a class onto a time a booked member already occupies must be refused
with app.app_context():
    yoga = [s for s in ClassSession.query.all() if s.class_type_id == 1 and s.id != target_id]
    move_id = yoga[0].id
clash_start = target_start
clash_end = target_start + timedelta(hours=1)
r = client.post("/admin/classes/%d/edit" % move_id,
                data=edit_payload(move_id,
                                  start=clash_start.strftime("%Y-%m-%dT%H:%M"),
                                  end=clash_end.strftime("%Y-%m-%dT%H:%M"),
                                  instructor_id="1", room_id="3", capacity="14"),
                follow_redirects=True)
check("edit that would double-book a member refused",
      "two classes at once" in r.get_data(as_text=True).lower(), "no refusal text")

# a legal move still works and keeps the bookings
free_start = target_start + timedelta(hours=4)
r = client.post("/admin/classes/%d/edit" % move_id,
                data=edit_payload(move_id,
                                  start=free_start.strftime("%Y-%m-%dT%H:%M"),
                                  end=(free_start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                                  instructor_id="1", room_id="3", capacity="14"),
                follow_redirects=True)
check("legal move accepted",
      r.status_code == 200 and "two classes" not in r.get_data(as_text=True).lower(),
      str(r.status_code))

with app.app_context():
    proposal = ClassChangeProposal.query.filter_by(
        class_session_id=move_id, status="Pending").first()
    proposal_id = proposal.id
    approver_email = proposal.target_instructor.user.email

r = client.post("/logout")
r = client.post("/login", data={"email": approver_email, "password": "password"})
r = client.post("/instructor/proposals/%d/accept" % proposal_id,
                follow_redirects=True)
check("instructor accepts the legal move",
      "change accepted" in r.get_data(as_text=True).lower(), "no acceptance")
r = client.post("/logout")
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})

with app.app_context():
    moved = db.session.get(ClassSession, move_id)
    check("bookings stay after a legal move", moved.booked_count == 1,
          "booked=%d" % moved.booked_count)

r = client.get("/admin/plans")
check("staff plans 200", r.status_code == 200, str(r.status_code))

# ---------------------------------------------------------------- attendance
r = client.post("/logout")

with app.app_context():
    alex = User.query.filter_by(email="alex@example.com").first()
    own = [b for b in alex.bookings if b.status == "Booked" and b.class_session_id != target_id][0]
    attend_id = own.class_session_id
    alex_booking_id = own.id

r = client.post("/login", data={"email": "mei@example.com", "password": "password"})
r = client.post("/classes/%d/book" % attend_id)
check("mei books the attendance class", r.status_code == 303, str(r.status_code))
r = client.post("/logout")

with app.app_context():
    mei = User.query.filter_by(email="mei@example.com").first()
    mei_booking = Booking.query.filter_by(
        user_id=mei.id, class_session_id=attend_id, status="Booked").first()
    mei_booking_id = mei_booking.id
    credits_before = mei_booking.membership.credits_remaining
    places_before = db.session.get(ClassSession, attend_id).places_left

r = client.post("/login", data={"email": "maya@example.com", "password": "password"})
check("instructor login 303", r.status_code == 303, str(r.status_code))

r = client.get("/classes/%d/attendance" % attend_id)
body = r.get_data(as_text=True)
check("attendance waits until the class ends",
      r.status_code == 200 and "after the class ends" in body.lower() and 'name="result"' not in body,
      str(r.status_code))

r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(mei_booking_id), "result": "Attended"},
                follow_redirects=True)
check("mark before the end is refused",
      "after the class has ended" in r.get_data(as_text=True).lower(), "no refusal")

with app.app_context():
    still = db.session.get(Booking, mei_booking_id)
    check("early mark does not change the booking", still.status == "Booked", still.status)
    row = db.session.get(ClassSession, attend_id)
    row.start = datetime.now() - timedelta(hours=2)
    row.end = datetime.now() - timedelta(hours=1)
    db.session.commit()

r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(mei_booking_id), "result": "Attended"},
                follow_redirects=True)
check("instructor marks attended", "attendance saved" in r.get_data(as_text=True).lower(), "no save")

with app.app_context():
    marked = db.session.get(Booking, mei_booking_id)
    check("attended keeps the credit", marked.membership.credits_remaining == credits_before,
          str(marked.membership.credits_remaining))
    check("attended still holds the place",
          db.session.get(ClassSession, attend_id).places_left == places_before,
          str(db.session.get(ClassSession, attend_id).places_left))

r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(mei_booking_id), "result": "No-show"},
                follow_redirects=True)
check("instructor can switch to no-show", "attendance saved" in r.get_data(as_text=True).lower(), "no save")

with app.app_context():
    marked = db.session.get(Booking, mei_booking_id)
    check("no-show keeps the credit", marked.membership.credits_remaining == credits_before,
          str(marked.membership.credits_remaining))
    check("no-show still holds the place",
          db.session.get(ClassSession, attend_id).places_left == places_before,
          str(db.session.get(ClassSession, attend_id).places_left))
    check("no-show is the stored result", marked.status == "No-show", marked.status)
    mei = User.query.filter_by(email="mei@example.com").first()
    alex_user = User.query.filter_by(email="alex@example.com").first()
    card = Membership.query.filter_by(user_id=alex_user.id).first()
    db.session.add(Booking(user_id=mei.id, class_session_id=attend_id,
                           membership_id=marked.membership_id, status="Waiting"))
    db.session.add(Booking(user_id=card.user_id, class_session_id=attend_id,
                           membership_id=card.id, status="Offered", offered_at=datetime.now()))
    db.session.commit()

r = client.get("/classes/%d/attendance" % attend_id)
body = r.get_data(as_text=True)
check("finished class hides the queue", "Waiting" not in body and "Offered" not in body, "")

with app.app_context():
    waiting = Booking.query.filter_by(class_session_id=attend_id, status="Waiting").count()
    offered = Booking.query.filter_by(class_session_id=attend_id, status="Offered").count()
    check("finished class does not promote the queue", waiting == 0 and offered == 0,
          "waiting=%s offered=%s" % (waiting, offered))
    marked = db.session.get(Booking, mei_booking_id)
    check("voided offer does not refund a credit",
          marked.membership.credits_remaining == credits_before,
          str(marked.membership.credits_remaining))

r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(alex_booking_id), "result": "Attended"},
                follow_redirects=True)
check("second member can be marked", "attendance saved" in r.get_data(as_text=True).lower(), "no save")

r = client.post("/logout")
r = client.post("/login", data={"email": "alex@example.com", "password": "password"})
r = client.get("/my-bookings")
check("member sees attended in past bookings", "Attended" in r.get_data(as_text=True), "status missing")
r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(alex_booking_id), "result": "No-show"})
check("member cannot mark attendance", r.status_code == 403, str(r.status_code))

r = client.post("/logout")
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
r = client.get("/admin/classes/%d/roster" % attend_id)
body = r.get_data(as_text=True)
check("staff roster shows the result", "No-show" in body and 'name="result"' not in body, "buttons or missing status")
r = client.post("/classes/%d/attendance" % attend_id,
                data={"booking_id": str(mei_booking_id), "result": "Attended"})
check("staff cannot mark attendance", r.status_code == 403, str(r.status_code))

with app.app_context():
    current = db.session.get(ClassSession, attend_id)
    kept_start = current.start
    kept_end = current.end
    kept_capacity = current.capacity

new_start = (datetime.now() - timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
new_end = new_start + timedelta(hours=1)
r = client.post("/admin/classes/%d/edit" % attend_id,
                data=edit_payload(attend_id,
                                  start=new_start.strftime("%Y-%m-%dT%H:%M"),
                                  end=new_end.strftime("%Y-%m-%dT%H:%M")),
                follow_redirects=True)
body = r.get_data(as_text=True).lower()
check("started class refuses a time change",
      "has started" in body and "nothing was written" in body, body[:180])

with app.app_context():
    moved = db.session.get(ClassSession, attend_id)
    check("time stays after a started class refuses the edit",
          moved.start == kept_start and moved.end == kept_end,
          moved.start.strftime("%Y-%m-%d %H:%M"))

r = client.get("/admin/classes/%d/edit" % attend_id)
body = r.get_data(as_text=True)
check("started class shows the time fields locked",
      'id="start"' in body and "readonly" in body and "has started" in body.lower(),
      "lock missing")

r = client.post("/admin/classes/%d/edit" % attend_id,
                data=edit_payload(attend_id, capacity=str(kept_capacity - 1)),
                follow_redirects=True)
check("room and capacity can still be saved after the start",
      "class updated" in r.get_data(as_text=True).lower(),
      r.get_data(as_text=True)[:180])

with app.app_context():
    moved = db.session.get(ClassSession, attend_id)
    same_time = (moved.start.replace(second=0, microsecond=0) == kept_start.replace(second=0, microsecond=0)
                 and moved.end.replace(second=0, microsecond=0) == kept_end.replace(second=0, microsecond=0))
    check("a non-time save keeps the started class time",
          moved.capacity == kept_capacity - 1 and same_time,
          "capacity=%s start=%s" % (moved.capacity, moved.start.strftime("%Y-%m-%d %H:%M")))

# ---------------------------------------------------------------------- report
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
