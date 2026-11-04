"""Central application configuration."""
import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # MySQL (InnoDB) connection; override via the DATABASE_URL environment variable.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://user:password@localhost/northbank_fitness",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
