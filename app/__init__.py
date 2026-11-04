"""Northbank Fitness Centre — Flask application factory."""
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)

    # Load the default configuration, then any instance overrides.
    app.config.from_object("config.Config")
    if config_object is not None:
        app.config.from_object(config_object)

    db.init_app(app)
    login_manager.init_app(app)

    # Register the three modules: access and pages, booking, scheduling.
    from app import access, booking, scheduling  # noqa: F401

    return app
