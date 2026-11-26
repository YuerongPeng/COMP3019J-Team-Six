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
from .models import Booking, ClassSession, ClassType, Instructor, Membership, Notification, User

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


SLOT_CHOICES = (
    ("morning", "Morning, before 12:00"),
    ("afternoon", "Afternoon, 12:00–16:59"),
    ("evening", "Evening, from 17:00"),
)


@bp.get("/timetable")
def timetable():
    now = datetime.now()
    types = ClassType.query.order_by(ClassType.name).all()
    instructors = Instructor.query.order_by(Instructor.name).all()
    chosen_date = _timetable_date(request.args.get("date", ""))
    chosen_type = _timetable_type(request.args.get("type", ""), types)
    chosen_instructor = _timetable_instructor(request.args.get("instructor", ""), instructors)
    chosen_slot = _timetable_slot(request.args.get("slot", ""))
    open_only = request.args.get("open") == "1"
    week_start = _visible_week(now, chosen_date, request.args.get("week", ""))
    week_end = week_start + timedelta(days=7)
    sessions = (ClassSession.query
                .filter(ClassSession.start >= now,
                        ClassSession.start >= week_start,
                        ClassSession.start < week_end)
                .order_by(ClassSession.start)
                .all())
    if chosen_date is not None:
        day_end = chosen_date + timedelta(days=1)
        sessions = [s for s in sessions if chosen_date <= s.start < day_end]
    if chosen_type is not None:
        sessions = [s for s in sessions if s.class_type_id == chosen_type]
    if chosen_instructor is not None:
        sessions = [s for s in sessions if s.instructor_id == chosen_instructor]
    if chosen_slot is not None:
        sessions = [s for s in sessions if _in_slot(s.start, chosen_slot)]
    if open_only:
        sessions = [s for s in sessions if s.places_left > 0]
    mine = set()
    blocks = {}
    notice = None
    if current_user.is_authenticated and current_user.role == "member":
        from .booking import book_block, plan_notice
        notice = plan_notice(current_user)
        mine = {b.class_session_id for b in current_user.bookings if b.status == "Booked"}
        blocks = {s.id: book_block(current_user, s) for s in sessions}
    days = []
    current = None
    for row in sessions:
        key = row.start.date()
        if current is None or current["date"] != key:
            current = {"date": key, "label": row.start.strftime("%A %d %b"), "sessions": []}
            days.append(current)
        current["sessions"].append(row)
    filters = {
        "type": chosen_type or None,
        "instructor": chosen_instructor or None,
        "slot": chosen_slot,
        "open": "1" if open_only else None,
    }
    return render_template(
        "timetable.html",
        days=days,
        mine=mine,
        blocks=blocks,
        plan_notice=notice,
        types=types,
        instructors=instructors,
        slots=SLOT_CHOICES,
        chosen_date=request.args.get("date", "") if chosen_date else "",
        chosen_type=chosen_type or "",
        chosen_instructor=chosen_instructor or "",
        chosen_slot=chosen_slot or "",
        open_only=open_only,
        filtered=any(filters.values()) or chosen_date is not None,
        week_label="%s – %s" % (
            week_start.strftime("%d %b"),
            (week_end - timedelta(days=1)).strftime("%d %b %Y")),
        prev_url=url_for("access.timetable", **_timetable_args(
            filters, week_start - timedelta(days=7))),
        next_url=url_for("access.timetable", **_timetable_args(
            filters, week_start + timedelta(days=7))),
        week_value=week_start.strftime("%Y-%m-%d"),
    )


def _timetable_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None


def _monday(moment):
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def _visible_week(now, chosen_date, raw_week):
    """The Monday of the week on screen.

    A chosen date wins. Otherwise an explicit week is used. The first visit
    shows this week, or the week of the next class when this week has none left.
    """
    if chosen_date is not None:
        return _monday(chosen_date)
    explicit = _timetable_date(raw_week)
    if explicit is not None:
        return _monday(explicit)
    week_start = _monday(now)
    week_end = week_start + timedelta(days=7)
    has_any = (ClassSession.query
               .filter(ClassSession.start >= now,
                       ClassSession.start < week_end)
               .first())
    if has_any is not None:
        return week_start
    earliest = (ClassSession.query
                .filter(ClassSession.start >= now)
                .order_by(ClassSession.start)
                .first())
    if earliest is None:
        return week_start
    return _monday(earliest.start)


def _timetable_args(filters, week_start):
    args = {"week": week_start.strftime("%Y-%m-%d")}
    for key, value in filters.items():
        if value:
            args[key] = value
    return args


def _timetable_instructor(raw, instructors):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        instructor_id = int(raw)
    except ValueError:
        return None
    if any(row.id == instructor_id for row in instructors):
        return instructor_id
    return None


def _timetable_slot(raw):
    raw = (raw or "").strip()
    if raw in {key for key, _label in SLOT_CHOICES}:
        return raw
    return None


def _in_slot(start, slot):
    hour = start.hour
    if slot == "morning":
        return hour < 12
    if slot == "afternoon":
        return 12 <= hour < 17
    if slot == "evening":
        return hour >= 17
    return True


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
    from .booking import waitlist_view
    block = None
    user_id = None
    if current_user.is_authenticated and current_user.role == "member":
        from .booking import book_block
        block = book_block(current_user, class_row)
        user_id = current_user.id
    return render_template(
        "class_detail.html",
        session=class_row,
        block=block,
        queue=waitlist_view(session_id, user_id),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password_hash, password):
            flash("Email or password is incorrect.")
            return render_template("login.html"), 200
        session.clear()
        login_user(user)
        from .booking import login_destination
        return redirect(login_destination(user), code=303)
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
    if current_user.role != "member":
        abort(403)
    from .booking import cancel_block, cleanup_expired, open_offers, seconds_left
    cleanup_expired()
    now = datetime.now()
    bookings = (Booking.query
                .filter_by(user_id=current_user.id)
                .all())
    upcoming = [b for b in bookings if b.session.start >= now]
    past = [b for b in bookings if b.session.start < now]
    upcoming.sort(key=lambda b: b.session.start)
    past.sort(key=lambda b: b.session.start, reverse=True)
    offers = [{"booking": b, "seconds": seconds_left(b)} for b in open_offers(current_user)]
    from .booking import waitlist_place
    ranks = {}
    for booking in upcoming:
        if booking.status == "Waiting":
            ranks[booking.id] = waitlist_place(current_user.id, booking.class_session_id)
    return render_template(
        "my_bookings.html",
        upcoming=upcoming,
        past=past,
        offers=offers,
        ranks=ranks,
        cancel_states={b.id: cancel_block(b, now) for b in upcoming},
    )


@bp.get("/notifications")
@login_required
def notifications():
    if current_user.role not in ("member", "staff"):
        abort(403)
    rows = (Notification.query
            .filter_by(user_id=current_user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .all())
    return render_template("notifications.html", notifications=rows)


@bp.post("/notifications/<int:notification_id>/read")
@login_required
def mark_notification_read(notification_id):
    if current_user.role not in ("member", "staff"):
        abort(403)
    row = db.get_or_404(Notification, notification_id)
    if row.user_id != current_user.id:
        abort(403)
    row.read = True
    db.session.commit()
    return redirect(url_for("access.notifications"), code=303)


@bp.post("/notifications/read-all")
@login_required
def mark_all_notifications_read():
    if current_user.role not in ("member", "staff"):
        abort(403)
    Notification.query.filter_by(user_id=current_user.id, read=False).update(
        {"read": True}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for("access.notifications"), code=303)
