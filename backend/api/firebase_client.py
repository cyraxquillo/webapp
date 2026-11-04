import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


def get_firebase_app():
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    credentials_path_value = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if not credentials_path_value:
        raise RuntimeError("FIREBASE_CREDENTIALS_PATH must point to a service account JSON file")

    credentials_path = Path(credentials_path_value).expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[2]
    try:
        credentials_path.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise RuntimeError("Firebase credentials must be stored outside the repository")

    if not credentials_path.is_file():
        raise FileNotFoundError(
            f"Firebase credentials file does not exist: {credentials_path}"
        )

    certificate = credentials.Certificate(str(credentials_path))
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    options = {"projectId": project_id} if project_id else None
    try:
        return firebase_admin.initialize_app(certificate, options)
    except ValueError:
        return firebase_admin.get_app()


def get_firestore_client():
    return firestore.client(get_firebase_app())