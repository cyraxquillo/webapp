import os
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import Client, Department, Employee, Evaluation, User


DEMO_EMPLOYEES = [
    ("Maya Santos", "maya.santos@lexie.ph", "EMP-1042", "Recruitment", "Senior Staffing Consultant", 92),
    ("Noah Williams", "noah.williams@lexie.ph", "EMP-1048", "Recruitment", "Talent Acquisition Specialist", 86),
    ("Arielle Cruz", "arielle.cruz@lexie.ph", "EMP-1037", "Client Services", "Client Success Lead", 78),
    ("Daniel Park", "daniel.park@lexie.ph", "EMP-1054", "Finance", "Business Analyst", 71),
    ("Leah Thompson", "leah.thompson@lexie.ph", "EMP-1029", "Recruitment", "Recruitment Coordinator", 64),
    ("Rafael Garcia", "rafael.garcia@lexie.ph", "EMP-1061", "HR & Admin", "Operations Associate", 83),
]


class Command(BaseCommand):
    help = "Create deterministic local demo users, departments, employees, and evaluations."

    @transaction.atomic
    def handle(self, *args, **options):
        demo_passwords = {
            "admin": os.getenv("DEMO_ADMIN_PASSWORD", ""),
            "client": os.getenv("DEMO_CLIENT_PASSWORD", ""),
            "staff": os.getenv("DEMO_STAFF_PASSWORD", ""),
        }
        missing_passwords = [
            f"DEMO_{role.upper()}_PASSWORD"
            for role, password in demo_passwords.items()
            if not password.strip()
        ]
        if missing_passwords:
            raise CommandError(
                "Set the following environment variables before seeding: "
                + ", ".join(missing_passwords)
            )

        admin, _ = User.objects.get_or_create(
            email="admin@lexie.ph",
            defaults={"name": "Amelia Reyes", "role": User.Role.admin},
        )
        admin.name = admin.name or "Amelia Reyes"
        admin.role = User.Role.admin
        admin.is_active = True
        admin.is_staff = True
        admin.is_superuser = True
        admin.set_password(demo_passwords["admin"])
        admin.must_change_password = True
        admin.save()
        today = date.today()

        client_user, _ = User.objects.get_or_create(
            email="client@lexie.ph",
            defaults={"name": "Joanne Lim", "role": User.Role.client},
        )
        client_user.name = "Joanne Lim"
        client_user.role = User.Role.client
        client_user.is_active = True
        client_user.set_password(demo_passwords["client"])
        client_user.must_change_password = True
        client_user.save()
        Client.objects.get_or_create(
            user=client_user,
            defaults={
                "client_code": "CL-018",
                "company_name": "Northstar Retail Group",
                "services_used": "Executive Search",
                "account_status": "active",
                "next_renewal_date": date(today.year, 11, 24),
            },
        )

        for name in ("Recruitment", "Client Services", "Finance", "HR & Admin"):
            Department.objects.get_or_create(name=name)

        for index, (name, email, code, department_name, position, score) in enumerate(DEMO_EMPLOYEES):
            staff, _ = User.objects.get_or_create(
                email=email,
                defaults={"name": name, "role": User.Role.staff},
            )
            staff.name = name
            staff.role = User.Role.staff
            staff.is_active = True
            staff.set_password(demo_passwords["staff"])
            staff.must_change_password = True
            staff.save()

            department = Department.objects.get(name=department_name)
            employee, _ = Employee.objects.get_or_create(
                employee_code=code,
                defaults={
                    "user": staff,
                    "name": name,
                    "department": department,
                    "position": position,
                    "date_hired": today - timedelta(days=(index + 1) * 365),
                    "attendance_rate": max(65, min(99, score + 4)),
                    "tenure_months": (index + 2) * 12,
                    "training_hours": 24 + index * 4,
                },
            )
            employee.department = department
            employee.save(update_fields=("department",))
            if not employee.evaluations.exists():
                scores = [max(0, min(100, score + adjustment)) for adjustment in (2, 1, 3, -1, 0)]
                Evaluation.objects.create(
                    employee=employee,
                    evaluator=admin,
                    date=today - timedelta(days=index * 9),
                    productivity_score=scores[0],
                    attendance_score=scores[1],
                    quality_score=scores[2],
                    teamwork_score=scores[3],
                    communication_score=scores[4],
                    comments="Demo evaluation; replace with an actual review.",
                )

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
