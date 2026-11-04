# PythonAnywhere deployment guide

Replace `<username>` with your PythonAnywhere account name and `<repository-url>` with the GitHub clone URL. These steps assume the repository is at `/home/<username>/webapp` and Django is in its `backend/` directory.

Keep the trained artifacts in `backend/api/ml/`: `rf_model.pkl`, `le_department.pkl`, `le_target.pkl`, and `feature_columns.pkl`. The model loads these files relative to its module; do not retrain or replace them during deployment.

## Deployment steps

1. **Clone the repository** from a PythonAnywhere Bash console:

   ```bash
   cd /home/<username>
   git clone <repository-url> webapp
   cd /home/<username>/webapp/backend
   ```

   Deploy the Django `backend/` directory, not the top-level frontend/build directories.

2. **Create and activate a virtual environment** with a supported Python version (the pinned dependencies support Python 3.10–3.13):

   ```bash
   python3.13 -m venv /home/<username>/.virtualenvs/lexie
   source /home/<username>/.virtualenvs/lexie/bin/activate
   python -m pip install --upgrade pip
   ```

3. **Install the backend dependencies:**

   ```bash
   cd /home/<username>/webapp/backend
   python -m pip install -r requirements.txt
   ```

   PythonAnywhere integrates with the app through WSGI, so Gunicorn is not needed.

4. **Create the server-only environment file.** Copy `.env.example` to `.env`, edit it, and never commit `.env`:

   ```bash
   cp .env.example .env
   ```

   Set at least the following values, replacing the host and secret:

   ```text
   DATABASE_URL=sqlite:///./django.sqlite3
   SECRET_KEY=<long-random-secret>
   DEBUG=false
   ALLOWED_HOSTS=<username>.pythonanywhere.com
   CSRF_TRUSTED_ORIGINS=https://<username>.pythonanywhere.com
   FRONTEND_ORIGINS=
   SECURE_COOKIES=true
   SECURE_SSL_REDIRECT=true
   SECURE_HSTS_SECONDS=3600
   SESSION_COOKIE_HTTPONLY=true
   X_FRAME_OPTIONS=DENY
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=60
   ```

   Generate a secret using the activated environment:

   ```bash
   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
   ```

   With the relative SQLite URL above, the database is `backend/django.sqlite3`. Keep a separate backup before migrations or seeding. PostgreSQL can be added later by installing `psycopg[binary]` and configuring a PostgreSQL `DATABASE_URL`; no DB driver is needed for SQLite.

5. **Apply migrations and check the configuration:**

   ```bash
   python manage.py migrate
   python manage.py check
   ```

6. **Seed demo accounts only for a new/demo database.** Set strong environment values for `DEMO_ADMIN_PASSWORD`, `DEMO_CLIENT_PASSWORD`, and `DEMO_STAFF_PASSWORD`, then run:

   ```bash
   export DEMO_ADMIN_PASSWORD='<strong-unique-password>'
   export DEMO_CLIENT_PASSWORD='<strong-unique-password>'
   export DEMO_STAFF_PASSWORD='<strong-unique-password>'
   python manage.py seed_demo
   unset DEMO_ADMIN_PASSWORD DEMO_CLIENT_PASSWORD DEMO_STAFF_PASSWORD
   ```

   The command updates the demo accounts' passwords and requires a password change at first login. Back up the DB before running this on an existing installation. Create any additional Management, HR Manager, or Team Leader accounts through the account-management workflow.

7. **Collect static files:**

   ```bash
   python manage.py collectstatic --noinput
   ```

   WhiteNoise uses `CompressedManifestStaticFilesStorage` for collected files.

8. **Configure the Web app.** In PythonAnywhere's **Web** tab, select the same Python version and virtual environment (`/home/<username>/.virtualenvs/lexie`). Set the working directory to:

   ```text
   /home/<username>/webapp/backend
   ```

   Edit the WSGI configuration file shown in the Web tab so it imports the project application:

   ```python
   import os
   import sys

   project = "/home/<username>/webapp/backend"
   if project not in sys.path:
       sys.path.insert(0, project)
   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   from wsgi import application
   ```

   The project's `wsgi.py` exports `application`.

9. **Add Static files mappings** in the Web tab:

   | URL | Directory |
   | --- | --- |
   | `/static/` | `/home/<username>/webapp/backend/staticfiles` |
   | `/media/` | `/home/<username>/webapp/backend/media` |

   Django itself serves media only with `DEBUG=true`; in production the PythonAnywhere `/media/` mapping serves uploaded files.

10. **Reload and run a smoke test.** Click **Reload** in the Web tab, then:

    - Open `https://<username>.pythonanywhere.com/health`; expect `{"status":"ok"}`.
    - Log in as each provisioned role (seeded admin, client, staff, plus any created Management/HR Manager/Team Leader accounts). Complete the forced password change for seeded users.
    - As an authorized manager, run a prediction for an employee in a trained department.
    - As a staff user, upload a JPG or PNG profile photo and verify it loads after saving.
    - Confirm static assets load and inspect the PythonAnywhere error log if anything fails.

## Optional scheduled automation

Add these commands in the PythonAnywhere **Tasks** tab and use the same virtual environment. Task times are UTC; examples run at 01:00 UTC (09:00 in Manila):

| Schedule | Command |
| --- | --- |
| 1st day of each month, 01:00 UTC | `cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/lexie/bin/python manage.py create_monthly_evaluations` |
| Daily, 01:20 UTC | `cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/lexie/bin/python manage.py run_classification` |
| Daily, 01:30 UTC | `cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/lexie/bin/python manage.py send_pending_reminders` |
| Daily, 16:05 UTC (00:05 Asia/Manila) | `cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/lexie/bin/python manage.py mark_absences` |

`create_monthly_evaluations` is safe to rerun for a month and creates no duplicate employee/month cycle. `mark_absences` defaults to the previous Asia/Manila date; pass `--date YYYY-MM-DD` to process a specific day.
