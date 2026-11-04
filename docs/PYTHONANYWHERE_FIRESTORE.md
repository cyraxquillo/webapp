# PythonAnywhere Firestore Setup

These instructions configure Firebase Admin for the Django backend on PythonAnywhere. Replace `<username>` and `<virtualenv>` with your account name and the virtualenv path selected in the PythonAnywhere Web tab. Never place the service-account JSON in the repository or under a directory mapped as static files.

Keep `USE_FIRESTORE=false`; Phase A mirrors SQL data to Firestore but does not switch application reads. Keep `FIRESTORE_SYNC_ENABLED=false` until the outbox migration has been applied and the Firestore connection has passed its health check. The current views remain SQL-backed.

## 1. Upload the service-account key privately

Create a private directory from a PythonAnywhere Bash console:

```bash
mkdir -p /home/<username>/private
chmod 700 /home/<username>/private
```

In the PythonAnywhere **Files** tab, upload the service-account JSON directly to:

```text
/home/<username>/private/lexie-firebase.json
```

Do not upload it inside `/home/<username>/webapp`, the Django `backend/` directory, or either static/media directory. Restrict access after upload:

```bash
chmod 600 /home/<username>/private/lexie-firebase.json
ls -ld /home/<username>/private
ls -l /home/<username>/private/lexie-firebase.json
```

The directory should be accessible only to your account, and the key file should be readable only by your account. Do not run `cat` on the key, paste its contents into a ticket, or commit it.

## 2. Install Firebase Admin in the web app virtualenv

Use the same virtualenv selected in the PythonAnywhere **Web** tab. From a Bash console:

```bash
cd /home/<username>/webapp/backend
/home/<username>/.virtualenvs/<virtualenv>/bin/python -m pip install -r requirements.txt
/home/<username>/.virtualenvs/<virtualenv>/bin/python -c "import firebase_admin; print(firebase_admin.__version__)"
```

If your virtualenv is not under `.virtualenvs`, replace that path with its actual path. The Web tab must point to this same environment.

## 3. Set environment variables

In the PythonAnywhere **Web** tab, open the WSGI configuration file. Set the values before importing the Django WSGI application (before `from wsgi import application` or `get_wsgi_application()`):

```python
import os

os.environ["FIREBASE_PROJECT_ID"] = "lexie-13e6b"
os.environ["FIREBASE_CREDENTIALS_PATH"] = "/home/<username>/private/lexie-firebase.json"
os.environ.setdefault("USE_FIRESTORE", "false")
os.environ.setdefault("FIRESTORE_SYNC_ENABLED", "false")
```

Replace `<username>`. These settings are project ID, file path, and feature flag only; never paste the service-account JSON or private key into the WSGI file. If your PythonAnywhere account provides per-web-app environment variables, you may set the same names there instead. Set them before Django is loaded.

Keep `USE_FIRESTORE=false`; setting it to `true` is not part of Phase A. After deploying the Phase A code, applying its SQL migrations, and passing `check_firebase`, set `FIRESTORE_SYNC_ENABLED=true` to enable the one-way SQL-to-Firestore mirror. This does not change views or reads.

## 4. Test from a Bash console

WSGI environment variables are not automatically present in a separate Bash console. Export them for the test command, activate the web app's virtualenv, and run the health check:

```bash
cd /home/<username>/webapp/backend
source /home/<username>/.virtualenvs/<virtualenv>/bin/activate
export FIREBASE_PROJECT_ID="lexie-13e6b"
export FIREBASE_CREDENTIALS_PATH="/home/<username>/private/lexie-firebase.json"
python manage.py check_firebase
```

Success prints:

```text
PASS: Firestore write/read/delete succeeded.
```

The command creates a random document in the `healthcheck` collection, reads it, and deletes it. On failure, it prints `FAIL:` with the exception type and message. It retries cleanup if the write/read/delete operation fails; a reported cleanup error means a temporary health-check document may remain and should be removed from the Firestore console after inspecting the collection.

Common failures:

