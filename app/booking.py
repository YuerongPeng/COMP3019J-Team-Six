# -*- coding: utf-8 -*-
"""Booking module (Ma).

The only writer of Booked rows and of credits. Checks the plan, the cut-off and the
capacity, and owns the transaction that protects the capacity rule.
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from . import db
from .access import RuleError, active_membership
from .models import Booking, ClassSession, Membership, User

bp = Blueprint("booking", __name__)

OFFER_MINUTES = 30


def lock_member_schedule(user_id):
    """Serialize every action that can give one member a second booked class.

    Instructor approval locks the same User rows before it validates a time
    change. The no-op UPDATE provides a current row lock on both MySQL and the
    local SQLite test database.
    """
    # The no-op UPDATE is a row lock on MySQL and starts the SQLite write lock.
    # SELECT FOR UPDATE alone is ignored by SQLite, so it cannot protect the
    # local concurrency tests.
    db.session.execute(
        text("UPDATE user SET status = status WHERE id = :i"),
        {"i": user_id},
    )
    return db.session.get(User, user_id)


def lock_membership(membership_id):
    """Lock one membership card before changing or spending its credits."""
    if membership_id is None:
        return None
    return db.session.execute(
        select(Membership)
        .where(Membership.id == membership_id)
        .with_for_update()
    ).scalar_one_or_none()


def locked_active_membership(user_id):
    """Read the current active card after acquiring the member lock."""
    return (Membership.query
            .filter_by(user_id=user_id, status="active")
            .order_by(Membership.end.desc())
            .with_for_update()
            .first())


def _locked_booking(booking_id):
    return db.session.execute(
        select(Booking).where(Booking.id == booking_id).with_for_update()
    ).scalar_one_or_none()


def _member_time_conflict(user_id, class_row, ignore_booking_id=None):
    """Another class this member already holds, including an open offer."""
    query = (Booking.query
             .join(ClassSession, Booking.class_session_id == ClassSession.id)
             .filter(Booking.user_id == user_id,
                     Booking.class_session_id != class_row.id,
                     Booking.status.in_(("Booked", "Offered")),
                     ClassSession.start < class_row.end,
                     ClassSession.end > class_row.start))
    if ignore_booking_id is not None:
        query = query.filter(Booking.id != ignore_booking_id)
    return query.with_for_update().first()


def booked_count(session_id):
    return (db.session.query(func.count(Booking.id))
            .filter(Booking.class_session_id == session_id,
                    Booking.status == "Booked")
            .scalar())


def held_count(session_id):
    """Seats that are not free: booked, offered, attended or no-show."""
    return (db.session.query(func.count(Booking.id))
            .filter(Booking.class_session_id == session_id,
                    Booking.status.in_(("Booked", "Offered", "Attended", "No-show")))
            .scalar())


def plan_notice(user):
    """One timetable line when the member's plan has already ended."""
    if not user.is_authenticated or user.role != "member":
        return None
    membership = active_membership(user.id)
    if membership is None:
        return "You do not have an active plan."
    if membership.end < datetime.now().date():
        return "Your %s ended on %s." % (
            membership.plan.name, membership.end.strftime("%d %b %Y"))
    return None


def book_block(user, class_row):
    """Why this member cannot book, or None when the Book button may be used.

    The order is the one shown on the button. Several reasons may be true;
    only the first is returned. Submitting the form still runs the real checks.
    """
    if not user.is_authenticated or user.role != "member":
        return None
    booked = [b for b in user.bookings if b.status == "Booked"]
    holding = [b for b in user.bookings if b.status in ("Booked", "Offered")]
    if any(b.class_session_id == class_row.id for b in booked):
        return {"label": "Booked", "detail": ""}
    if class_row.start <= datetime.now():
        return {
            "label": "Started",
            "detail": "This class has already started.",
        }
    membership = active_membership(user.id)
    if membership is None or membership.end < class_row.start.date():
        return {"label": "Plan expired", "detail": ""}
    if membership.credits_remaining is not None and membership.credits_remaining <= 0:
        return {"label": "No credits", "detail": ""}
    if membership.plan.off_peak and class_row.start.hour >= 16:
        return {
            "label": "Off-peak",
            "detail": "This plan only covers classes that start before 16:00.",
        }
    for other in holding:
        if other.class_session_id == class_row.id:
            continue
        if other.session.start < class_row.end and class_row.start < other.session.end:
            return {
                "label": "Time clash",
                "detail": "Overlaps %s %s." % (
                    other.session.class_type.name,
                    other.session.start.strftime("%a %H:%M")),
            }
    if any(b.class_session_id == class_row.id and b.status == "Offered" for b in holding):
        return {
            "label": "Offered",
            "detail": "Confirm this place in My bookings.",
            "action": "queued",
        }
    if class_row.places_left <= 0:
        mine_here = [b for b in user.bookings if b.class_session_id == class_row.id]
        if any(b.status == "Waiting" for b in mine_here):
            return {"label": "Waiting", "detail": "You are in the queue.", "action": "queued"}
        if any(b.status == "Offered" for b in mine_here):
            return {
                "label": "Offered",
                "detail": "Confirm this place in My bookings.",
                "action": "queued",
            }
        return {"label": "Join waitlist", "detail": "", "action": "wait"}
    return None


