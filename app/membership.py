# -*- coding: utf-8 -*-
"""Membership and account module.

Owns the member-facing account flow: registration, password change, renewing the
current plan, and switching to another plan. Holds the shared helpers that decide
what a calendar row's disabled button should say, so the timetable, the class
detail page and the server-side booking check all read from one source of truth.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .models import Membership, MembershipPlan, PlanChange, User

bp = Blueprint("membership", __name__)


def _active_membership(user_id):
    # Deferred so this module has no import-time dependency on app.access.
    from .access import active_membership
    return active_membership(user_id)


# --------------------------------------------------------------------------
# Shared decision helpers
# --------------------------------------------------------------------------

def _renewal_end(current_end, plan):
    """Extend from the current end date, never from today.

    A still-valid card extends from its own end date; an already-lapsed card
    extends from today so the member does not pay again for days already gone.
    """
    base = current_end if current_end >= date.today() else date.today()
    return base + timedelta(days=plan.duration_days)


def membership_covering(membership, session_start):
    """Does this membership card cover a class at ``session_start``?"""
    if membership is None or membership.status != "active":
        return False
    return membership.end >= session_start.date() and membership.start <= session_start.date()


def active_card(user_id):
    return (Membership.query
            .filter_by(user_id=user_id, status="active")
            .order_by(Membership.start.desc())
            .first())


def book_refusal(session_row, member):
    """Return ``None`` to allow booking, or a (message, detail) pair.

    ``detail`` is the full explanation shown on click / hover / mobile; ``message``
    is the short text written on the grey button. Several rules can fail at once, so
    the first match in the required order wins: already booked, plan expired, no
    credits, off-peak window, time conflict, full.
    """
    membership = _active_membership(member.id)

    already = any(b.class_session_id == session_row.id and b.status == "Booked"
                  for b in member.bookings)
    if already:
        return "Booked", "You already have this class booked"

    if membership is None or not membership_covering(membership, session_row.start):
        if membership is None:
            detail = "You have no active plan. Subscribe on your Plans page first."
        elif membership.end < date.today():
            detail = "Your plan expired on %s" % membership.end
        else:
            detail = "Your current plan does not cover the date of this class"
        return "Plan expired", detail

    if membership.credits_exhausted:
        return "No credits", "You have used all the classes on this plan. Renew or switch to keep booking."

    if membership.plan.off_peak and session_row.start.hour >= 16:
        return "Wrong time", "This plan can only book classes that start before 16:00"

    for other in member.bookings:
        if other.status != "Booked":
            continue
        if other.session.start < session_row.end and session_row.start < other.session.end:
            when = other.session.start.strftime("%A %H:%M")
            return "Time conflict", "Overlaps your %s with %s" % (
                when, other.session.class_type.name)

    if session_row.places_left <= 0:
        return "Full", "This class is full"

    return None


def can_cancel(booking):
    """Whether ``booking`` may still be cancelled, plus a reason when it may not.

    The two-hour cut-off is waived when staff moved the start time into the
    cut-off window after the member booked: the member did not create the risk,
    so they may still cancel.
    """
    if booking.status != "Booked":
        return False, "That booking is no longer active"
    now = datetime.now()
    if booking.session.start - now < timedelta(hours=2):
        return False, "Less than two hours to the start, you cannot cancel"
    return True, ""


# --------------------------------------------------------------------------
# Account pages and actions
# --------------------------------------------------------------------------

@bp.get("/register")
def register_form():
    return render_template("register.html")


@bp.post("/register")
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if not name or not email or not password:
        flash("Please fill in your name, email and password.")
        return render_template("register.html"), 200
    if password != confirm:
        flash("The two passwords do not match.")
        return render_template("register.html"), 200
    if len(password) < 6:
        flash("The password must be at least 6 characters.")
        return render_template("register.html"), 200
    if User.query.filter_by(email=email).first() is not None:
        flash("That email is already registered.")
        return render_template("register.html"), 200

    user = User(name=name, email=email, role="member",
                password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    login_user(user)
    flash("Account created and you are signed in. Choose a plan on the Plans page to get started.")
    return redirect(url_for("access.timetable"), code=303)


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not check_password_hash(current_user.password_hash, current):
            flash("The current password is incorrect.")
            return render_template("change_password.html"), 200
        if len(new) < 6:
            flash("The new password must be at least 6 characters.")
            return render_template("change_password.html"), 200
        if new != confirm:
            flash("The two new passwords do not match.")
            return render_template("change_password.html"), 200
        current_user.password_hash = generate_password_hash(new)
        db.session.commit()
        flash("Password updated.")
        return redirect(url_for("membership.account"), code=303)
    return render_template("change_password.html")


@bp.get("/account")
@login_required
def account():
    card = active_card(current_user.id)
    plans = MembershipPlan.query.order_by(MembershipPlan.id).all()
    history = (PlanChange.query
               .filter(PlanChange.membership_id.in_(
                   [m.id for m in current_user.memberships]))
               .order_by(PlanChange.at.desc())
               .all())
    return render_template("account.html", card=card, plans=plans, history=history)


@bp.post("/account/renew")
@login_required
def renew():
    card = active_card(current_user.id)
    if card is None:
        flash("You have no active plan yet. Subscribe first.")
        return redirect(url_for("membership.account"), code=303)
    return _renew_or_switch(card, card.plan)


@bp.post("/account/switch")
@login_required
def switch_plan():
    plan_id = request.form.get("plan_id", type=int)
    plan = db.session.get(MembershipPlan, plan_id) if plan_id else None
    if plan is None:
        flash("Please choose a plan.")
        return redirect(url_for("membership.account"), code=303)
    card = active_card(current_user.id)
    if card is None:
        return _open_new_membership(plan, None)
    if plan.id == card.plan_id:
        flash("That is already your current plan.")
        return redirect(url_for("membership.account"), code=303)
    return _renew_or_switch(card, plan)


def _renew_or_switch(card, plan):
    """One card lifecycle event, with the right outcome for renew vs switch."""
    switching = plan.id != card.plan_id
    prior_end = card.end
    prior_credits = card.credits_remaining

    if switching:
        # Close the old card (bookings stay attached to it, unchanged) and open
        # a fresh card from today.
        card.status = "replaced"
        new_start = date.today()
        new_end = new_start + timedelta(days=plan.duration_days)
        new_credits = plan.credit_limit  # full allowance on the new plan
        new_card = Membership(user_id=card.user_id, plan_id=plan.id,
                              start=new_start, end=new_end,
                              credits_remaining=new_credits, status="active")
        db.session.add(new_card)
        db.session.flush()
        change = PlanChange(membership_id=card.id, kind="switch", plan_id=plan.id,
                            prior_end=prior_end, new_end=new_end,
                            prior_credits=prior_credits,
                            new_credits=new_credits, linked_id=new_card.id)
        db.session.add(change)
        message = "Switched to '%s'. The new card starts today and its classes are given by the new plan." % plan.name
        if prior_credits:
            message += " The %d unused classes on the old card are forfeited." % prior_credits
    else:
        # Renew the same card: extend from its own end, top credits back up to
        # the plan limit (no second card).
        new_end = _renewal_end(card.end, plan)
        new_credits = plan.credit_limit if plan.credit_limit is not None else None
        change = PlanChange(membership_id=card.id, kind="renew", plan_id=plan.id,
                            prior_end=prior_end, new_end=new_end,
                            prior_credits=prior_credits, new_credits=new_credits)
        db.session.add(change)
        card.end = new_end
        card.credits_remaining = new_credits
        message = "Renewed '%s' until %s." % (plan.name, new_end)

    db.session.commit()
    flash(message)
    return redirect(url_for("membership.account"), code=303)


def _open_new_membership(plan, _unused):
    """First subscription for a member with no card at all."""
    new_start = date.today()
    new_end = new_start + timedelta(days=plan.duration_days)
    card = Membership(user_id=current_user.id, plan_id=plan.id,
                      start=new_start, end=new_end,
                      credits_remaining=plan.credit_limit, status="active")
    db.session.add(card)
    db.session.flush()
    db.session.add(PlanChange(membership_id=card.id, kind="open", plan_id=plan.id,
                              new_end=new_end, new_credits=plan.credit_limit))
    db.session.commit()
    flash("Opened '%s' for you, valid until %s." % (plan.name, new_end))
    return redirect(url_for("membership.account"), code=303)
