# -*- coding: utf-8 -*-
"""The nine records.

User, Membership plan, Membership, Instructor, Instructor skill, Room,
Class type, Class session, Booking.
"""
from datetime import date, datetime

from flask_login import UserMixin

from . import db


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="member")   # member | staff | instructor
    phone = db.Column(db.String(30))
    status = db.Column(db.String(10), nullable=False, default="active")

    memberships = db.relationship("Membership", back_populates="user")
    bookings = db.relationship("Booking", back_populates="user")
    notifications = db.relationship("Notification", back_populates="user")

    @property
    def is_staff(self):
        return self.role == "staff"


class MembershipPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    credit_limit = db.Column(db.Integer)                 # None = unlimited
    off_peak = db.Column(db.Boolean, nullable=False, default=False)


class PlanChange(db.Model):
    """One membership card life-cycle event.

    Every card starts here (op kind). Renewing keeps the same card and extends
    ``end``; the change row records what was extended and how the credits moved.
    Switching plans closes the old card (status "replaced") and opens a new card
    (op "switch"); the old and new cards are linked through ``linked_id`` so a
    member's history reads as one story.
    """
    id = db.Column(db.Integer, primary_key=True)
    membership_id = db.Column(db.Integer, db.ForeignKey("membership.id"), nullable=False)
    kind = db.Column(db.String(12), nullable=False)      # open | renew | switch
    plan_id = db.Column(db.Integer, db.ForeignKey("membership_plan.id"), nullable=False)
    prior_end = db.Column(db.Date)
    new_end = db.Column(db.Date)
    prior_credits = db.Column(db.Integer)
    new_credits = db.Column(db.Integer)
    linked_id = db.Column(db.Integer, db.ForeignKey("membership.id"))  # switch: the other card
    at = db.Column(db.DateTime, default=datetime.utcnow)

    membership = db.relationship("Membership", foreign_keys=[membership_id])
    plan = db.relationship("MembershipPlan")


class Membership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey("membership_plan.id"), nullable=False)
    start = db.Column(db.Date, nullable=False)
    end = db.Column(db.Date, nullable=False)
    credits_remaining = db.Column(db.Integer)             # None = unlimited
    status = db.Column(db.String(10), nullable=False, default="active")

    user = db.relationship("User", back_populates="memberships")
    plan = db.relationship("MembershipPlan")

    @property
    def plan_name(self):
        return self.plan.name if self.plan else ""

    @property
    def is_expired(self):
        return self.end < date.today()

    @property
    def credits_exhausted(self):
        return self.credits_remaining is not None and self.credits_remaining <= 0


class Instructor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    profile = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)

    skills = db.relationship("InstructorSkill", back_populates="instructor")
    user = db.relationship("User")


class ClassChangeProposal(db.Model):
    """A staff edit that remains invisible until its target instructor agrees."""
    id = db.Column(db.Integer, primary_key=True)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False, index=True)
    submitted_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    target_instructor_id = db.Column(db.Integer, db.ForeignKey("instructor.id"), nullable=False)
    class_type_id = db.Column(db.Integer, db.ForeignKey("class_type.id"), nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey("instructor.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    base_instructor_id = db.Column(db.Integer, nullable=False)
    base_room_id = db.Column(db.Integer, nullable=False)
    base_start = db.Column(db.DateTime, nullable=False)
    base_end = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(255))

    session = db.relationship("ClassSession")
    target_instructor = db.relationship("Instructor", foreign_keys=[target_instructor_id])
    instructor = db.relationship("Instructor", foreign_keys=[instructor_id])
    class_type = db.relationship("ClassType")
    room = db.relationship("Room")
    submitted_by = db.relationship("User")


class InstructorAbsenceRequest(db.Model):
    """An instructor's request for staff to arrange cover for one class."""
    id = db.Column(db.Integer, primary_key=True)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False, index=True)
    instructor_id = db.Column(db.Integer, db.ForeignKey("instructor.id"), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reason = db.Column(db.String(255))
    status = db.Column(db.String(12), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime)

    session = db.relationship("ClassSession")
    instructor = db.relationship("Instructor")
    requested_by = db.relationship("User")


class InstructorSkill(db.Model):
    instructor_id = db.Column(db.Integer, db.ForeignKey("instructor.id"), primary_key=True)
    class_type_id = db.Column(db.Integer, db.ForeignKey("class_type.id"), primary_key=True)

    instructor = db.relationship("Instructor", back_populates="skills")
    class_type = db.relationship("ClassType")


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    equipment = db.Column(db.String(120))


class ClassType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)
    required_equipment = db.Column(db.String(120))
    duration_min = db.Column(db.Integer, nullable=False, default=60)
    description = db.Column(db.Text)


class ClassSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_type_id = db.Column(db.Integer, db.ForeignKey("class_type.id"), nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey("instructor.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="scheduled")

    class_type = db.relationship("ClassType")
    instructor = db.relationship("Instructor")
    room = db.relationship("Room")
    bookings = db.relationship("Booking", back_populates="session")

    @property
    def booked_count(self):
        return sum(1 for b in self.bookings if b.status == "Booked")

    @property
    def places_left(self):
        """Rule: computed on read, never stored.

        An offer holds a place until it is confirmed or dropped. Attended and
        no-show keep the place, so the seat is not sold again.
        """
        held = sum(1 for b in self.bookings
                   if b.status in ("Booked", "Offered", "Attended", "No-show"))
        return self.capacity - held


class ScheduleLock(db.Model):
    """The single row that serializes timetable writes across all classes."""
    id = db.Column(db.Integer, primary_key=True)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False)
    membership_id = db.Column(db.Integer, db.ForeignKey("membership.id"), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="Booked")
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)
    cancelled_at = db.Column(db.DateTime)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Set when a time change pulls this class inside the two-hour cut-off.
    # That member can still cancel until the class starts. After the start,
    # the credit is not returned.
    late_change_cancel = db.Column(db.Boolean, nullable=False, default=False)
    offered_at = db.Column(db.DateTime)
    skip_seen = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", back_populates="bookings")
    session = db.relationship("ClassSession", back_populates="bookings")
    membership = db.relationship("Membership")


class Notification(db.Model):
    """One message left for a member when a class they booked changes.

    Written when a schedule change takes effect (instructor, room, time or class
    type). ``read`` is cleared by the member on the Notifications page.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=True)
    message = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    read = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", back_populates="notifications")
    session = db.relationship("ClassSession")