def cancel_block(booking, now=None):
    """How the Cancel button should look for one upcoming booking."""
    if booking.status != "Booked":
        return None
    now = now or datetime.now()
    if now >= booking.session.start:
        return {
            "disabled": True,
            "detail": "The class has started, so this cannot be cancelled.",
        }
    inside = booking.session.start - now < timedelta(hours=2)
    if not inside:
        return {"disabled": False, "detail": ""}
    if booking.late_change_cancel:
        return {
            "disabled": False,
            "detail": "The start time was moved inside the two-hour cut-off. You can still cancel.",
        }
    return {
        "disabled": True,
        "detail": "Less than two hours before the start, so this cannot be cancelled.",
    }


@bp.post("/classes/<int:session_id>/book")
@login_required
def book(session_id):
    try:
        lock_member_schedule(current_user.id)
        # One transaction. SQLite takes the write lock when the row is touched;
        # on MySQL this is the place for SELECT ... FOR UPDATE.
        db.session.execute(text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
                           {"i": session_id})

        class_row = db.session.get(ClassSession, session_id)
        if class_row is None:
            raise RuleError("That class no longer exists.")

        membership = locked_active_membership(current_user.id)
        if membership is None or membership.end < class_row.start.date():
            raise RuleError("No active membership covers that date.")
        if membership.plan.off_peak and class_row.start.hour >= 16:
            raise RuleError("This plan covers off-peak classes only.")
        if membership.credits_remaining is not None and membership.credits_remaining <= 0:
            raise RuleError("No credits left on this plan.")
        if held_count(session_id) >= class_row.capacity:
            raise RuleError("That class is full.")
        if class_row.end <= datetime.now():
            raise RuleError("That class has ended.")
        if class_row.start <= datetime.now():
            raise RuleError("That class has started.")
        if (Booking.query.filter(Booking.user_id == current_user.id,
                                 Booking.class_session_id == session_id,
                                 Booking.status.in_(("Booked", "Offered")))
                .with_for_update().first()):
            raise RuleError("You already have this class booked.")
        if _member_time_conflict(current_user.id, class_row):
            raise RuleError("That overlaps another class you booked or were offered.")

        db.session.add(Booking(user_id=current_user.id,
                               class_session_id=session_id,
                               membership_id=membership.id,
                               status="Booked"))
        if membership.credits_remaining is not None:
            membership.credits_remaining -= 1
        db.session.commit()
        flash("Booked.")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc))
        return redirect(url_for("access.class_detail", session_id=session_id), code=303)
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
        return redirect(url_for("access.class_detail", session_id=session_id), code=303)
    return redirect(url_for("access.timetable"), code=303)