| Error | Likely cause and action |
| --- | --- |
| `FIREBASE_CREDENTIALS_PATH must point to a service account JSON file` | The variable is unset or empty in the process running the command. Export it in Bash or set it before Django loads in the WSGI configuration. |
| `Firebase credentials must be stored outside the repository` | The path resolves inside the checked-out project. Move the key to `/home/<username>/private/` and update the path. |
| `Firebase credentials file does not exist: ...` | The path or filename is wrong, or the key was not uploaded. Check the path with `test -f "$FIREBASE_CREDENTIALS_PATH"` and its permissions with `ls -l`; do not print the file. |
| JSON parsing, `ValueError`, or `DefaultCredentialsError` | The file is not a valid service-account JSON key, is incomplete, or is unreadable. Re-upload a newly created key and check file permissions without displaying its contents. |
| `PermissionDenied` / HTTP 403 | Confirm the service account belongs to project `lexie-13e6b`, Firestore is enabled, and the account has Firestore data access sufficient to create, read, and delete documents (for example, `roles/datastore.user`). |
| `NotFound` / HTTP 404 | Confirm the project ID and Firestore database exist and that the Firestore API is enabled for the project. |
| `Unavailable`, `DeadlineExceeded`, DNS, socket, or timeout errors | The PythonAnywhere plan or network may block outbound access, or a transient Google API/network issue occurred. Check outbound networking below, then retry once connectivity is available. |
| Failure mentioning cleanup | The check could not delete its test document. Inspect `healthcheck` in the Firestore console and remove only the random test document left by this check. |

After a successful check, click **Reload** in the PythonAnywhere Web tab. Review the web app error log if the application does not start.

## 5. Run and retry SQL-to-Firestore sync

Phase A records an outbox entry in the same SQL transaction as each Employee/Evaluation save or delete. After that transaction commits, Django makes one best-effort sync attempt. A failure is logged and retained in the SQL outbox; it does not roll back or block the existing SQL write. The scheduled command retries pending entries with backoff.

Run a retry from a PythonAnywhere Bash console:

```bash
cd /home/<username>/webapp/backend
source /home/<username>/.virtualenvs/<virtualenv>/bin/activate
python manage.py sync_firestore_outbox --batch-size 100
```

In the PythonAnywhere **Tasks** tab, a scheduled command can use:

```bash
cd /home/<username>/webapp/backend && /home/<username>/.virtualenvs/<virtualenv>/bin/python manage.py sync_firestore_outbox --batch-size 500
```

If your plan permits only one daily scheduled task, use it for `sync_firestore_outbox`. The on-commit attempt still runs after each successful SQL save/delete; a failed immediate attempt will be retried on the next daily run, so Firestore may lag by up to a day. The sync command also processes the queued dashboard-summary rebuild. Run `rebuild_dashboard_summaries` manually after the initial migration or whenever you need to refresh the cache immediately.

To inspect differences without modifying Firestore:

```bash
python manage.py reconcile_firestore
```

Only use `--apply` after reviewing the report. It repairs Firestore from SQL and removes Firestore-only Employee/Evaluation documents; it never deletes SQL records.

## 6. Firestore client security rules

The browser must not read or write these Firestore collections. The Django Admin SDK authenticates as a service account and bypasses Firestore Security Rules; its access is controlled by Google Cloud IAM instead. Recommended rules:

```text
rules_version = '2';
service cloud.firestore {
	match /databases/{database}/documents {
		match /{document=**} {
			allow read, write: if false;
		}
	}
}
```

To inspect the deployed rules, open **Firebase Console → project `lexie-13e6b` → Build → Firestore Database → Rules**. Confirm the published rules deny all client reads and writes. Use the Rules Playground or a client SDK test to verify denial; `check_firebase` uses the Admin SDK and is not a test of client rules.

## 7. If outbound Firestore access is blocked

Firestore Admin traffic must reach Google's APIs over outbound HTTPS (and may use gRPC transport). PythonAnywhere free plans can restrict outbound internet access; the exact restrictions depend on the current account and plan.

If the check reports DNS, socket, timeout, or `Unavailable` errors:

1. Check PythonAnywhere's current outbound-network policy for your plan and whether Google's Firestore API endpoints are permitted.
2. Ask PythonAnywhere support whether outbound Firestore/gRPC traffic is supported for your account, or move to a plan that permits the required connections.
3. Keep `USE_FIRESTORE=false` and continue using SQL until `check_firebase` passes. Do not work around the restriction by exposing a service-account key or sending it through a browser/client.
4. Once outbound access is available, rerun the Bash-console command above. A passing check confirms the server can authenticate and perform Firestore document operations.

Firestore security rules should deny direct client reads and writes if the browser never accesses Firestore. The Django backend uses the Admin SDK, which authenticates with IAM and bypasses Firestore security rules; control backend access with service-account IAM permissions instead.
