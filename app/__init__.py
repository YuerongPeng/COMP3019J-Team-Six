# -*- coding: utf-8 -*-
"""Northbank Fitness Centre - application factory.

Three blueprints match the three modules in the design report:
  access      - role checks and every rendered page      (Yang)
  booking     - the only writer of Booked rows/credits    (Ma)
  scheduling  - class create/edit and roster              (Peng)
"""
import os

from flask import Flask, render_template
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

db = SQLAlchemy()
login_manager = LoginManager()


def _ensure_booking_columns(app):
    """Add columns to a database created before waitlist offers."""
    statements = {
        "late_change_cancel": "ALTER TABLE booking ADD COLUMN late_change_cancel BOOLEAN NOT NULL DEFAULT 0",
        "offered_at": "ALTER TABLE booking ADD COLUMN offered_at DATETIME",
        "skip_seen": "ALTER TABLE booking ADD COLUMN skip_seen BOOLEAN NOT NULL DEFAULT 0",
    }
    with app.app_context():
        try:
            insp = inspect(db.engine)
        except Exception:
            return
        if not insp.has_table("booking"):
            return
        names = {col["name"] for col in insp.get_columns("booking")}
        changed = False
        for name, sql in statements.items():
            if name not in names:
                db.session.execute(text(sql))
                changed = True
        if changed:
            db.session.commit()


def _ensure_instructor_account(app):
    """Link Maya Lin to a login so she can record attendance on her own classes.

    Existing databases were created before instructors could sign in. This adds
    the column and the one demo account. It does not build the rest of the
    instructor area.
    """
    with app.app_context():
        try:
            insp = inspect(db.engine)
        except Exception:
            return
        if not insp.has_table("instructor"):
            return
        names = {col["name"] for col in insp.get_columns("instructor")}
        if "user_id" not in names:
            db.session.execute(text("ALTER TABLE instructor ADD COLUMN user_id INTEGER"))
            db.session.commit()
        from werkzeug.security import generate_password_hash

        from .models import Instructor, User
        maya = Instructor.query.filter_by(name="Maya Lin").first()
        if maya is None or maya.user_id is not None:
            return
        user = User.query.filter_by(email="maya@example.com").first()
        if user is None:
            user = User(name="Maya Lin", email="maya@example.com", role="instructor",
                        password_hash=generate_password_hash("password"))
            db.session.add(user)
            db.session.flush()
        maya.user_id = user.id
        db.session.commit()


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True,
                template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
                static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"))
    app.config.from_object(config_object)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    _ensure_booking_columns(app)
    _ensure_instructor_account(app)
    login_manager.init_app(app)
    login_manager.login_view = "access.login"
    login_manager.login_message = "Please sign in to continue."

    from . import access, booking, membership, scheduling
    app.register_blueprint(access.bp)
    app.register_blueprint(booking.bp)
    app.register_blueprint(membership.bp)
    app.register_blueprint(scheduling.bp)

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def missing(_):
        return render_template("404.html"), 404

    @app.cli.command("seed")
    def seed_command():
        """Load the starting rows: plans, users, instructors, rooms, classes."""
        from .seed import seed
        with app.app_context():
            db.create_all()
            seed()
        print("seeded")

    return app
