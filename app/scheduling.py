# -*- coding: utf-8 -*-
"""Scheduling module (Peng).

Checks instructor experience, room suitability, overlaps and capacity when a class is
saved, and updates a class without changing its identity, so existing bookings stay.
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select, text
from sqlalchemy.exc import OperationalError

from . import db
from .access import RuleError, staff_only
from .booking import (attendance_list, held_count, lock_member_schedule,
                      record_attendance)
from .models import (Booking, ClassChangeProposal, ClassSession, ClassType,
                     Instructor, InstructorAbsenceRequest, Membership,
                     MembershipPlan, Room, User)

bp = Blueprint("scheduling", __name__)


def validate_class(class_row, existing=False, locked_member_ids=None):
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
               .filter(or_(ClassSession.instructor_id == class_row.instructor_id,
                           ClassSession.room_id == class_row.room_id))
               .filter(ClassSession.start < class_row.end)
               .filter(ClassSession.end > class_row.start)
               .with_for_update()
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
        clashing = members_double_booked(
            class_row,
            user_ids=locked_member_ids,
            locking=locked_member_ids is not None,
        )
        if clashing:
            raise RuleError(
                "That time would put %d booked member(s) in two classes at once. "
                "Nothing was written." % clashing)


def _class_member_ids(session_id, locking=False):
    """Current members whose place must follow a schedule change."""
    query = (db.session.query(Booking.user_id)
             .filter(Booking.class_session_id == session_id,
                     Booking.status.in_(("Booked", "Offered")))
             .distinct()
             .order_by(Booking.user_id))
    if locking:
        query = query.with_for_update()
    return [row[0] for row in query.all()]


def _current_class_member_ids(session_id):
    """Read member ids in a separate current snapshot before taking their locks."""
    statement = (select(Booking.user_id)
                 .where(Booking.class_session_id == session_id,
                        Booking.status.in_(("Booked", "Offered")))
                 .distinct().order_by(Booking.user_id))
    with db.engine.connect() as connection:
        return [row[0] for row in connection.execute(statement).all()]


def _lock_members_for_time_change(session_id):
    """Lock affected members before the class, then refresh the member set.

    Booking takes the same locks in the same logical order: member first, class
    second. The second query is a locking/current read, so MySQL does not reuse
    an older REPEATABLE READ snapshot after waiting for a booking to finish.
    """
    initial_ids = _current_class_member_ids(session_id)
    for user_id in initial_ids:
        lock_member_schedule(user_id)

    class_row = db.session.execute(
        select(ClassSession)
        .where(ClassSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if class_row is None:
        abort(404)

    current_ids = _class_member_ids(session_id, locking=True)
    if current_ids != initial_ids:
        raise RuleError(
            "The class list changed while this schedule change was being approved. "
            "Please try again."
        )
    return class_row, current_ids


def _lock_schedule():
    """Serialize every timetable write before checking shared resources."""
    db.session.execute(
        text("UPDATE schedule_lock SET id = id WHERE id = 1")
    )


def members_double_booked(class_row, user_ids=None, locking=False):
    """How many members already booked here would clash with another booking."""
    affected = set()
    user_ids = user_ids if user_ids is not None else _class_member_ids(class_row.id)
    for user_id in user_ids:
        query = (Booking.query
                 .join(ClassSession, Booking.class_session_id == ClassSession.id)
                 .filter(Booking.user_id == user_id,
                         Booking.class_session_id != class_row.id,
                         Booking.status.in_(("Booked", "Offered")),
                         ClassSession.start < class_row.end,
                         ClassSession.end > class_row.start))
        if locking:
            query = query.with_for_update()
        if query.first() is not None:
            affected.add(user_id)
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


def _reject_past_start(start):
    """A new or not-yet-started class cannot be given a start time that has passed."""
    if start <= datetime.now():
        raise RuleError("The start time is already in the past. Nothing was written.")


def _reject_started_time_change(class_row, form):
    """Once a class has started, its start and end stay as they are.

    Room, instructor and capacity can still be saved. The check uses the time
    stored now, so a form opened before the start cannot move the class later.
    """
    if datetime.now() < class_row.start:
        return
    current = _opened_stamp(class_row)
    if form.get("start") != current["start"] or form.get("end") != current["end"]:
        raise RuleError(
            "This class has started, so its start and end can no longer be changed. "
            "Nothing was written."
        )


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


def _proposal_candidate(class_row, form):
    """Validate a proposed version without changing the visible class row."""
    candidate = ClassSession(
        id=class_row.id,
        class_type_id=int(form["class_type_id"]),
        instructor_id=int(form["instructor_id"]),
        room_id=int(form["room_id"]),
        start=datetime.fromisoformat(form["start"]),
        end=datetime.fromisoformat(form["end"]),
        capacity=int(form["capacity"]),
    )
    candidate.class_type = db.session.get(ClassType, candidate.class_type_id)
    candidate.instructor = db.session.get(Instructor, candidate.instructor_id)
    candidate.room = db.session.get(Room, candidate.room_id)
    if not candidate.class_type or not candidate.instructor or not candidate.room:
        raise RuleError("That class type, instructor or room no longer exists.")
    if candidate.end <= candidate.start:
        raise RuleError("The class must end after it starts.")
    validate_class(candidate, existing=True)
    return candidate


def _keep_cancel_open(class_row, previous_start):
    """If the new start is inside the cut-off, booked members may still cancel.

    The exception lasts only until the class starts. A start that stays at
    least two hours away clears it, because the normal rule applies.
    Returns how many bookings were given that chance.
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


