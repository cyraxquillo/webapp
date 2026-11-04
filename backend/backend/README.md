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

For a fresh setup only, copy `.env.example` to `.env` and set a strong `SECRET_KEY`. Do not overwrite an existing `.env`. The example is configured for the HTTPS PythonAnywhere host; when developing over local HTTP, set `SECURE_COOKIES=false` in the uncommitted `.env`.

## Initialize data and run

Run from `backend` with the virtual environment active:

```powershell
python manage.py check
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. The root URL redirects to the sign-in page.

Before running `seed_demo`, set `DEMO_ADMIN_PASSWORD`, `DEMO_CLIENT_PASSWORD`, and `DEMO_STAFF_PASSWORD` in the environment. The command requires all three values, sets passwords for the seeded account groups, and requires each seeded account to change its password after signing in. It does not print passwords. Since each run resets the seeded accounts' passwords, do not run it where that reset is unwanted.

Run the test suite with:

```powershell
python manage.py test api
```

## Roles and access

- **Admin:** workforce, departments, evaluation criteria, predictions, reports, user accounts, and personal settings.
- **HR / Manager:** workforce, departments, evaluations, predictions, reports, and personal settings; cannot manage accounts or evaluation criteria.
- **Employee:** read-only access to their own profile, performance/evaluation history, predictions, and personal settings. Employee-scoped querysets are applied on the server.
- **Client:** read-only access to the client portal showing the signed-in client's company, services, and next renewal date.

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

## Firebase Hosting and Cloud Run deployment

Firebase Hosting serves the public domain and rewrites application requests to the Django service on Cloud Run. The Firebase SDK in the shared template initializes Firebase Analytics; it does not deploy or host Django by itself. The repository is prepared for Firebase project `lexie-13e6b`, Cloud Run service `lexie-django`, and region `asia-southeast1` (Singapore).

### Production database and secrets

Cloud Run filesystems are ephemeral, so do not deploy the local SQLite database as the production database. Create a persistent PostgreSQL database (Cloud SQL is supported), configure `DATABASE_URL` as a Secret Manager secret, and grant the Cloud Run service account Cloud SQL Client and Secret Manager Secret Accessor roles. For Cloud SQL over its Unix socket, use a URL like:

```text
postgresql://DB_USER:URL_ENCODED_PASSWORD@/DB_NAME?host=%2Fcloudsql%2FPROJECT_ID%3AINSTANCE_REGION%3AINSTANCE_NAME
```

The existing local database and its data are not uploaded or changed by these deployment files. Back up and explicitly migrate/import the existing data to the production database before directing users to the hosted app. Do not run `seed_demo` in production; it resets the seeded administrator password.

Store a unique production `SECRET_KEY` and the production `DATABASE_URL` in Secret Manager. Set `CSRF_TRUSTED_ORIGINS` to the deployed Firebase Hosting origin(s), including any custom domain, and set `ALLOWED_HOSTS` to the app's `run.app` and Hosting/custom hostnames. Set `SECURE_COOKIES=true` in Cloud Run; local development keeps the existing non-secure-cookie behavior by default.

### Deploy

Install and authenticate the Google Cloud SDK and Firebase CLI, enable Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud SQL Admin (when using Cloud SQL), and Firebase Hosting APIs, then select project `lexie-13e6b`. Build/deploy the Django container from the `backend` directory:

```powershell
gcloud run deploy lexie-django `
  --source backend `
  --project lexie-13e6b `
  --region asia-southeast1 `
  --memory 1Gi `
  --allow-unauthenticated `
  --add-cloudsql-instances PROJECT_ID:INSTANCE_REGION:INSTANCE_NAME `
  --set-secrets SECRET_KEY=django-secret:latest,DATABASE_URL=database-url:latest
```

For a non-Cloud-SQL PostgreSQL provider, omit `--add-cloudsql-instances`. Before using the service, apply Django migrations to the production database with a one-off Cloud Run Job (using the same image, secrets, region, and Cloud SQL connection) or through an authenticated Cloud SQL connection. Do not make schema changes by running migrations automatically at every web-server startup.

Then deploy the Hosting rewrite from the repository root:

```powershell
firebase deploy --only hosting --project lexie-13e6b
```

The `firebase.json` rewrite sends all Hosting paths to the Cloud Run service. If the Firebase Hosting site has a different site ID, configure that site in `firebase.json` before deployment. Verify the configured `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` include the actual Hosting domain before testing login.
