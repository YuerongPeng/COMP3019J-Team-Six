# -*- coding: utf-8 -*-
"""Central application configuration."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # SQLite keeps the skeleton runnable with no server; the design targets MySQL
    # (InnoDB) for the row lock, so set DATABASE_URL to switch:
    #   mysql+pymysql://user:password@localhost/northbank_fitness
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "northbank.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # SQLite locks the whole database file, not one row. Give writers a 30s
    # window to wait out a conflicting writer instead of failing instantly, so a
    # member booking and a staff edit that land at the same moment just queue.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"timeout": 30},
    }
