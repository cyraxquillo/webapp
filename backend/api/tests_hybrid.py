from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from api.hybrid import (
    calculate_attendance_rate,
    calculate_hybrid_score,
    calculate_work_score,
)
from api.models import (
    AttendanceRecord,
    AutomationSettings,
    Department,
    Employee,
    Evaluation,
    Submission,
)

User = get_user_model()


class HybridScoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Hybrid Test")
        cls.employee = Employee.objects.create(
            employee_code="HYBRID-1",
            name="Hybrid Employee",
            department=cls.department,
            position="Analyst",
            date_hired=date(2026, 1, 1),
        )

    def test_attendance_points_follow_requested_status_values(self):
        records = [
            AttendanceRecord.objects.create(
                employee=self.employee,
                work_date=date(2026, 2, 1),
                status=AttendanceRecord.Status.present,
            ),
            AttendanceRecord.objects.create(
                employee=self.employee,
                work_date=date(2026, 2, 2),
                status=AttendanceRecord.Status.late,
            ),
            AttendanceRecord.objects.create(
                employee=self.employee,
                work_date=date(2026, 2, 3),
                status=AttendanceRecord.Status.excused,
            ),
            AttendanceRecord.objects.create(
                employee=self.employee,
                work_date=date(2026, 2, 4),
                status=AttendanceRecord.Status.absent,
            ),
        ]

        result = calculate_attendance_rate(records)

        self.assertEqual(result["earned_points"], 1.75)
        self.assertEqual(result["working_days"], 4)
        self.assertEqual(result["attendance_rate"], 43.75)

    def test_monthly_attendance_rate_uses_configured_working_days_and_holidays(self):
        settings = AutomationSettings.objects.get_or_create_default()
        settings.attendance_working_days = "1,2,3,4,5"
        settings.attendance_holidays = "2026-02-02"
        settings.save()
        record = AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 2, 3),
            status=AttendanceRecord.Status.present,
            time_in_status="on_time",
            time_out_status=AttendanceRecord.TimeOutStatus.on_time,
            clock_in_at=timezone.make_aware(datetime(2026, 2, 3, 8, 0)),
            clock_out_at=timezone.make_aware(datetime(2026, 2, 3, 19, 0)),
        )
        from api.hybrid import working_days_in_month

        working_days = working_days_in_month(settings, date(2026, 2, 1), self.employee.date_hired)
        result = calculate_attendance_rate([record], settings, working_days)
        self.assertEqual(working_days, 19)
        self.assertEqual(result["earned_points"], 1.0)
        self.assertEqual(result["attendance_rate"], round(100 / 19, 2))

    def test_work_score_uses_only_existing_submissions_and_caps_at_100(self):
        submissions = [
            Submission.objects.create(
                employee=self.employee,
                title="Complete",
                file="complete.pdf",
                status=Submission.Status.reviewed,
                is_late=False,
            ),
            Submission.objects.create(
                employee=self.employee,
                title="Late",
                file="late.pdf",
                status=Submission.Status.reviewed,
                is_late=True,
            ),
            Submission.objects.create(
                employee=self.employee,
                title="Rejected",
                file="rejected.pdf",
                status=Submission.Status.pending,
                is_late=False,
            ),
        ]

        result = calculate_work_score(submissions, monthly_target=2)

        self.assertEqual(result["earned_points"], 1.5)
        self.assertEqual(result["target_points"], 2)
        self.assertEqual(result["work_score"], 75)

    def test_hybrid_score_uses_manual_ratings_and_proportional_fallback(self):
        result = calculate_hybrid_score(
            attendance_rate=80,
            work_score=70,
            team_leader_rating=90,
            management_rating=None,
            weights={"attendance": 20, "work": 30, "team_leader": 30, "management": 20},
        )

        self.assertEqual(result["overall_score"], 80.0)
        self.assertEqual(result["management_rating"], None)
        self.assertEqual(result["management_weight"], 20)
        self.assertEqual(result["effective_weights"], {
            "attendance": 20,
            "work": 30,
            "team_leader": 30,
            "management": 20,
        })

    def test_hybrid_score_does_not_auto_fill_manual_ratings(self):
        evaluation = Evaluation.objects.create(
            employee=self.employee,
            evaluator=User.objects.create_user(
                email="manager@lexie.ph",
                password="test",
                name="Manager",
                role=User.Role.management,
            ),
            date=date(2026, 2, 1),
            cycle_month=date(2026, 2, 1),
            status=Evaluation.Status.draft,
            attendance_score=80,
            productivity_score=70,
        )

        self.assertIsNone(evaluation.team_leader_rating)
        self.assertIsNone(evaluation.management_rating)
        self.assertEqual(evaluation.phase, Evaluation.Phase.draft)
