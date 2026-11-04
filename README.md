# COMP3019J Team Six — Northbank Fitness Centre

Web application for Northbank Fitness Centre: group-class timetable, booking and
cancellation, class management and membership plans.

## Team

| Member | Student number |
|--------|----------------|
| Yilong Ma | 24372302 |
| Yuerong Peng | 24372318 |
| Yanzhen Yang | 24372321 |

## Roles

- **Member** — browse the timetable, book and cancel classes, view their own bookings.
- **Staff** — create and edit classes, view the roster, manage membership plans.
- **Visitor** — browse only (unauthenticated).

## Stack

- Frontend: HTML, CSS, JavaScript
- Backend: Python, Flask, Jinja2, Flask-WTF, Flask-Login
- Database: MySQL (InnoDB)

## Setup

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# Install dependencies
python -m pip install -r requirements.txt

# Run the development server
flask run
```

Seed data populates the database on first run (membership plans, instructors, rooms,
class types and an initial timetable), so no manual data entry is required.
