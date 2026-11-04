"""Scheduling module.

Checks instructor experience, room suitability, overlaps and capacity when a class is
saved, and updates a class without changing its identity (keeping existing bookings).
"""
from flask import Blueprint

scheduling_bp = Blueprint("scheduling", __name__)

# TODO (Yuerong Peng): instructor/room/skill validation, overlap check,
#                      class-edit workflow that keeps existing bookings.
