# Lexie Performance Hub

Lexie is a server-rendered Django application for employee performance tracking. Django serves the login page, dashboards, forms, reports, and the legacy JSON API from one process. The web application uses Django session authentication; the older API endpoints remain available for compatibility.

## Requirements

- Python 3.12 or later
- SQLite (default), PostgreSQL, or MySQL

No Node.js process or separate frontend server is required. The templates use Tailwind CSS and Chart.js from their CDNs.

## Windows PowerShell setup

From the repository root, configure the existing backend virtual environment:

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For a new environment, create one with a Python version supported by the installed Django release:

```powershell
cd backend
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Keep the existing `backend\.env` and its `DATABASE_URL` setting. The supplied local configuration selects `backend\django.sqlite3`, which contains the active Django application data. The separate legacy `backend\lexie.db` is not used by Django and is retained unchanged; do not replace or merge database files.

For a fresh setup only, copy `.env.example` to `.env` and set a strong `SECRET_KEY`. Do not overwrite an existing `.env`.

## Initialize data and run

Run from `backend` with the virtual environment active:

```powershell
python manage.py check
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. The root URL redirects to the sign-in page.

The demo administrator is `admin@lexie.ph` / `Admin123!`. This password is for local development only. `seed_demo` is idempotent but resets the seeded administrator password; do not run it where that reset is unwanted.

Run the test suite with:

```powershell
python manage.py test api
```

## Roles and access

- **Admin:** workforce, departments, evaluation criteria, predictions, reports, user accounts, and personal settings.
- **HR / Manager:** workforce, departments, evaluations, predictions, reports, and personal settings; cannot manage accounts or evaluation criteria.
- **Employee:** read-only access to their own profile, performance/evaluation history, predictions, and personal settings. Employee-scoped querysets are applied on the server.

Existing `staff` role values continue to work as employee accounts for backwards compatibility. A Django session is used for browser login, and sign-out requires a CSRF-protected POST.

## Data and compatibility notes

- Existing Django models, migrations, trained model artifacts, and database contents are preserved. `makemigrations --check --dry-run` should report no changes.
- A performance record is represented by an existing `Evaluation` row. This avoids introducing a new table or migration and keeps historical scores intact.
- The five score criteria are editable by Admin in `api/criteria.json`; changing their display labels does not change stored evaluation fields.
- Single and batch prediction pages call the existing `api.ml.predict.predict_performance` function and save results in the existing `Prediction` table.
- CSV and PDF reports use the selected department and date range. PDF generation uses the Python standard library.
- The old React source is retained as a backup and is not part of the Django runtime.
- The legacy `/auth`, `/users`, `/employees`, `/evaluations`, `/predictions`, `/reports`, `/clients`, and `/health` JSON routes are retained. Django REST Framework, Simple JWT, and CORS middleware therefore remain required; browser pages use session authentication instead.

## Database configuration

SQLite is the default. PostgreSQL and MySQL can be configured through `DATABASE_URL` in `backend\.env`; the matching database driver is included in `requirements.txt`. Back up the selected database before running migrations or changing database settings.

The Django admin site is available at <http://127.0.0.1:8000/admin/>.
