# -*- coding: utf-8 -*-
"""Seed data.

Inserts the starting rows on first setup: four membership plans, three members of
staff-level data (users), instructors with their skills, rooms, class types and an
initial timetable, so no manual data entry is required and every demonstration runs
from the same known rows.
"""
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from . import db
from .models import (Booking, ClassSession, ClassType, Instructor, InstructorSkill,
                     Membership, MembershipPlan, Room, User)


def soon(days, hour):
    day = date.today() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, 0)


def seed():
    """Populate the database. Safe to call once per database."""
    if User.query.first() is not None:
        return 0

    plans = [
        MembershipPlan(name="Monthly unlimited", duration_days=30, credit_limit=None, off_peak=False),
        MembershipPlan(name="Ten-visit pack", duration_days=90, credit_limit=10, off_peak=False),
        MembershipPlan(name="Seven-day trial", duration_days=7, credit_limit=3, off_peak=False),
        MembershipPlan(name="Off-peak", duration_days=30, credit_limit=None, off_peak=True),
    ]
    db.session.add_all(plans)

    staff = User(name="Sam Staff", email="staff@example.com", role="staff",
                 password_hash=generate_password_hash("password"))
    alex = User(name="Alex Chen", email="alex@example.com", role="member",
                password_hash=generate_password_hash("password"))
    mei = User(name="Mei Lin", email="mei@example.com", role="member",
               password_hash=generate_password_hash("password"))
    maya_user = User(name="Maya Lin", email="maya@example.com", role="instructor",
                     password_hash=generate_password_hash("password"))
    quinn_user = User(name="Quinn He", email="quinn@example.com", role="instructor",
                      password_hash=generate_password_hash("password"))
    rob_user = User(name="Rob Diaz", email="rob@example.com", role="instructor",
                    password_hash=generate_password_hash("password"))
    db.session.add_all([staff, alex, mei, maya_user, quinn_user, rob_user])
    db.session.flush()

    db.session.add_all([
        Membership(user_id=alex.id, plan_id=plans[0].id, start=date.today(),
                   end=date.today() + timedelta(days=30), credits_remaining=None),
        Membership(user_id=mei.id, plan_id=plans[1].id, start=date.today(),
                   end=date.today() + timedelta(days=90), credits_remaining=6),
    ])

    maya = Instructor(name="Maya Lin", profile="Yoga and mobility", user_id=maya_user.id)
    quinn = Instructor(name="Quinn He", profile="Yoga and cycling", user_id=quinn_user.id)
    rob = Instructor(name="Rob Diaz", profile="Strength", user_id=rob_user.id)
    db.session.add_all([maya, quinn, rob])
    db.session.flush()

    yoga = ClassType(name="Yoga", required_equipment="mats", duration_min=60)
    spin = ClassType(name="Cycling", required_equipment="bikes", duration_min=45)
    pilates = ClassType(name="Pilates", required_equipment="mats", duration_min=60)
    strength = ClassType(name="Strength", required_equipment="weights", duration_min=60)
    db.session.add_all([yoga, spin, pilates, strength])
    db.session.flush()

    db.session.add_all([
        InstructorSkill(instructor_id=maya.id, class_type_id=yoga.id),
        InstructorSkill(instructor_id=maya.id, class_type_id=pilates.id),
        InstructorSkill(instructor_id=quinn.id, class_type_id=yoga.id),
        InstructorSkill(instructor_id=quinn.id, class_type_id=spin.id),
        InstructorSkill(instructor_id=rob.id, class_type_id=strength.id),
    ])

    studio1 = Room(name="Studio 1", capacity=20, equipment="bikes, mats")
    studio2 = Room(name="Studio 2", capacity=14, equipment="mats")
    hall = Room(name="Hall", capacity=30, equipment="weights, mats")
    db.session.add_all([studio1, studio2, hall])
    db.session.flush()

    sessions = [
        ClassSession(class_type_id=yoga.id, instructor_id=maya.id, room_id=studio2.id,
                     start=soon(1, 18), end=soon(1, 19), capacity=14),
        ClassSession(class_type_id=spin.id, instructor_id=quinn.id, room_id=studio1.id,
                     start=soon(1, 19), end=soon(1, 20), capacity=20),
        ClassSession(class_type_id=pilates.id, instructor_id=maya.id, room_id=studio2.id,
                     start=soon(2, 9), end=soon(2, 10), capacity=10),
        ClassSession(class_type_id=strength.id, instructor_id=rob.id, room_id=hall.id,
                     start=soon(2, 18), end=soon(2, 19), capacity=8),
        ClassSession(class_type_id=yoga.id, instructor_id=quinn.id, room_id=studio2.id,
                     start=soon(3, 18), end=soon(3, 19), capacity=14),
        ClassSession(class_type_id=spin.id, instructor_id=quinn.id, room_id=studio1.id,
                     start=soon(3, 20), end=soon(3, 21), capacity=20),
    ]
    db.session.add_all(sessions)
    db.session.flush()

    # one class at 13 of 14, so the two-request demonstration has a target
    near_full = sessions[4]
    mei_card = Membership.query.filter_by(user_id=mei.id).first()
    alex_card = Membership.query.filter_by(user_id=alex.id).first()
    for _ in range(13):
        db.session.add(Booking(user_id=mei.id, class_session_id=near_full.id,
                               membership_id=mei_card.id, status="Booked"))
    db.session.add(Booking(user_id=alex.id, class_session_id=sessions[0].id,
                           membership_id=alex_card.id, status="Booked"))
    db.session.add(Booking(user_id=mei.id, class_session_id=sessions[5].id,
                           membership_id=mei_card.id, status="Cancelled", active=False))

    db.session.commit()
    return 15