@bp.post("/bookings/<int:booking_id>/cancel")
@login_required
def cancel(booking_id):
    try:
        booking = db.get_or_404(Booking, booking_id)
        if booking.user_id != current_user.id:
            abort(403)
        lock_member_schedule(current_user.id)
        _lock_class(booking.class_session_id)
        class_row = db.session.execute(
            select(ClassSession)
            .where(ClassSession.id == booking.class_session_id)
            .with_for_update()
        ).scalar_one_or_none()
        if class_row is None:
            abort(404)
        if datetime.now() >= class_row.start:
            raise RuleError("The class has started, so this cannot be cancelled.")
        inside_cutoff = class_row.start - datetime.now() < timedelta(hours=2)
        cancellation = Booking.query.filter(
            Booking.id == booking_id, Booking.status == "Booked")
        if inside_cutoff:
            cancellation = cancellation.filter(
                Booking.late_change_cancel.is_(True))
        changed = cancellation.update({
            Booking.status: "Cancelled",
            Booking.active: False,
            Booking.late_change_cancel: False,
            Booking.cancelled_at: datetime.utcnow(),
        }, synchronize_session=False)
        if changed != 1:
            if inside_cutoff:
                raise RuleError(
                    "Too late to cancel, or this booking was already cancelled."
                )
            raise RuleError("That booking is already cancelled.")
        membership = lock_membership(booking.membership_id)
        if membership is not None and membership.credits_remaining is not None:
            membership.credits_remaining += 1
        offered = _offer_next(booking.class_session_id)
        db.session.commit()
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc))
        return redirect(url_for("access.my_bookings"), code=303)
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
        return redirect(url_for("access.my_bookings"), code=303)
    if offered:
        flash("Cancelled. The place was offered to the next person on the waitlist.")
    else:
        flash("Cancelled. The place is free again.")
    return redirect(url_for("access.my_bookings"), code=303)


def _require_member():
    if current_user.role != "member":
        abort(403)


def _lock_class(session_id):
    db.session.execute(text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
                       {"i": session_id})


def _offer_next(session_id):
    """Give the next queued member a held place, while a seat is free."""
    db.session.flush()
    class_row = db.session.get(ClassSession, session_id)
    if class_row is None:
        return False
    if class_row.start <= datetime.now():
        return False
    offered = False
    while held_count(session_id) < class_row.capacity:
        nxt = (Booking.query
               .filter_by(class_session_id=session_id, status="Waiting")
               .order_by(Booking.booked_at.asc(), Booking.id.asc())
               .first())
        if nxt is None:
            return offered
        nxt.status = "Offered"
        nxt.offered_at = datetime.now()
        offered = True
        db.session.flush()
    return offered


def _eligible(class_row, membership, ignore_booking_id=None):
    if membership is None or membership.end < class_row.start.date():
        raise RuleError("No active membership covers that date.")
    if membership.plan.off_peak and class_row.start.hour >= 16:
        raise RuleError("This plan covers off-peak classes only.")
    if membership.credits_remaining is not None and membership.credits_remaining <= 0:
        raise RuleError("No credits left on this plan.")
    duplicate = (Booking.query
                 .filter(Booking.user_id == current_user.id,
                         Booking.class_session_id == class_row.id,
                         Booking.status.in_(("Booked", "Offered"))))
    if ignore_booking_id is not None:
        duplicate = duplicate.filter(Booking.id != ignore_booking_id)
    if duplicate.with_for_update().first():
        raise RuleError("You already have this class booked.")
    if _member_time_conflict(current_user.id, class_row, ignore_booking_id):
        raise RuleError("That overlaps another class you booked or were offered.")


@bp.post("/classes/<int:session_id>/wait")
@login_required
def wait(session_id):
    _require_member()
    try:
        lock_member_schedule(current_user.id)
        _lock_class(session_id)
        class_row = db.session.get(ClassSession, session_id)
        if class_row is None:
            raise RuleError("That class no longer exists.")
        if class_row.end <= datetime.now():
            raise RuleError("That class has ended.")
        if class_row.start <= datetime.now():
            raise RuleError("That class has started.")
        if held_count(session_id) < class_row.capacity:
            raise RuleError("That class still has a place. Book it directly.")
        existing = Booking.query.filter(
            Booking.user_id == current_user.id,
            Booking.class_session_id == session_id,
            Booking.status.in_(("Booked", "Waiting", "Offered"))).first()
        if existing:
            raise RuleError("You are already booked or waiting for this class.")
        membership = locked_active_membership(current_user.id)
        _eligible(class_row, membership)
        db.session.add(Booking(user_id=current_user.id,
                               class_session_id=session_id,
                               membership_id=membership.id,
                               status="Waiting",
                               booked_at=datetime.utcnow()))
        db.session.commit()
        flash("You are on the waitlist.")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc))
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
    return redirect(url_for("access.timetable"), code=303)


