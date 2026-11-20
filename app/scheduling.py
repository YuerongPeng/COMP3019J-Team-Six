# -*- coding: utf-8 -*-
"""Scheduling module (Peng).

Checks instructor experience, room suitability, overlaps and capacity when a class is
saved, and updates a class without changing its identity, so existing bookings stay.
"""
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from . import db
from .access import RuleError, staff_only
from .booking import booked_count, held_count
from .models import (Booking, ClassSession, ClassType, Instructor, Membership,
                     MembershipPlan, Room)

bp = Blueprint("scheduling", __name__)


def validate_class(class_row, existing=False):
    if not any(s.class_type_id == class_row.class_type_id
               for s in class_row.instructor.skills):
        raise RuleError("That instructor cannot teach this class type.")

    required = (class_row.class_type.required_equipment or "").lower()
    room_gear = (class_row.room.equipment or "").lower()
    for item in [x.strip() for x in required.split(",") if x.strip()]:
        if item not in room_gear:
            raise RuleError("That room lacks the equipment this class needs.")

    if class_row.capacity > class_row.room.capacity:
        raise RuleError("Capacity is above what the room holds.")

    clashes = (ClassSession.query
               .filter(ClassSession.id != class_row.id)
               .filter(ClassSession.start < class_row.end)
               .filter(ClassSession.end > class_row.start)
               .all())
    for other in clashes:
        if other.instructor_id == class_row.instructor_id:
            raise RuleError("That instructor is already teaching then.")
        if other.room_id == class_row.room_id:
            raise RuleError("That room is already in use then.")

    if existing:
        taken = held_count(class_row.id)
        if class_row.capacity < taken:
            raise RuleError(
                "That capacity is below the %d places already held. "
                "Remove bookings first, or cancel the class." % taken)
        # A change to the time, instructor or room must not leave a member in two
        # classes at once, and must not overload an instructor or a room.
        clashing = members_double_booked(class_row)
        if clashing:
            raise RuleError(
                "That time would put %d booked member(s) in two classes at once. "
                "Nothing was written." % clashing)


def members_double_booked(class_row):
    """How many members already booked here would clash with another booking."""
    affected = set()
    for booking in class_row.bookings:
        if booking.status not in ("Booked", "Offered"):
            continue
        for other in booking.user.bookings:
            if other.status not in ("Booked", "Offered") or other.class_session_id == class_row.id:
                continue
            if other.session.start < class_row.end and class_row.start < other.session.end:
                affected.add(booking.user_id)
                break
    return len(affected)


def _drop_clashing_waiters(class_row):
    """A queued member has no seat, so a time change must not be blocked by them.

    If the new time overlaps another class they already hold, remove them from
    this queue. Booked and offered members are still checked before this runs.
    """
    dropped = 0
    for booking in list(class_row.bookings):
        if booking.status != "Waiting":
            continue
        clashes = False
        for other in booking.user.bookings:
            if other.status not in ("Booked", "Offered") or other.class_session_id == class_row.id:
                continue
            if other.session.start < class_row.end and class_row.start < other.session.end:
                clashes = True
                break
        if clashes:
            booking.status = "Cancelled"
            booking.active = False
            booking.cancelled_at = datetime.utcnow()
            dropped += 1
    return dropped


def _opened_stamp(class_row):
    """The class as this form was built. A save must still match it."""
    return {
        "class_type_id": str(class_row.class_type_id),
        "instructor_id": str(class_row.instructor_id),
        "room_id": str(class_row.room_id),
        "start": class_row.start.strftime("%Y-%m-%dT%H:%M"),
        "end": class_row.end.strftime("%Y-%m-%dT%H:%M"),
        "capacity": str(class_row.capacity),
    }


def _still_the_opened_class(class_row, form):
    opened = _opened_stamp(class_row)
    return all(form.get("base_" + key) == value for key, value in opened.items())


def _apply_class_form(class_row, form):
    class_type = db.session.get(ClassType, int(form["class_type_id"]))
    instructor = db.session.get(Instructor, int(form["instructor_id"]))
    room = db.session.get(Room, int(form["room_id"]))
    if class_type is None or instructor is None or room is None:
        raise RuleError("That class type, instructor or room no longer exists.")
    start = datetime.fromisoformat(form["start"])
    end = datetime.fromisoformat(form["end"])
    if end <= start:
        raise RuleError("The class must end after it starts.")
    class_row.class_type = class_type
    class_row.instructor = instructor
    class_row.room = room
    class_row.start = start
    class_row.end = end
    class_row.capacity = int(form["capacity"])


