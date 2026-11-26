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
    from app.models import ClassType, Instructor, InstructorSkill
    maya = Instructor.query.filter_by(name="Maya Lin").first()
    pilates = ClassType.query.filter_by(name="Pilates").first()
    skill = InstructorSkill.query.filter_by(
        instructor_id=maya.id, class_type_id=pilates.id).first()
    check("seed lets Maya teach Pilates", skill is not None)

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

# --------------------------------------- late time change ends at the start
with app.app_context():
    from app.booking import cancel_block
    mei = User.query.filter_by(email="mei@example.com").first()
    late = Booking.query.filter_by(user_id=mei.id, class_session_id=target_id, status="Booked").first()
    late.late_change_cancel = True
    late_id = late.id
    late_credits = late.membership.credits_remaining
    started = late.session.start
    check("late time change still allows cancel before the start",
          cancel_block(late, started - timedelta(minutes=30))["disabled"] is False)
    check("late time change blocks cancel once the class has started",
          cancel_block(late, started + timedelta(minutes=1))["disabled"] is True)
    late.session.start = datetime.now() - timedelta(minutes=30)
    late.session.end = datetime.now() + timedelta(minutes=30)
    db.session.commit()

r = client.post("/logout")
r = client.post("/login", data={"email": "mei@example.com", "password": "password"})
r = client.post("/bookings/%d/cancel" % late_id, follow_redirects=True)
check("cancel after the start is refused",
      "has started" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    kept = db.session.get(Booking, late_id)
    check("booking stays booked after the refused cancel", kept.status == "Booked", kept.status)
    check("no credit is returned after the class has started",
          kept.membership.credits_remaining == late_credits,
          str(kept.membership.credits_remaining))

# ------------------------- a class in progress cannot be booked or queued
with app.app_context():
    sample = ClassSession.query.filter(ClassSession.start > datetime.now()).first()
    live = ClassSession(
        class_type_id=sample.class_type_id,
        instructor_id=sample.instructor_id,
        room_id=sample.room_id,
        start=datetime.now() - timedelta(minutes=20),
        end=datetime.now() + timedelta(minutes=40),
        capacity=8,
    )
    db.session.add(live)
    db.session.commit()
    live_id = live.id
    mei = User.query.filter_by(email="mei@example.com").first()
    live_credits = [m for m in mei.memberships if m.status == "active"][0].credits_remaining
    future_id = sample.id
    future_start = sample.start

r = client.post("/classes/%d/book" % live_id, follow_redirects=True)
check("in-progress class cannot be booked",
      "has started" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
r = client.get("/classes/%d" % live_id)
check("in-progress class shows a started button",
      "Started" in r.get_data(as_text=True))
r = client.post("/classes/%d/wait" % live_id, follow_redirects=True)
check("in-progress class cannot be joined on the waitlist",
      "has started" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    mei = User.query.filter_by(email="mei@example.com").first()
    credits_now = [m for m in mei.memberships if m.status == "active"][0].credits_remaining
    queued = Booking.query.filter_by(user_id=mei.id, class_session_id=live_id).count()
    check("in-progress book did not take a credit", credits_now == live_credits, str(credits_now))
    check("in-progress class gained no booking", queued == 0, str(queued))

r = client.post("/logout")
r = client.post("/login", data={"email": "staff@example.com", "password": "password"})
past_start = (datetime.now() - timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
past_end = past_start + timedelta(hours=1)
r = client.post("/admin/classes/%d/edit" % future_id,
                data=edit_payload(future_id,
                                  start=past_start.strftime("%Y-%m-%dT%H:%M"),
                                  end=past_end.strftime("%Y-%m-%dT%H:%M")),
                follow_redirects=True)
check("a future class cannot be moved into the past",
      "already in the past" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    kept = db.session.get(ClassSession, future_id)
    check("the future class time was not moved", kept.start == future_start,
          kept.start.strftime("%Y-%m-%d %H:%M"))
    before = ClassSession.query.count()
    new_type = kept.class_type_id
    new_instructor = kept.instructor_id
    new_room = kept.room_id

r = client.post("/admin/classes/new", data={
    "class_type_id": str(new_type),
    "instructor_id": str(new_instructor),
    "room_id": str(new_room),
    "start": past_start.strftime("%Y-%m-%dT%H:%M"),
    "end": past_end.strftime("%Y-%m-%dT%H:%M"),
    "capacity": "8",
}, follow_redirects=True)
check("a new class cannot start in the past",
      "already in the past" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    check("no past class was created", ClassSession.query.count() == before, str(ClassSession.query.count()))

# -------------------------------- an offer occupies the member's time
r = client.post("/logout")
r = client.post("/login", data={"email": "mei@example.com", "password": "password"})
with app.app_context():
    sample = ClassSession.query.first()
    start = datetime.now() + timedelta(days=12)
    end = start + timedelta(hours=1)
    held = ClassSession(class_type_id=sample.class_type_id, instructor_id=sample.instructor_id,
                        room_id=sample.room_id, start=start, end=end, capacity=6)
    other = ClassSession(class_type_id=sample.class_type_id, instructor_id=sample.instructor_id,
                         room_id=sample.room_id, start=start, end=end, capacity=6)
    db.session.add_all([held, other])
    db.session.flush()
    mei = User.query.filter_by(email="mei@example.com").first()
    card = [m for m in mei.memberships if m.status == "active"][0]
    offer_credits = card.credits_remaining
    db.session.add(Booking(user_id=mei.id, class_session_id=held.id, membership_id=card.id,
                           status="Offered", offered_at=datetime.utcnow()))
    passed = Booking(user_id=mei.id, class_session_id=other.id, membership_id=card.id,
                     status="Passed")
    db.session.add(passed)
    db.session.commit()
    other_id = other.id
    passed_id = passed.id

r = client.post("/classes/%d/book" % other_id, follow_redirects=True)
check("an offer blocks an overlapping booking",
      "overlap" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    mei = User.query.filter_by(email="mei@example.com").first()
    card = [m for m in mei.memberships if m.status == "active"][0]
    still = Booking.query.filter_by(user_id=mei.id, class_session_id=other_id, status="Booked").first()
    check("the overlapping book was not saved", still is None)
    check("the offer did not spend a credit", card.credits_remaining == offer_credits,
          str(card.credits_remaining))

before_utc = datetime.utcnow()
r = client.post("/bookings/%d/rejoin" % passed_id, follow_redirects=True)
check("rejoin is accepted", "queue" in r.get_data(as_text=True).lower(), r.get_data(as_text=True)[:180])
with app.app_context():
    rejoined = db.session.get(Booking, passed_id)
    check("rejoin uses the same clock as the rest of the queue",
          before_utc <= rejoined.booked_at <= datetime.utcnow() + timedelta(seconds=2),
          str(rejoined.booked_at))

# ------------------------------------------------ week, queue
with app.app_context():
    from app.models import ClassType, Instructor
    pilates = (ClassSession.query
               .join(ClassType, ClassSession.class_type_id == ClassType.id)
               .filter(ClassType.name == "Pilates")
               .first())
    strength = (ClassSession.query
                .join(ClassType, ClassSession.class_type_id == ClassType.id)
                .filter(ClassType.name == "Strength")
                .first())
    week = (pilates.start - timedelta(days=pilates.start.weekday())).strftime("%Y-%m-%d")
    rob_id = Instructor.query.filter_by(name="Rob Diaz").first().id
    sample = ClassSession.query.first()
    start = datetime.now() + timedelta(days=20)
    queued = ClassSession(class_type_id=sample.class_type_id, instructor_id=sample.instructor_id,
                          room_id=sample.room_id, start=start, end=start + timedelta(hours=1),
                          capacity=8)
    db.session.add(queued)
    db.session.flush()
    mei = User.query.filter_by(email="mei@example.com").first()
    card = [m for m in mei.memberships if m.status == "active"][0]
    db.session.add(Booking(user_id=mei.id, class_session_id=queued.id, membership_id=card.id,
                           status="Waiting", booked_at=datetime.utcnow()))
    db.session.commit()
    queued_id = queued.id
    pilates_id = pilates.id

r = client.get("/timetable?week=%s&slot=morning" % week)
body = r.get_data(as_text=True)
check("morning filter keeps the morning class",
      ">Pilates</a>" in body and ">Strength</a>" not in body, body[:180])
r = client.get("/timetable?week=%s&instructor=%d" % (week, rob_id))
body = r.get_data(as_text=True)
check("instructor filter keeps that instructor",
      ">Strength</a>" in body and ">Pilates</a>" not in body, body[:180])
r = client.get("/classes/%d" % pilates_id)
body = r.get_data(as_text=True)
check("class page shows the introduction and the rules",
      "control and posture" in body and "Yoga and mobility" in body
      and "two hours" in body and "30 minutes" in body, body[:220])
r = client.get("/classes/%d" % queued_id)
check("waitlist order marks this member", "You are here" in r.get_data(as_text=True), "")
r = client.get("/my-bookings")
check("waitlist rank is on my bookings", "1 of 1" in r.get_data(as_text=True), "")
r = client.get("/absences")
check("absences page is gone", r.status_code == 404, str(r.status_code))
r = client.get("/timetable")
body = r.get_data(as_text=True)
check("booking buttons are marked to submit once",
      "data-once" in body and "Absences" not in body, "")

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