@bp.post("/bookings/<int:booking_id>/confirm")
@login_required
def confirm(booking_id):
    _require_member()
    expire_offers()
    try:
        lock_member_schedule(current_user.id)
        booking = _locked_booking(booking_id)
        if booking is None:
            abort(404)
        if booking.user_id != current_user.id:
            abort(403)
        _lock_class(booking.class_session_id)
        booking = _locked_booking(booking_id)
        if booking.status != "Offered":
            raise RuleError("That place is no longer offered to you.")
        class_row = booking.session
        if class_row.end <= datetime.now():
            close_finished()
            db.session.commit()
            flash("That class has ended. The offer was cancelled and no class was charged.")
            return redirect(url_for("access.my_bookings"), code=303)
        if class_row.start <= datetime.now():
            booking.status = "Cancelled"
            booking.active = False
            booking.cancelled_at = datetime.utcnow()
            db.session.commit()
            flash("That class has started, so this place can no longer be confirmed.")
            return redirect(url_for("access.my_bookings"), code=303)
        membership = locked_active_membership(current_user.id)
        try:
            _eligible(class_row, membership, ignore_booking_id=booking.id)
        except RuleError as exc:
            booking.status = "Cancelled"
            booking.active = False
            booking.cancelled_at = datetime.utcnow()
            _offer_next(class_row.id)
            db.session.commit()
            flash(str(exc))
            return redirect(url_for("access.my_bookings"), code=303)
        booking.status = "Booked"
        booking.membership_id = membership.id
        booking.late_change_cancel = False
        if membership.credits_remaining is not None:
            membership.credits_remaining -= 1
        db.session.commit()
        flash("Booked.")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc))
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
    return redirect(url_for("access.my_bookings"), code=303)


@bp.post("/bookings/<int:booking_id>/decline")
@login_required
def decline(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    try:
        _lock_class(booking.class_session_id)
        db.session.refresh(booking)
        if booking.status != "Offered":
            raise RuleError("That place is no longer offered to you.")
        session_id = booking.class_session_id
        booking.status = "Cancelled"
        booking.active = False
        booking.cancelled_at = datetime.utcnow()
        offered = _offer_next(session_id)
        db.session.commit()
        if offered:
            flash("Declined. The place was offered to the next person.")
        else:
            flash("Declined.")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc))
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
    return redirect(url_for("access.my_bookings"), code=303)