def _keep_cancel_open(class_row, previous_start):
    """If the new start is inside the cut-off, booked members may still cancel.

    Returns how many bookings were given that chance. A start that stays at
    least two hours away clears the exception, because the normal rule applies.
    """
    if class_row.start == previous_start:
        return 0
    booked = [b for b in class_row.bookings if b.status == "Booked"]
    if class_row.start - datetime.now() >= timedelta(hours=2):
        for booking in booked:
            booking.late_change_cancel = False
        return 0
    for booking in booked:
        booking.late_change_cancel = True
    return len(booked)


@bp.get("/admin/classes")
@staff_only
def class_list():
    sessions = (ClassSession.query
                .order_by(ClassSession.start)
                .all())
    return render_template("classes_manage.html", sessions=sessions)


@bp.route("/admin/classes/new", methods=["GET", "POST"])
@staff_only
def class_new():
    if request.method == "POST":
        try:
            class_row = ClassSession(
                class_type_id=int(request.form["class_type_id"]),
                instructor_id=int(request.form["instructor_id"]),
                room_id=int(request.form["room_id"]),
                start=datetime.fromisoformat(request.form["start"]),
                end=datetime.fromisoformat(request.form["end"]),
                capacity=int(request.form["capacity"]),
            )
            validate_class(class_row)
            db.session.add(class_row)
            db.session.commit()
            flash("Class created.")
            return redirect(url_for("access.timetable"), code=303)
        except (RuleError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc))
        except OperationalError:
            db.session.rollback()
            flash("The system is busy. Please try again.")
    return render_template("class_form.html", session=None,
                           types=ClassType.query.all(),
                           instructors=Instructor.query.all(),
                           rooms=Room.query.all())


@bp.route("/admin/classes/<int:session_id>/edit", methods=["GET", "POST"])
@staff_only
def class_edit(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    if request.method == "POST":
        try:
            # Same write lock as booking. The second staff waits, then we
            # re-read the row and refuse if it is no longer the one they opened.
            db.session.execute(
                text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
                {"i": session_id},
            )
            db.session.refresh(class_row)
            if not _still_the_opened_class(class_row, request.form):
                raise RuleError(
                    "Another staff member saved this class while this form was open. "
                    "Nothing was written. The form now shows the current class."
                )
            previous_start = class_row.start
            _apply_class_form(class_row, request.form)
            validate_class(class_row, existing=True)
            opened = _keep_cancel_open(class_row, previous_start)
            dropped = 0
            if class_row.start != previous_start:
                dropped = _drop_clashing_waiters(class_row)
                restarted = datetime.now()
                for booking in class_row.bookings:
                    if booking.status == "Offered":
                        booking.offered_at = restarted
            db.session.commit()
            flash("Class updated on the same id.")
            if opened:
                flash(
                    "%d booked member(s) can still cancel. The new start is inside "
                    "the two-hour cut-off, so the usual refusal does not apply to them."
                    % opened
                )
            if dropped:
                flash(
                    "%d waiting member(s) left the queue because the new time overlaps another class."
                    % dropped
                )
            return redirect(url_for("scheduling.roster", session_id=class_row.id), code=303)
        except (RuleError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc))
            class_row = db.session.get(ClassSession, session_id)
        except OperationalError:
            db.session.rollback()
            flash("The system is busy. Please try again.")
            class_row = db.session.get(ClassSession, session_id)
    return render_template("class_form.html", session=class_row,
                           types=ClassType.query.all(),
                           instructors=Instructor.query.all(),
                           rooms=Room.query.all(),
                           booked=booked_count(class_row.id) if class_row else 0)


@bp.get("/admin/classes/<int:session_id>/roster")
@staff_only
def roster(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    rows = [b for b in class_row.bookings if b.status == "Booked"]
    return render_template("roster.html", session=class_row, rows=rows)


@bp.route("/admin/plans", methods=["GET", "POST"])
@staff_only
def plans():
    if request.method == "POST":
        flash("Plan changes are not part of this milestone.")
        return redirect(url_for("scheduling.plans"), code=303)
    return render_template("plans.html", plans=MembershipPlan.query.all(),
                           memberships=Membership.query.all())
