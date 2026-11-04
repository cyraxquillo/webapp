import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .automation import classify_evaluation
from .ml.predict import UnknownDepartmentError
from .models import Evaluation


logger = logging.getLogger(__name__)


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