import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .automation import classify_evaluation
from .ml.predict import UnknownDepartmentError
from .firestore_sync import enqueue_record_sync
from .models import Employee, Evaluation


logger = logging.getLogger(__name__)


@receiver(post_save, sender=Employee)
def queue_employee_save(sender, instance, **kwargs):
    enqueue_record_sync(
        "employee",
        instance.pk,
        "upsert",
    )


@receiver(post_delete, sender=Employee)
def queue_employee_delete(sender, instance, **kwargs):
    enqueue_record_sync(
        "employee",
        instance.pk,
        "delete",
    )


@receiver(post_save, sender=Evaluation)
def queue_evaluation_save(sender, instance, **kwargs):
    enqueue_record_sync(
        "evaluation",
        instance.pk,
        "upsert",
    )


@receiver(post_delete, sender=Evaluation)
def queue_evaluation_delete(sender, instance, **kwargs):
    enqueue_record_sync(
        "evaluation",
        instance.pk,
        "delete",
    )


@receiver(post_save, sender=Evaluation)
def classify_saved_evaluation(sender, instance, **kwargs):
    try:
        classify_evaluation(instance)
    except UnknownDepartmentError:
        logger.warning(
            "Skipping automatic classification for evaluation %s because its department "
            "is not supported by the trained model.",
            instance.pk,
        )