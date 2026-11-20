# Northbank Fitness Centre

COMP3019J Team Six. Group-class timetable, booking and cancellation, class management
and membership plans for Northbank Fitness Centre.

| Member | Student number |
|---|---|
| Yilong Ma | 24372302 |
| Yuerong Peng | 24372318 |
| Yanzhen Yang | 24372321 |

## Roles

- **Member** - browse the timetable, book and cancel classes, see only their own bookings.
- **Staff** - create and edit classes, read the roster, maintain membership plans.
- **Visitor** - browse only.
- **Instructor** - never logs in; staff maintain their experience as data.

## Stack

HTML, CSS and JavaScript in the browser; Python, Flask, Jinja2, Flask-WTF and
Flask-Login on the server; MySQL (InnoDB) as the design target, SQLite for local runs.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

flask --app run seed              # tables + starting rows
flask --app run run --debug       # http://127.0.0.1:5000
```

Seeded accounts, password `password`:

| Email | Role |
|---|---|
| alex@example.com | member, monthly unlimited |
| mei@example.com | member, ten-visit pack |
| staff@example.com | staff, no plan |

The seed includes one class at 13 of 14 places, so the "two members, one place"
demonstration has a target.

## Layout

```
config.py              Config (SECRET_KEY, DATABASE_URL)
run.py                 create_app() and the dev server
app/__init__.py        application factory, blueprints, error pages, `seed` command
app/models.py          the nine records
app/access.py          role checks and every page          (Yang)
app/booking.py         the only writer of Booked rows      (Ma)
app/scheduling.py      class create/edit, roster, plans    (Peng)
app/seed.py            the starting rows
templates/ static/     the pages and the theme
```

## Routes

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/` | all | redirect to the timetable |
| GET | `/timetable` | all | classes with places left |
| GET | `/classes/<id>` | all | one class, or 404 |
| GET, POST | `/login` | visitor | sign in |
| GET, POST | `/register` | visitor | create an account |
| GET, POST | `/account/password` | signed in | change own password |
| GET | `/account` | member | own plan, renew / switch |
| POST | `/account/renew` | member | extend current plan in place |
| POST | `/account/switch` | member | change to another plan |
| POST | `/logout` | signed in | clear the session |
| GET | `/my-bookings` | member | own bookings only |
| POST | `/classes/<id>/book` | member | lock, count, insert |
| POST | `/bookings/<id>/cancel` | member (owner) | two-hour cut-off |
| GET, POST | `/admin/classes/new` | staff | create a class |
| GET, POST | `/admin/classes/<id>/edit` | staff | same class id |
| GET | `/admin/classes/<id>/roster` | staff | who is still booked |
| GET, POST | `/admin/plans` | staff | plans and memberships |

## Rules in place

1. **Capacity** - the class row is locked, Booked rows are counted, then the insert
   happens in the same transaction; the count is never stored.
2. **No duplicate** - one Booked row per member per class.
3. **No overlap** - a new class must not overlap one the member already booked.
4. **Credits** - deducted only when the plan uses them, returned to the same plan on a
   valid cancellation.
5. **Edit keeps identity** - a class edit updates the same row, so bookings stay
   attached; capacity may not fall below the number already booked.

6. **Edits cannot double-book a member** - a class edit may change the time, the
   instructor, the room or the capacity, but it is refused when a member already
   booked on that class would end up in two classes at once.

7. **Account and membership** - a member registers their own account, changes their
   own password, renews the current plan in place (end extends from the current end,
   credits refill to the limit, no second card) or switches to another plan (the old
   card is marked "replaced", a new card starts today with the new plan's allowance,
   and bookings already made stay attached to the old card). The timetable and the
   class page show a disabled button with a single reason when a booking is refused,
   in this order: already booked, plan expired, no credits, off-peak window, time
   conflict, full. The server re-checks the same rules on submit.

## Tests

```bash
python tests/smoke_test.py        # 22 checks over routes, roles and rules
python tests/edit_clash_test.py   # 5 checks focused on the class-edit clash
```

## Still to do

CSRF tokens on every POST (Flask-WTF), an ownership check on every route that takes an
id, and plan maintenance on `/admin/plans`.
