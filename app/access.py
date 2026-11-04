"""Access and pages module.

Responsible for role checks before any booking or admin action, and for building
every page from the latest query (including places left and refusal messages).
"""
from flask import Blueprint

access_bp = Blueprint("access", __name__)

# TODO (Yanzhen Yang): routes for timetable, booking, My bookings, roster, plans.
