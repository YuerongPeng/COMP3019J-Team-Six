# -*- coding: utf-8 -*-
"""Focused check: a staff edit must not put a member in two classes at once.

Run from the project root:  python tests/edit_clash_test.py
"""
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from datetime import timedelta  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import ClassSession  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "edit_clash.db")
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
    # session 1: yoga, day +1 18:00;  session 3: pilates, day +2 09:00
    yoga = ClassSession.query.filter_by(class_type_id=1).order_by(ClassSession.start).first()
    pilates = ClassSession.query.filter_by(class_type_id=3).first()
    yoga_id, pilates_id = yoga.id, pilates.id
    pilates_start = pilates.start

client = app.test_client()

# alex books both classes, so he holds two non-overlapping bookings
client.post("/login", data={"email": "alex@example.com", "password": "password"})
r1 = client.post("/classes/%d/book" % yoga_id)
r2 = client.post("/classes/%d/book" % pilates_id)
check("member holds two bookings", r1.status_code == 303 and r2.status_code == 303,
      "%s / %s" % (r1.status_code, r2.status_code))
client.post("/logout")

client.post("/login", data={"email": "staff@example.com", "password": "password"})


def payload(start, end, instructor_id, room_id, capacity):
    with app.app_context():
        row = db.session.get(ClassSession, yoga_id)
        return {
            "class_type_id": str(row.class_type_id),
            "instructor_id": str(instructor_id),
            "room_id": str(room_id),
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "capacity": str(capacity),
            "base_class_type_id": str(row.class_type_id),
            "base_instructor_id": str(row.instructor_id),
            "base_room_id": str(row.room_id),
            "base_start": row.start.strftime("%Y-%m-%dT%H:%M"),
            "base_end": row.end.strftime("%Y-%m-%dT%H:%M"),
            "base_capacity": str(row.capacity),
        }


# 1. move the yoga class onto the hour alex already holds -> must be refused
data = payload(pilates_start, pilates_start + timedelta(hours=1), instructor_id=2,
               room_id=3, capacity=14)
r = client.post("/admin/classes/%d/edit" % yoga_id, data=data, follow_redirects=True)
body = r.get_data(as_text=True).lower()
check("move onto a member's other class refused", "two classes at once" in body, "no refusal text")

with app.app_context():
    row = db.session.get(ClassSession, yoga_id)
    still_booked = sum(1 for b in row.bookings if b.status == "Booked")
    check("nothing written on refusal", row.start != pilates_start and still_booked == 1,
          "start=%s booked=%d" % (row.start, still_booked))

# 2. move it to a free hour -> allowed, bookings stay attached
free_start = pilates_start + timedelta(hours=3)
data = payload(free_start, free_start + timedelta(hours=1), instructor_id=2, room_id=3, capacity=14)
r = client.post("/admin/classes/%d/edit" % yoga_id, data=data, follow_redirects=True)
body = r.get_data(as_text=True).lower()
check("move to a free hour allowed", r.status_code == 200 and "two classes" not in body,
      str(r.status_code))

with app.app_context():
    row = db.session.get(ClassSession, yoga_id)
    check("bookings stay after a legal move",
          sum(1 for b in row.bookings if b.status == "Booked") == 1 and row.start == free_start,
          "booked=%d start=%s" % (sum(1 for b in row.bookings if b.status == "Booked"), row.start))
    db.session.remove()
    db.engine.dispose()

failed = [x for x in results if not x[1]]
for name, ok, detail in results:
    print(("PASS  " if ok else "FAIL  ") + name + (("   (" + detail + ")") if detail and not ok else ""))
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
sys.exit(1 if failed else 0)
