import logging
from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .access import MANAGEMENT_ROLES
from .models import AutomationSettings, Employee, Evaluation, Notification, Prediction, User


logger = logging.getLogger(__name__)


def create_notification(recipient, title, message, category, sender=None, dedupe_key=None):
    if dedupe_key:
        notification, created = Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                "recipient": recipient,
                "sender": sender,
                "category": category,
                "title": title,
                "message": message,
            },
        )
    else:
        notification = Notification.objects.create(
            recipient=recipient,
            sender=sender,
            category=category,
            title=title,
            message=message,
        )
        created = True

    if created and getattr(settings, "EMAIL_HOST_CONFIGURED", False) and recipient.email:
        try:
            send_mail(
                title,
                message,
                getattr(settings, "DEFAULT_FROM_EMAIL", None),
                [recipient.email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("Unable to email notification %s", notification.pk)
    return notification


def notify_performance_change(evaluation, prediction):
    category_is_low = prediction.classification_tone == "low"
    settings_row = AutomationSettings.objects.first()
    threshold = settings_row.score_drop_threshold if settings_row else 10
    cycle_date = evaluation.cycle_month or date(
        evaluation.date.year, evaluation.date.month, 1
    )
    previous_month = (
        date(cycle_date.year - 1, 12, 1)
        if cycle_date.month == 1
        else date(cycle_date.year, cycle_date.month - 1, 1)
    )
    previous = (
        Evaluation.objects.filter(
            employee=evaluation.employee,
            status=Evaluation.Status.completed,
            date__year=previous_month.year,
            date__month=previous_month.month,
        )
        .exclude(pk=evaluation.pk)
        .order_by("-date", "-id")
        .first()
    )
    score_dropped = bool(
        previous
        and previous.overall_score is not None
        and evaluation.overall_score is not None
        and previous.overall_score - evaluation.overall_score > threshold
    )
    if not category_is_low and not score_dropped:
        return

    employee = evaluation.employee
    if category_is_low and score_dropped:
        alert_title = "Performance check-in recommended"
    elif category_is_low:
        alert_title = "Performance support recommended"
    else:
        alert_title = "Performance change to review"

    recipients = []
    if employee.team_leader_id:
        recipients.append(employee.team_leader)
    recipients.extend(
        User.objects.filter(role__in=MANAGEMENT_ROLES, is_active=True).order_by("email")
    )
    for recipient in {user.pk: user for user in recipients}.values():
        create_notification(
            recipient,
            alert_title,
            f"Please check in with {employee.name} and review their latest evaluation. A supportive conversation may help identify useful next steps.",
            Notification.Category.automatic,
            dedupe_key=f"performance-alert-{evaluation.pk}-{recipient.pk}",
        )

    if employee.user_id:
        create_notification(
            employee.user,
            "Your performance check-in",
            "Your latest review is ready. Your manager is here to support you and discuss any help or next steps that would be useful.",
            Notification.Category.automatic,
            dedupe_key=f"employee-performance-alert-{evaluation.pk}-{employee.user_id}",
        )


def classify_evaluation(evaluation):
    if evaluation.status != Evaluation.Status.completed or evaluation.overall_score is None:
        return None

    from .ml.predict import UnknownDepartmentError, predict_performance

    try:
        result = predict_performance(evaluation.employee, evaluation)
    except UnknownDepartmentError as error:
        logger.warning(
            "Skipping classification for evaluation %s: %s",
            evaluation.pk,
            error,
        )
        raise
    except Exception:
        logger.exception("Unable to classify evaluation %s", evaluation.pk)
        return None

    prediction, _ = Prediction.objects.update_or_create(
        evaluation=evaluation,
        defaults={
            "employee": evaluation.employee,
            "created_at": timezone.now(),
            **result,
        },
    )
    notify_performance_change(evaluation, prediction)
    return prediction


def month_start(value=None):
    value = value or timezone.localdate()
    return date(value.year, value.month, 1)


@transaction.atomic
def create_monthly_evaluations(month=None):
    cycle_month = month_start(month)
    settings_row = AutomationSettings.objects.first()
    auto_mode = bool(
        settings_row
        and settings_row.scoring_mode == AutomationSettings.ScoringMode.automatic
    )
    fallback_assignee = (
        User.objects.filter(role__in=MANAGEMENT_ROLES, is_active=True)
        .order_by("role", "name")
        .first()
    )
    if fallback_assignee is None and Employee.objects.filter(status="active").exists():
        raise ValueError("Create an active Management or Admin account before generating evaluations.")

    created = []
    employees = Employee.objects.filter(status="active").select_related(
        "department", "team_leader"
    )
    for employee in employees.iterator():
        if employee.date_hired >= cycle_month:
            continue
        assignee = employee.team_leader or fallback_assignee
        latest = (
            Evaluation.objects.filter(
                employee=employee,
                status=Evaluation.Status.completed,
                date__lt=cycle_month,
            )
            .exclude(productivity_score__isnull=True)
            .order_by("-date", "-id")
            .first()
        )
        submission_count = employee.submissions.filter(
            uploaded_at__year=cycle_month.year,
            uploaded_at__month=cycle_month.month,
        ).count()
        productivity_score = min(submission_count * 20, 100)
        attendance_score = max(
            0,
            min(float(employee.attendance_rate or 0), 100),
        )
        auto_score = round((attendance_score + productivity_score) / 2, 2)
        auto_data = {
            "attendance_rate": attendance_score,
            "submission_count": submission_count,
            "productivity_score": productivity_score,
        }
        evaluation, was_created = Evaluation.objects.get_or_create(
            employee=employee,
            cycle_month=cycle_month,
            defaults={
                "date": cycle_month,
                "evaluator": assignee,
                "assigned_to": assignee,
                "attendance_score": attendance_score,
                "productivity_score": productivity_score,
                "quality_score": auto_score if auto_mode else None,
                "teamwork_score": auto_score if auto_mode else None,
                "communication_score": auto_score if auto_mode else None,
                "status": Evaluation.Status.completed if auto_mode else Evaluation.Status.pending,
                "auto_score": auto_score if auto_mode else None,
                "auto_data": auto_data,
            },
        )
        if was_created:
            created.append(evaluation)
    return created