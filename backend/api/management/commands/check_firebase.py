from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from firebase_admin import firestore

from api.firebase_client import get_firestore_client


class Command(BaseCommand):
    help = "Verify Firestore access by writing, reading, and deleting a health-check document."

    def handle(self, *args, **options):
        document = None
        try:
            client = get_firestore_client()
            document = client.collection("healthcheck").document(uuid4().hex)
            document.set({"status": "ok", "checked_at": firestore.SERVER_TIMESTAMP})
            snapshot = document.get()
            if not snapshot.exists:
                raise RuntimeError("The health-check document was not returned after writing it")
            document.delete()
        except Exception as error:
            cleanup_error = None
            if document is not None:
                try:
                    document.delete()
                except Exception as cleanup_exception:
                    cleanup_error = cleanup_exception

            detail = f"{type(error).__name__}: {error}"
            if cleanup_error is not None:
                detail += (
                    f"; cleanup also failed with "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise CommandError(f"FAIL: {detail}") from error

        self.stdout.write(self.style.SUCCESS("PASS: Firestore write/read/delete succeeded."))