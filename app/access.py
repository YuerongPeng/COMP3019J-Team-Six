# -*- coding: utf-8 -*-
"""Access and pages (Yang).

Role checks before any booking or admin action, and every rendered page built from
the latest query - including places left and the refusal messages.
"""
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from . import db, login_manager
from .models import Booking, ClassSession, ClassType, Membership, User

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
    types = ClassType.query.order_by(ClassType.name).all()
    chosen_date = _timetable_date(request.args.get("date", ""))
    chosen_type = _timetable_type(request.args.get("type", ""), types)
    open_only = request.args.get("open") == "1"
    if chosen_date is not None:
        day_end = chosen_date + timedelta(days=1)
        sessions = [s for s in sessions if chosen_date <= s.start < day_end]
    if chosen_type is not None:
        sessions = [s for s in sessions if s.class_type_id == chosen_type]
    if open_only:
        sessions = [s for s in sessions if s.places_left > 0]
    mine = set()
    if current_user.is_authenticated:
        mine = {b.class_session_id for b in current_user.bookings if b.status == "Booked"}
    return render_template(
        "timetable.html",
        sessions=sessions,
        mine=mine,
        types=types,
        chosen_date=request.args.get("date", "") if chosen_date else "",
        chosen_type=chosen_type or "",
        open_only=open_only,
    )


def _timetable_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None


def _timetable_type(raw, types):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        type_id = int(raw)
    except ValueError:
        return None
    if any(t.id == type_id for t in types):
        return type_id
    return None


@bp.get("/classes/<int:session_id>")
def class_detail(session_id):
    class_row = db.get_or_404(ClassSession, session_id)
    booked = False
    if current_user.is_authenticated:
        booked = any(b.class_session_id == session_id and b.status == "Booked"
                     for b in current_user.bookings)
    return render_template("class_detail.html", session=class_row, booked=booked)


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
    now = datetime.now()
    bookings = (Booking.query
                .filter_by(user_id=current_user.id)
                .all())
    upcoming = [b for b in bookings if b.session.start >= now]
    past = [b for b in bookings if b.session.start < now]
    upcoming.sort(key=lambda b: b.session.start)
    past.sort(key=lambda b: b.session.start, reverse=True)
    return render_template("my_bookings.html", upcoming=upcoming, past=past)
