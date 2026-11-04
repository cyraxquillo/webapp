import datetime

from django.db import migrations


def ensure_default_automation_settings(apps, schema_editor):
    AutomationSettings = apps.get_model("api", "AutomationSettings")
    if not AutomationSettings.objects.filter(pk=1).exists():
        AutomationSettings.objects.create(
            pk=1,
            scoring_mode="team_leader",
            score_drop_threshold=10,
            attendance_cutoff_time=datetime.time(8, 0),
            attendance_grace_minutes=0,
            attendance_on_time_points=1.0,
            attendance_late_points=0.5,
            attendance_excused_points=1.0,
            attendance_absent_points=0.0,
            attendance_working_days="1,2,3,4,5",
            attendance_holidays="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0010_attendancerecord_clock_in_at_and_more"),
    ]

    operations = [
        migrations.RunPython(
            ensure_default_automation_settings,
            migrations.RunPython.noop,
        ),
    ]