@bp.post("/bookings/<int:booking_id>/leave")
@login_required
def leave(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    if booking.status != "Waiting":
        flash("You are not waiting for that class.")
        return redirect(url_for("access.my_bookings"), code=303)
    booking.status = "Cancelled"
    booking.active = False
    booking.cancelled_at = datetime.utcnow()
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
        return redirect(url_for("access.my_bookings"), code=303)
    flash("You left the waitlist.")
    return redirect(url_for("access.my_bookings"), code=303)


def _closed_key(booking):
    stamp = booking.offered_at.strftime("%Y%m%d%H%M%S") if booking.offered_at else "0"
    return "offer_closed_%s_%s" % (booking.id, stamp)


def seconds_left(booking, now=None):
    if booking.offered_at is None:
        return 0
    now = now or datetime.now()
    remaining = booking.offered_at + timedelta(minutes=OFFER_MINUTES) - now
    return max(0, int(remaining.total_seconds()))


def expire_offers():
    """Pass any offer that was not accepted within 30 minutes, then move the queue."""
    now = datetime.now()
    rows = Booking.query.filter_by(status="Offered").all()
    sessions = set()
    changed = False
    for booking in rows:
        if booking.offered_at is None:
            booking.offered_at = now
            changed = True
            continue
        if seconds_left(booking, now) == 0:
            booking.status = "Passed"
            booking.skip_seen = False
            sessions.add(booking.class_session_id)
            changed = True
    if not changed:
        return
    db.session.flush()
    for session_id in sessions:
        _offer_next(session_id)
    db.session.commit()


def open_offers(user):
    expire_offers()
    if not user.is_authenticated or user.role != "member":
        return []
    now = datetime.now()
    rows = (Booking.query
            .filter_by(user_id=user.id, status="Offered")
            .order_by(Booking.offered_at.asc())
            .all())
    return [b for b in rows if seconds_left(b, now) > 0 and b.session.end > now]


def unseen_pass(user):
    if not user.is_authenticated or user.role != "member":
        return None
    return (Booking.query
            .filter_by(user_id=user.id, status="Passed", skip_seen=False)
            .order_by(Booking.id.desc())
            .first())


def login_destination(user):
    """Where a member goes immediately after signing in."""
    close_finished()
    db.session.commit()
    expire_offers()
    if user.role == "instructor":
        return url_for("scheduling.my_classes")
    if user.role != "member":
        return url_for("access.timetable")
    passed = unseen_pass(user)
    if passed is not None:
        return url_for("booking.passed_page", booking_id=passed.id)
    for offer in open_offers(user):
        if not session.get(_closed_key(offer)):
            return url_for("booking.offer_page", booking_id=offer.id)
    return url_for("access.timetable")


@bp.get("/bookings/<int:booking_id>/offer")
@login_required
def offer_page(booking_id):
    _require_member()
    close_finished()
    db.session.commit()
    expire_offers()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    if booking.status != "Offered" or seconds_left(booking) == 0:
        return redirect(url_for("access.my_bookings"), code=303)
    return render_template("offer_notice.html", booking=booking, seconds=seconds_left(booking))


@bp.post("/bookings/<int:booking_id>/offer/close")
@login_required
def close_offer(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    session[_closed_key(booking)] = True
    return redirect(url_for("access.my_bookings"), code=303)


@bp.get("/bookings/<int:booking_id>/passed")
@login_required
def passed_page(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id or booking.status != "Passed":
        abort(403)
    return render_template("offer_passed.html", booking=booking)


@bp.post("/bookings/<int:booking_id>/rejoin")
@login_required
def rejoin(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    if booking.status != "Passed":
        flash("That place is no longer available to rejoin.")
        return redirect(url_for("access.my_bookings"), code=303)
    held = Booking.query.filter(
        Booking.user_id == current_user.id,
        Booking.class_session_id == booking.class_session_id,
        Booking.status.in_(("Booked", "Waiting", "Offered"))).first()
    if held:
        booking.skip_seen = True
        db.session.commit()
        flash("You are already booked or waiting for this class.")
        return redirect(url_for("access.my_bookings"), code=303)
    booking.status = "Waiting"
    booking.booked_at = datetime.utcnow()
    booking.offered_at = None
    booking.skip_seen = True
    booking.active = True
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
        return redirect(url_for("access.my_bookings"), code=303)
    flash("You joined the back of the queue.")
    return redirect(url_for("access.my_bookings"), code=303)


@bp.post("/bookings/<int:booking_id>/passed/close")
@login_required
def close_passed(booking_id):
    _require_member()
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    booking.skip_seen = True
    db.session.commit()
    return redirect(url_for("access.my_bookings"), code=303)


def close_finished(now=None):
    """Drop queue places and unconfirmed offers once the class has ended.

    Nobody is marked no-show. No credit is charged or returned.
    """
    now = now or datetime.now()
    rows = (Booking.query
            .join(ClassSession, Booking.class_session_id == ClassSession.id)
            .filter(ClassSession.end <= now,
                    Booking.status.in_(("Offered", "Waiting")))
            .all())
    for booking in rows:
        booking.status = "Cancelled"
        booking.active = False
        booking.offered_at = None
        booking.cancelled_at = datetime.utcnow()
    return bool(rows)


def attendance_list(class_row):
    """Booked, attended and no-show rows. A finished class drops its queue first."""
    close_finished()
    db.session.commit()
    class_row = db.session.get(ClassSession, class_row.id)
    rows = [b for b in class_row.bookings if b.status in ("Booked", "Attended", "No-show")]
    rows.sort(key=lambda b: (b.user.name.lower(), b.id))
    return class_row, rows


def record_attendance(class_row, booking, result):
    """The class instructor marks a booked member after the class has ended.

    Attended and no-show can be swapped. Neither one changes credits, frees the
    seat, or offers the place to the waitlist.
    """
    if class_row.instructor is None or class_row.instructor.user_id != current_user.id:
        abort(403)
    if datetime.now() < class_row.end:
        raise RuleError("Attendance can be recorded only after the class has ended.")
    close_finished()
    if (booking.class_session_id != class_row.id
            or booking.status not in ("Booked", "Attended", "No-show")):
        raise RuleError("Only a booked member of this class can be marked.")
    if result not in ("Attended", "No-show"):
        raise RuleError("Choose attended or no-show.")
    booking.status = result