@bp.route("/admin/instructors/accounts", methods=["GET", "POST"])
@staff_only
def instructor_accounts():
    if request.method == "POST":
        instructor = db.session.get(Instructor, request.form.get("instructor_id", type=int))
        if instructor is not None:
            db.session.execute(
                text("UPDATE instructor SET user_id = user_id WHERE id = :i"),
                {"i": instructor.id},
            )
            db.session.refresh(instructor)
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if instructor is None:
            flash("Choose a valid instructor.")
        elif instructor.user_id is not None:
            flash("This instructor already has a login account.")
        elif not name or not email or len(password) < 6:
            flash("Enter a name, email and password of at least 6 characters.")
        elif User.query.filter_by(email=email).first():
            flash("That email address is already in use.")
        else:
            from werkzeug.security import generate_password_hash
            user = User(name=name, email=email, role="instructor",
                        password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.flush()
            instructor.user_id = user.id
            db.session.commit()
            flash("Instructor login created and linked.")
            return redirect(url_for("scheduling.instructor_accounts"), code=303)
    instructors = Instructor.query.order_by(Instructor.name).all()
    return render_template("instructor_accounts.html", instructors=instructors)


@bp.get("/admin/change-requests")
@staff_only
def change_requests():
    proposals = (ClassChangeProposal.query
                 .filter_by(status="Pending")
                 .order_by(ClassChangeProposal.created_at.desc()).all())
    absences = (InstructorAbsenceRequest.query
                .filter_by(status="Pending")
                .order_by(InstructorAbsenceRequest.created_at.desc()).all())
    return render_template("change_requests.html", proposals=proposals, absences=absences)


@bp.post("/admin/proposals/<int:proposal_id>/cancel")
@staff_only
def cancel_proposal(proposal_id):
    proposal = db.get_or_404(ClassChangeProposal, proposal_id)
    db.session.execute(
        text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
        {"i": proposal.class_session_id},
    )
    db.session.refresh(proposal)
    if proposal.status == "Pending":
        proposal.status = "Cancelled"
        proposal.decided_at = datetime.utcnow()
        db.session.commit()
        flash("Pending change cancelled.")
    return redirect(url_for("scheduling.change_requests"), code=303)


@bp.route("/admin/classes/new", methods=["GET", "POST"])
@staff_only
def class_new():
    if request.method == "POST":
        try:
            instructor_id = int(request.form["instructor_id"])
            room_id = int(request.form["room_id"])
            _lock_schedule()
            class_type = db.session.get(ClassType, int(request.form["class_type_id"]))
            instructor = db.session.get(Instructor, instructor_id)
            room = db.session.get(Room, room_id)
            if class_type is None or instructor is None or room is None:
                raise RuleError("That class type, instructor or room no longer exists.")
            class_row = ClassSession(
                class_type_id=class_type.id,
                instructor_id=instructor.id,
                room_id=room.id,
                class_type=class_type,
                instructor=instructor,
                room=room,
                start=datetime.fromisoformat(request.form["start"]),
                end=datetime.fromisoformat(request.form["end"]),
                capacity=int(request.form["capacity"]),
            )
            _reject_past_start(class_row.start)
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
    if request.method == "POST":
        try:
            _lock_schedule()
            # Locking read ignores an older REPEATABLE READ snapshot after a
            # concurrent scheduler has released the global timetable lock.
            class_row = db.session.execute(
                select(ClassSession)
                .where(ClassSession.id == session_id)
                .with_for_update()
            ).scalar_one_or_none()
            if class_row is None:
                abort(404)
            if not _still_the_opened_class(class_row, request.form):
                raise RuleError(
                    "Another staff member saved this class while this form was open. "
                    "Nothing was written. The form now shows the current class."
                )
            if ClassChangeProposal.query.filter_by(
                    class_session_id=session_id, status="Pending").first():
                raise RuleError("This class already has a change waiting for instructor approval.")
            previous_start = class_row.start
            _reject_started_time_change(class_row, request.form)
            if datetime.now() < class_row.start:
                _reject_past_start(datetime.fromisoformat(request.form["start"]))
            proposed_instructor_id = int(request.form["instructor_id"])
            proposed_room_id = int(request.form["room_id"])
            proposed_start = datetime.fromisoformat(request.form["start"])
            proposed_end = datetime.fromisoformat(request.form["end"])
            current_start = class_row.start.replace(second=0, microsecond=0)
            current_end = class_row.end.replace(second=0, microsecond=0)
            needs_approval = (
                proposed_instructor_id != class_row.instructor_id
                or proposed_room_id != class_row.room_id
                or proposed_start != current_start
                or proposed_end != current_end
            )
            if needs_approval:
                candidate = _proposal_candidate(class_row, request.form)
                target = candidate.instructor
                if target.user_id is None or target.user.role != "instructor":
                    raise RuleError("The proposed instructor needs a linked instructor login before this change can be sent.")
                proposal = ClassChangeProposal(
                    class_session_id=class_row.id,
                    submitted_by_user_id=current_user.id,
                    target_instructor_id=target.id,
                    class_type_id=candidate.class_type_id,
                    instructor_id=candidate.instructor_id,
                    room_id=candidate.room_id,
                    start=candidate.start,
                    end=candidate.end,
                    capacity=candidate.capacity,
                    base_instructor_id=class_row.instructor_id,
                    base_room_id=class_row.room_id,
                    base_start=class_row.start,
                    base_end=class_row.end,
                )
                db.session.add(proposal)
                db.session.commit()
                flash("Change sent to %s for approval. The timetable remains unchanged until accepted." % target.name)
                return redirect(url_for("scheduling.change_requests"), code=303)
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
    else:
        class_row = db.get_or_404(ClassSession, session_id)
    return render_template("class_form.html", session=class_row,
                           types=ClassType.query.all(),
                           instructors=Instructor.query.all(),
                           rooms=Room.query.all(),
                           booked=held_count(class_row.id) if class_row else 0,
                           time_locked=class_row.start <= datetime.now(),
                           pending_proposal=(ClassChangeProposal.query
                                             .filter_by(class_session_id=class_row.id,
                                                        status="Pending").first()))


def _roster_page(class_row, marking):
    class_row, rows = attendance_list(class_row)
    ended = class_row.end <= datetime.now()
    return render_template(
        "roster.html",
        session=class_row,
        rows=rows,
        can_mark=marking and ended,
        ended=ended,
        heading="Attendance" if marking else "Roster",
        back_to_my_classes=(current_user.is_authenticated
                            and current_user.role == "instructor"),
    )


@bp.get("/admin/classes/<int:session_id>/roster")
@staff_only
def roster(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    return _roster_page(class_row, marking=False)


@bp.route("/classes/<int:session_id>/attendance", methods=["GET", "POST"])
@login_required
def attendance(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    if class_row.instructor.user_id != current_user.id:
        abort(403)
    if request.method == "POST":
        try:
            booking_id = int(request.form.get("booking_id", ""))
        except ValueError:
            abort(404)
        booking = db.session.get(Booking, booking_id)
        if booking is None:
            abort(404)
        try:
            record_attendance(class_row, booking, request.form.get("result", ""))
            db.session.commit()
            flash("Attendance saved.")
        except RuleError as exc:
            db.session.rollback()
            flash(str(exc))
        except OperationalError:
            db.session.rollback()
            flash("The system is busy. Please try again.")
        return redirect(url_for("scheduling.attendance", session_id=session_id), code=303)
    return _roster_page(class_row, marking=True)


@bp.get("/instructor/classes")
@login_required
def my_classes():
    if current_user.role != "instructor":
        abort(403)
    instructor = Instructor.query.filter_by(user_id=current_user.id).first()
    if instructor is None:
        abort(403)
    sessions = (ClassSession.query
                .filter_by(instructor_id=instructor.id)
                .filter(ClassSession.start >= datetime.now())
                .order_by(ClassSession.start).all())
    pending_absences = {
        row.class_session_id for row in InstructorAbsenceRequest.query
        .filter_by(instructor_id=instructor.id, status="Pending").all()
    }
    proposals = (ClassChangeProposal.query
                 .filter_by(target_instructor_id=instructor.id, status="Pending")
                 .order_by(ClassChangeProposal.created_at.desc()).all())
    return render_template("instructor_classes.html", instructor=instructor,
                           sessions=sessions, pending_absences=pending_absences,
                           proposals=proposals)


@bp.post("/classes/<int:session_id>/cannot-teach")
@login_required
def cannot_teach(session_id):
    if current_user.role != "instructor":
        abort(403)
    class_row = db.get_or_404(ClassSession, session_id)
    instructor = Instructor.query.filter_by(user_id=current_user.id).first()
    if instructor is None or class_row.instructor_id != instructor.id:
        abort(403)
    if class_row.start <= datetime.now():
        flash("A request can only be submitted for a future class.")
    elif InstructorAbsenceRequest.query.filter_by(
            class_session_id=session_id, instructor_id=instructor.id,
            status="Pending").first():
        flash("A request for this class is already waiting for staff.")
    else:
        row = InstructorAbsenceRequest(
            class_session_id=session_id,
            instructor_id=instructor.id,
            requested_by_user_id=current_user.id,
            reason=request.form.get("reason", "").strip()[:255],
        )
        db.session.add(row)
        db.session.commit()
        flash("Request sent to staff. The class has not been changed.")
    return redirect(url_for("scheduling.my_classes"), code=303)


@bp.post("/instructor/proposals/<int:proposal_id>/<action>")
@login_required
def respond_to_proposal(proposal_id, action):
    if current_user.role != "instructor" or action not in ("accept", "decline"):
        abort(403)
    proposal = db.get_or_404(ClassChangeProposal, proposal_id)
    if proposal.target_instructor.user_id != current_user.id:
        abort(403)
    _lock_schedule()
    proposal = db.session.execute(
        select(ClassChangeProposal)
        .where(ClassChangeProposal.id == proposal_id)
        .with_for_update()
    ).scalar_one_or_none()
    if proposal is None:
        abort(404)
    class_row, locked_member_ids = _lock_members_for_time_change(
        proposal.class_session_id
    )
    if proposal.status != "Pending":
        flash("This change has already been decided.")
        return redirect(url_for("scheduling.my_classes"), code=303)
    if action == "decline":
        proposal.status = "Declined"
        proposal.decided_at = datetime.utcnow()
        db.session.commit()
        flash("Change declined. The original class remains in place.")
        return redirect(url_for("scheduling.my_classes"), code=303)
    try:
        db.session.refresh(class_row)
        if (class_row.instructor_id != proposal.base_instructor_id
                or class_row.room_id != proposal.base_room_id
                or class_row.start != proposal.base_start
                or class_row.end != proposal.base_end):
            raise RuleError("The class changed after this proposal was created. Ask staff to submit it again.")
        previous_start = class_row.start
        if previous_start <= datetime.now() and proposal.start != previous_start:
            raise RuleError("This class has started, so its time can no longer be changed.")
        class_row.class_type_id = proposal.class_type_id
        class_row.instructor_id = proposal.instructor_id
        class_row.room_id = proposal.room_id
        class_row.start = proposal.start
        class_row.end = proposal.end
        class_row.capacity = proposal.capacity
        validate_class(class_row, existing=True,
                       locked_member_ids=locked_member_ids)
        _keep_cancel_open(class_row, previous_start)
        if class_row.start != previous_start:
            _drop_clashing_waiters(class_row)
            restarted = datetime.now()
            for booking in class_row.bookings:
                if booking.status == "Offered":
                    booking.offered_at = restarted
        proposal.status = "Accepted"
        proposal.decided_at = datetime.utcnow()
        for absence in InstructorAbsenceRequest.query.filter_by(
                class_session_id=class_row.id, status="Pending").all():
            absence.status = "Resolved"
            absence.resolved_at = datetime.utcnow()
        db.session.commit()
        flash("Change accepted. Your schedule now shows the updated class.")
    except (RuleError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc))
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
    return redirect(url_for("scheduling.my_classes"), code=303)


@bp.route("/admin/plans", methods=["GET", "POST"])
@staff_only
def plans():
    if request.method == "POST":
        flash("Plan changes are not part of this milestone.")
        return redirect(url_for("scheduling.plans"), code=303)
    return render_template("plans.html", plans=MembershipPlan.query.all(),
                           memberships=Membership.query.all())
