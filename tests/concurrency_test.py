# -*- coding: utf-8 -*-
"""Concurrency check: many members book the same class at once.

With the SQLite busy timeout the writers queue instead of failing, and the
capacity is re-read under the write lock, so the total booked must never exceed
capacity and no request should raise (which would be a 500 in the web app).
"""
import os
import sys
import threading
from datetime import date, datetime, timedelta

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import create_app, db
from app.models import Booking, ClassSession, Membership, MembershipPlan, User

app = create_app()

results = []


def book(session_id, email):
    with app.app_context():
        try:
            user = User.query.filter_by(email=email).first()
            membership = Membership.query.filter_by(user_id=user.id).first()
            db.session.execute(
                db.text("UPDATE class_session SET capacity = capacity WHERE id = :i"),
                {"i": session_id})
            cls = db.session.get(ClassSession, session_id)
            dup = Booking.query.filter_by(
                user_id=user.id, class_session_id=session_id, status="Booked").first()
            if dup:
                db.session.rollback()
                return "dup"
            booked = Booking.query.filter_by(
                class_session_id=session_id, status="Booked").count()
            if booked >= cls.capacity:
                db.session.rollback()
                return "full"
            db.session.add(Booking(user_id=user.id, class_session_id=session_id,
                                   membership_id=membership.id, status="Booked"))
            db.session.commit()
            return "ok"
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            return "err:%s" % type(exc).__name__


def main():
    with app.app_context():
        db.drop_all()
        db.create_all()
        from app.seed import seed
        seed()

        cls = ClassSession.query.filter(
            ClassSession.start >= datetime.now()).order_by(ClassSession.start).first()
        sid, cap = cls.id, cls.capacity

        plan = MembershipPlan.query.first()
        for i in range(15):
            u = User(name="Load%d" % i, email="load%d@example.com" % i,
                     role="member", password_hash="x", status="active")
            db.session.add(u)
            db.session.flush()
            db.session.add(Membership(user_id=u.id, plan_id=plan.id,
                                      start=date.today(),
                                      end=date.today() + timedelta(days=30),
                                      credits_remaining=None))
        db.session.commit()
        emails = [u.email for u in User.query.filter(User.email.like("load%")).all()]

    threads = [threading.Thread(target=lambda e=e: results.append(book(sid, e)))
               for e in emails]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with app.app_context():
        booked = Booking.query.filter_by(
            class_session_id=sid, status="Booked").count()
        print("results:", sorted(set(r for r in results)))
        print("capacity=%d  final booked=%d" % (cap, booked))
        print("no error (500-like):", not any(r.startswith("err:") for r in results))
        print("no oversell:", booked <= cap)
        assert not any(r.startswith("err:") for r in results), results
        assert booked <= cap, "OVERSOLD %d > %d" % (booked, cap)
        print("PASS: concurrent booking did not error or oversell")


if __name__ == "__main__":
    main()
