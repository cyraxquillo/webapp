import math

from django.core.exceptions import ValidationError
from django.utils import timezone

from .access import get_managers_of
from .models import AttendanceRecord, Notification, OfficeLocation, User


MAX_GPS_ACCURACY_METERS = 100
EARTH_RADIUS_METERS = 6_371_000


class OutsidePremisesBlocked(ValidationError):
    def __init__(self, record):
        self.record = record
        super().__init__("Time In is blocked outside company premises.")


def parse_coordinates(data):
    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
        accuracy = float(data.get("accuracy"))
    except (TypeError, ValueError):
        raise ValidationError(
            "Location is required. Allow location access and try again."
        ) from None
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not math.isfinite(accuracy)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or accuracy < 0
    ):
        raise ValidationError("The location reading is invalid. Try again.")
    if accuracy > MAX_GPS_ACCURACY_METERS:
        raise ValidationError(
            f"GPS accuracy is too low (±{accuracy:.0f} m). Move to an open area and try again."
        )
    return latitude, longitude, accuracy


def haversine_distance_meters(latitude, longitude, office_latitude, office_longitude):
    latitude1, latitude2 = math.radians(latitude), math.radians(office_latitude)
    latitude_delta = latitude2 - latitude1
    longitude_delta = math.radians(office_longitude - longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude1)
        * math.cos(latitude2)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1, haversine)))


def measure_office_distance(latitude, longitude):
    offices = list(OfficeLocation.objects.filter(is_active=True))
    if not offices:
        return None, None, False
    measurements = [
        (
            haversine_distance_meters(
                latitude,
                longitude,
                office.latitude,
                office.longitude,
            ),
            office,
        )
        for office in offices
    ]
    closest_distance, closest_office = min(measurements, key=lambda item: item[0])
    is_inside = any(
        distance <= office.allowed_radius_meters
        for distance, office in measurements
    )
    return closest_distance, closest_office, not is_inside


def _outside_notification_message(user, record, *, blocked=False):
    employee = record.employee or getattr(user, "employee", None)
    if employee and employee.team_leader_id:
        team = employee.team_leader.name
    elif user.role == User.Role.team_leader:
        team = f"{user.name}'s team"
    elif user.role in (User.Role.management, User.Role.hr_manager):
        team = "Management"
    elif user.role == User.Role.admin:
        team = "Administration"
    else:
        team = "Not assigned"
    event = (
        "attempted to time in outside company premises; the attempt was blocked"
        if blocked
        else "timed in outside company premises"
    )
    message = (
        f"{user.name} ({user.get_role_display()}) {event}.\n"
        f"Team: {team}\n"
        f"Time: {timezone.localtime(record.clock_in_at or record.outside_attempt_at).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"Coordinates: {record.latitude:.6f}, {record.longitude:.6f}\n"
        f"Distance from nearest office: {record.distance_from_office:.0f} m"
    )
    if record.outside_premises_reason:
        message += f"\nReason: {record.outside_premises_reason}"
    return message


def create_outside_attendance_notifications(
    user,
    record,
    *,
    attempt_at=None,
    blocked=False,
):
    if user.role == User.Role.admin:
        return
    attempt_at = attempt_at or timezone.now()
    title = (
        f"Blocked outside-premises time-in: {user.name}"
        if blocked
        else f"Outside-premises time-in: {user.name}"
    )
    if blocked:
        record.outside_attempt_at = attempt_at
    message = _outside_notification_message(user, record, blocked=blocked)
    for recipient in get_managers_of(user):
        if blocked:
            dedupe_key = (
                f"outside-attendance-attempt-{user.pk}-"
                f"{int(attempt_at.timestamp() * 1_000_000)}-{recipient.pk}"
            )
        else:
            dedupe_key = f"outside-attendance-{record.pk}-{recipient.pk}"
        Notification.objects.create(
            recipient=recipient,
            category=Notification.Category.automatic,
            title=title,
            message=message,
            dedupe_key=dedupe_key,
        )


def apply_attendance_location(record, user, data, *, require_reason):
    latitude, longitude, accuracy = parse_coordinates(data)
    distance, office, is_outside = measure_office_distance(latitude, longitude)
    reason = str(data.get("outside_reason", "")).strip()
    record.latitude = latitude
    record.longitude = longitude
    record.accuracy = accuracy
    record.distance_from_office = distance
    record.is_outside_premises = is_outside if office else None
    if require_reason:
        record.outside_premises_reason = reason if is_outside else ""
    elif is_outside and reason:
        record.outside_premises_reason = reason
    if require_reason and is_outside and office:
        if office.outside_time_in_policy == OfficeLocation.OutsideTimeInPolicy.block:
            raise OutsidePremisesBlocked(record)
        if (
            office.outside_time_in_policy
            == OfficeLocation.OutsideTimeInPolicy.require_reason
            and not reason
        ):
            raise ValidationError(
                "You are outside company premises. Enter a reason to continue."
            )
    return bool(is_outside and office)
