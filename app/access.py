# -*- coding: utf-8 -*-
"""Access and pages (Yang).

Role checks before any booking or admin action, and every rendered page built from
the latest query - including places left and the refusal messages.
"""
from datetime import datetime
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from . import db, login_manager
from .membership import book_refusal, can_cancel
from .models import Booking, ClassSession, Membership, User

bp = Blueprint("access", __name__)


class RuleError(Exception):
    """A rule refused the action; nothing was written."""


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def staff_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("access.login", next=request.path))
        if current_user.role != "staff":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def active_membership(user_id):
    return (Membership.query
            .filter_by(user_id=user_id, status="active")
            .order_by(Membership.end.desc())
            .first())


@bp.get("/")
def index():
    return redirect(url_for("access.timetable"))


@bp.get("/timetable")
def timetable():
    sessions = (ClassSession.query
                .filter(ClassSession.start >= datetime.now())
                .order_by(ClassSession.start)
                .all())
    mine = set()
    refusals = {}
    plan_expired = False
    if current_user.is_authenticated and current_user.role == "member":
        mine = {b.class_session_id for b in current_user.bookings if b.status == "Booked"}
        for s in sessions:
            refusal = book_refusal(s, current_user)
            if refusal:
                refusals[s.id] = refusal
        membership = active_membership(current_user.id)
        plan_expired = (membership is not None and membership.end < datetime.now().date())
    return render_template("timetable.html", sessions=sessions, mine=mine,
                           refusals=refusals, plan_expired=plan_expired)


@bp.get("/classes/<int:session_id>")
def class_detail(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    booked = False
    refusal = None
    if current_user.is_authenticated and current_user.role == "member":
        booked = any(b.class_session_id == session_id and b.status == "Booked"
                     for b in current_user.bookings)
        refusal = book_refusal(class_row, current_user)
    return render_template("class_detail.html", session=class_row, booked=booked,
                           refusal=refusal)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password_hash, password):
            flash("Email or password is incorrect.")
            return render_template("login.html"), 200
        session.clear()
        login_user(user)
        return redirect(url_for("access.timetable"), code=303)
    return render_template("login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("access.index"), code=303)


@bp.get("/my-bookings")
@login_required
def my_bookings():
    if current_user.role == "staff":
        abort(403)
    bookings = (Booking.query
                .filter_by(user_id=current_user.id)
                .order_by(Booking.booked_at.desc())
                .all())
    upcoming = [b for b in bookings
                if b.status == "Booked" and b.session.start >= datetime.now()]
    past = [b for b in bookings
            if b.status != "Booked" or b.session.start < datetime.now()]
    cancelled = [(b, can_cancel(b)) for b in bookings if b.status == "Booked"]
    return render_template("my_bookings.html", upcoming=upcoming, past=past,
                           cancellable=dict(cancelled))
