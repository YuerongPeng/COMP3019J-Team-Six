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

from datetime import timedelta  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Booking, ClassSession, User  # noqa: E402
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
check("second booking refused",
      "full" in body or "already" in body or "已满" in body or "已订" in body or "已预订" in body,
      "no refusal text")

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
    moved = db.session.get(ClassSession, move_id)
    check("bookings stay after a legal move", moved.booked_count == 1,
          "booked=%d" % moved.booked_count)

r = client.get("/admin/plans")
check("staff plans 200", r.status_code == 200, str(r.status_code))

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
