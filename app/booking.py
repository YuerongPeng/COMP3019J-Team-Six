"""Booking module.

The only writer of Booked rows and credits. Checks plan, cut-off and capacity, and
owns the MySQL row-lock transaction that protects the capacity rule.
"""
from flask import Blueprint

booking_bp = Blueprint("booking", __name__)

# TODO (Yilong Ma): plan check, capacity rule, two-hour cut-off,
#                   cancellation, row-lock transaction.
