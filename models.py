# -*- coding: utf-8 -*-
"""The nine records.

User, Membership plan, Membership, Instructor, Instructor skill, Room,
Class type, Class session, Booking.
"""
from datetime import datetime

from flask_login import UserMixin

from . import db


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="member")   # member | staff
    phone = db.Column(db.String(30))
    status = db.Column(db.String(10), nullable=False, default="active")

    memberships = db.relationship("Membership", back_populates="user")
    bookings = db.relationship("Booking", back_populates="user")

    @property
    def is_staff(self):
        return self.role == "staff"


class MembershipPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    credit_limit = db.Column(db.Integer)                 # None = unlimited
    off_peak = db.Column(db.Boolean, nullable=False, default=False)


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


class Instructor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    profile = db.Column(db.Text)

    skills = db.relationship("InstructorSkill", back_populates="instructor")


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
        """Rule: computed on read, never stored."""
        return self.capacity - self.booked_count


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    class_session_id = db.Column(db.Integer, db.ForeignKey("class_session.id"), nullable=False)
    membership_id = db.Column(db.Integer, db.ForeignKey("membership.id"), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="Booked")
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)
    cancelled_at = db.Column(db.DateTime)
    active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", back_populates="bookings")
    session = db.relationship("ClassSession", back_populates="bookings")
    membership = db.relationship("Membership")
