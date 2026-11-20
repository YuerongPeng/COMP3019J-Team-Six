# -*- coding: utf-8 -*-
"""Booking module (Ma).

The only writer of Booked rows and of credits. Checks the plan, the cut-off and the
capacity, and owns the transaction that protects the capacity rule.
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError

from . import db
from .access import RuleError, active_membership
from .membership import book_refusal, membership_covering
from .models import Booking, ClassSession, Membership

bp = Blueprint("booking", __name__)


def booked_count(session_id):
    return (db.session.query(func.count(Booking.id))
            .filter(Booking.class_session_id == session_id,
                    Booking.status == "Booked")
            .scalar())


@bp.post("/classes/<int:session_id>/book")
@login_required
def book(session_id):
    try:
        # One transaction. SQLite takes the write lock when the row is touched;
        # on MySQL this is the place for SELECT ... FOR UPDATE.
        db.session.execute(text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
                           {"i": session_id})

        class_row = db.session.get(ClassSession, session_id)
        if class_row is None:
            raise RuleError("That class no longer exists.")

        # The same rules the calendar shows, re-checked at submit time.
        refusal = book_refusal(class_row, current_user)
        if refusal:
            raise RuleError("Cannot book: " + refusal[1])

        membership = active_membership(current_user.id)
        if membership is None:
            raise RuleError("No active membership.")
        if not membership_covering(membership, class_row.start):
            raise RuleError("No active membership covers that date.")

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
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(403)
    if booking.status != "Booked":
        flash("That booking is already cancelled.")
        return redirect(url_for("access.my_bookings"), code=303)
    if booking.session.start - datetime.now() < timedelta(hours=2):
        flash("Too late to cancel: the cut-off is two hours before the start.")
        return redirect(url_for("access.my_bookings"), code=303)

    booking.status = "Cancelled"
    booking.active = False
    booking.cancelled_at = datetime.utcnow()
    membership = db.session.get(Membership, booking.membership_id)
    if membership is not None and membership.credits_remaining is not None:
        membership.credits_remaining += 1
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        flash("The system is busy. Please try again.")
        return redirect(url_for("access.my_bookings"), code=303)
    flash("Cancelled. The place is free again.")
    return redirect(url_for("access.my_bookings"), code=303)
