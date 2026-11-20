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

db = SQLAlchemy()
login_manager = LoginManager()


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True,
                template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
                static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"))
    app.config.from_object(config_object)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
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
