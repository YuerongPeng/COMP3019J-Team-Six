# -*- coding: utf-8 -*-
"""Membership and account module.

Owns the member-facing account flow: registration, password change, renewing the
current plan, and switching to another plan. The booking/refusal logic (why a
button is disabled) lives in ``booking.book_block`` / ``booking.cancel_block``.
"""
from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .models import Membership, MembershipPlan, PlanChange, User

bp = Blueprint("membership", __name__)


# --------------------------------------------------------------------------
# Renew / switch helpers
# --------------------------------------------------------------------------

def _renewal_end(current_end, plan):
    """Extend from the current end date, never from today.

    A still-valid card extends from its own end date; an already-lapsed card
    extends from today so the member does not pay again for days already gone.
    """
    base = current_end if current_end >= date.today() else date.today()
    return base + timedelta(days=plan.duration_days)


def active_card(user_id):
    return (Membership.query
            .filter_by(user_id=user_id, status="active")
            .order_by(Membership.start.desc())
            .first())


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
