# -*- coding: utf-8 -*-
"""Concurrent timetable writes cannot double-book a room or instructor."""
import os
import sys
import threading
from datetime import date, datetime, timedelta

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import ClassSession, ClassType, Instructor, Room  # noqa: E402
from app.seed import seed  # noqa: E402

DB = os.path.join(HERE, "schedule_resource_concurrency.db")
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
    yoga = ClassType.query.filter_by(name="Yoga").first()
    maya = Instructor.query.filter_by(name="Maya Lin").first()
    studio = Room.query.filter_by(name="Studio 2").first()
    maya_id = maya.id
    studio_id = studio.id
    day = date.today() + timedelta(days=30)
    start = datetime(day.year, day.month, day.day, 12, 0)
    end = start + timedelta(hours=1)
    payload = {
        "class_type_id": str(yoga.id),
        "instructor_id": str(maya_id),
        "room_id": str(studio_id),
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": end.strftime("%Y-%m-%dT%H:%M"),
        "capacity": "10",
    }

barrier = threading.Barrier(2)
results = []


def create_same_class():
    client = app.test_client()
    client.post("/login", data={"email": "staff@example.com", "password": "password"})
    barrier.wait()
    response = client.post("/admin/classes/new", data=payload,
                           follow_redirects=True)
    results.append((response.status_code, response.get_data(as_text=True).lower()))


threads = [threading.Thread(target=create_same_class) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

with app.app_context():
    rows = (ClassSession.query
            .filter_by(instructor_id=maya_id, room_id=studio_id,
                       start=start, end=end).all())
    print("responses:", [status for status, _ in results])
    print("messages:", [body[body.find('<ul class=\"flash\"'):body.find('</ul>') + 5]
                         for _, body in results])
    print("matching classes:", len(rows))
    assert all(status == 200 for status, _ in results)
    assert len(rows) == 1, "concurrent writes created %d identical resource bookings" % len(rows)
    assert any("already teaching" in body or "already in use" in body
               for _, body in results)
    print("PASS: one instructor and room were assigned to only one class")

with app.app_context():
    db.session.remove()
    db.engine.dispose()
try:
    if os.path.exists(DB):
        os.remove(DB)
except OSError:
    pass
