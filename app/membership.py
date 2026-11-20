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
        return "已订", "你已预订这节课"

    if membership is None or not membership_covering(membership, session_row.start):
        if membership is None:
            detail = "你还没有有效方案，请先到「方案」页订阅"
        elif membership.end < date.today():
            detail = "你的方案已于 %s 到期" % membership.end
        else:
            detail = "当前方案不覆盖这节课的日期"
        return "方案已到期", detail

    if membership.credits_exhausted:
        return "课时用完", "你的方案课时已用完，请续订或更换方案"

    if membership.plan.off_peak and session_row.start.hour >= 16:
        return "时段不符", "此方案只能订16:00前开始的课"

    for other in member.bookings:
        if other.status != "Booked":
            continue
        if other.session.start < session_row.end and session_row.start < other.session.end:
            weekday = "周" + "一二三四五六日"[other.session.start.weekday()]
            when = other.session.start.strftime("%H:%M")
            return "时间冲突", "与%s %s %s重叠" % (
                weekday, when, other.session.class_type.name)

    if session_row.places_left <= 0:
        return "已满", "这节课已满"

    return None


def can_cancel(booking):
    """Whether ``booking`` may still be cancelled, plus a reason when it may not.

    The two-hour cut-off is waived when staff moved the start time into the
    cut-off window after the member booked: the member did not create the risk,
    so they may still cancel.
    """
    if booking.status != "Booked":
        return False, "该预订已不是进行中状态"
    now = datetime.now()
    if booking.session.start - now < timedelta(hours=2):
        return False, "距开始不足两小时，不能取消"
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
        flash("请填写姓名、邮箱和密码。")
        return render_template("register.html"), 200
    if password != confirm:
        flash("两次输入的密码不一致。")
        return render_template("register.html"), 200
    if len(password) < 6:
        flash("密码至少需要 6 位。")
        return render_template("register.html"), 200
    if User.query.filter_by(email=email).first() is not None:
        flash("该邮箱已注册。")
        return render_template("register.html"), 200

    user = User(name=name, email=email, role="member",
                password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    login_user(user)
    flash("注册成功，已为你登录。请到「方案」页选择一个方案开始使用。")
    return redirect(url_for("access.timetable"), code=303)


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not check_password_hash(current_user.password_hash, current):
            flash("当前密码不正确。")
            return render_template("change_password.html"), 200
        if len(new) < 6:
            flash("新密码至少需要 6 位。")
            return render_template("change_password.html"), 200
        if new != confirm:
            flash("两次输入的新密码不一致。")
            return render_template("change_password.html"), 200
        current_user.password_hash = generate_password_hash(new)
        db.session.commit()
        flash("密码已更新。")
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
        flash("你还没有有效方案，请先订阅。")
        return redirect(url_for("membership.account"), code=303)
    return _renew_or_switch(card, card.plan)


@bp.post("/account/switch")
@login_required
def switch_plan():
    plan_id = request.form.get("plan_id", type=int)
    plan = db.session.get(MembershipPlan, plan_id) if plan_id else None
    if plan is None:
        flash("请选择一个方案。")
        return redirect(url_for("membership.account"), code=303)
    card = active_card(current_user.id)
    if card is None:
        return _open_new_membership(plan, None)
    if plan.id == card.plan_id:
        flash("这就是你当前的方案。")
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
        message = "已更换为「%s」，新卡从今天开始，课时按新方案重给。" % plan.name
        if prior_credits:
            message += " 旧卡未用完的 %d 次课时作废。" % prior_credits
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
        message = "已续订「%s」，有效期延长至 %s。" % (plan.name, new_end)

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
    flash("已为你开通「%s」，有效期至 %s。" % (plan.name, new_end))
    return redirect(url_for("membership.account"), code=303)
