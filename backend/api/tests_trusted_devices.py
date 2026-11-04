import hashlib
from datetime import date
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Department, Employee, Notification, TrustedDevice, User


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    DEVICE_VERIFICATION_ENABLED=True,
)
class TrustedDeviceTests(TestCase):
    password = "StrongPassword123!"
    chrome_windows = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
    )

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="device-admin@example.test",
            password=self.password,
            name="Device Admin",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        self.user = User.objects.create_user(
            email="device-user@example.test",
            password=self.password,
            name="Device User",
            role=User.Role.staff,
        )

    def create_user(self, role, email):
        return User.objects.create_user(
            email=email,
            password=self.password,
            name=email.split("@", 1)[0].replace("-", " ").title(),
            role=role,
        )

    def create_device(self, user, label=None):
        label = label or f"{user.name} device"
        token = hashlib.sha256(f"{user.pk}-{label}".encode("utf-8")).hexdigest()
        return TrustedDevice.objects.create(
            user=user,
            token_hash=token,
            label=label,
            status=TrustedDevice.Status.pending,
        )

    def assign_to_team(self, user, team_leader, department_name):
        department = Department.objects.create(name=department_name)
        return Employee.objects.create(
            user=user,
            team_leader=team_leader,
            name=user.name,
            department=department,
            position="Staff",
            date_hired=date(2020, 1, 1),
        )

    def login(self, client, user=None, user_agent=None):
        user = user or self.user
        return client.post(
            reverse("web-login"),
            {
                "email": user.email,
                "password": self.password,
            },
            HTTP_USER_AGENT=user_agent or self.chrome_windows,
        )

    def device_cookie_name(self, user):
        return f"lexie_device_{user.pk}"

    def enroll_first_device(self, client=None):
        client = client or self.client
        response = self.login(client)
        self.assertEqual(response.status_code, 302)
        return TrustedDevice.objects.get(user=self.user)

    def test_first_device_is_auto_approved_and_cookie_is_set(self):
        response = self.login(self.client)

        device = TrustedDevice.objects.get(user=self.user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(device.status, TrustedDevice.Status.approved)
        self.assertEqual(device.approved_by, self.user)
        self.assertIsNotNone(device.last_used_at)
        cookie = response.cookies[self.device_cookie_name(self.user)]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertFalse(cookie["secure"])
        self.assertNotEqual(cookie.value, device.token_hash)
        self.assertEqual(
            hashlib.sha256(cookie.value.encode("utf-8")).hexdigest(),
            device.token_hash,
        )

    def test_device_cookie_is_secure_over_https(self):
        response = self.client.post(
            reverse("web-login"),
            {"email": self.user.email, "password": self.password},
            HTTP_USER_AGENT=self.chrome_windows,
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.cookies[self.device_cookie_name(self.user)]["secure"]
        )

    def test_known_device_signs_in_without_creating_a_record(self):
        enrolling_client = self.client_class()
        device = self.enroll_first_device(enrolling_client)
        cookie_name = self.device_cookie_name(self.user)
        raw_token = enrolling_client.cookies[cookie_name].value
        returning_client = self.client_class()
        returning_client.cookies[cookie_name] = raw_token

        response = self.login(returning_client)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 1)
        device.refresh_from_db()
        self.assertIsNotNone(device.last_used_at)

    def test_new_device_is_blocked_and_notifies_user_and_active_admin(self):
        self.enroll_first_device()
        new_client = self.client_class()

        response = self.login(
            new_client,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0",
        )

        pending = TrustedDevice.objects.get(user=self.user, status=TrustedDevice.Status.pending)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "New device detected. Waiting for approval from your team leader, management, or admin.",
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertNotIn("_auth_user_id", response.wsgi_request.session)
        self.assertEqual(
            Notification.objects.filter(
                recipient__in=(self.user, self.admin),
                title="New device detected",
            ).count(),
            2,
        )
        self.assertNotIn(
            new_client.cookies[self.device_cookie_name(self.user)].value,
            pending.token_hash,
        )

    def test_retrying_pending_browser_does_not_duplicate_device_or_notification(self):
        self.enroll_first_device()
        pending_client = self.client_class()

        self.login(pending_client, user_agent="Firefox/130.0")
        self.login(pending_client, user_agent="Firefox/130.0")

        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 2)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.admin,
                title="New device detected",
            ).count(),
            1,
        )

    def test_missing_cookie_reuses_same_browser_pending_device(self):
        self.enroll_first_device()
        pending_client = self.client_class()
        self.login(pending_client, user_agent="Firefox/130.0")
        pending = TrustedDevice.objects.get(
            user=self.user,
            status=TrustedDevice.Status.pending,
        )
        pending_cookie_name = self.device_cookie_name(self.user)
        pending_cookie_name_before = pending_client.cookies[pending_cookie_name].value
        del pending_client.cookies[pending_cookie_name]

        response = self.login(pending_client, user_agent="Firefox/130.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            TrustedDevice.objects.filter(
                user=self.user,
                status=TrustedDevice.Status.pending,
            ).count(),
            1,
        )
        pending.refresh_from_db()
        self.assertEqual(
            TrustedDevice.objects.get(
                user=self.user,
                status=TrustedDevice.Status.pending,
            ).pk,
            pending.pk,
        )
        self.assertNotEqual(
            pending_cookie_name_before,
            pending_client.cookies[pending_cookie_name].value,
        )

    def test_admin_approval_allows_pending_browser_to_sign_in(self):
        self.enroll_first_device()
        pending_client = self.client_class()
        self.login(pending_client, user_agent="Firefox/130.0")
        pending = TrustedDevice.objects.get(user=self.user, status=TrustedDevice.Status.pending)

        admin_client = self.client_class()
        admin_client.force_login(self.admin)
        approval = admin_client.post(
            reverse("trusted-device-action", args=(pending.pk,)),
            {"action": "approve"},
        )
        self.assertEqual(approval.status_code, 302)

        response = self.login(pending_client, user_agent="Firefox/130.0")
        pending.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(pending.status, TrustedDevice.Status.approved)
        self.assertEqual(pending.approved_by, self.admin)

    def test_same_browser_user_is_approved_after_admin_approves_pending_device(self):
        browser = self.client_class()
        self.assertEqual(self.login(browser).status_code, 302)
        self.assertEqual(browser.post(reverse("web-logout")).status_code, 302)
        del browser.cookies[self.device_cookie_name(self.user)]
        pending_response = self.login(browser, user_agent="Firefox/130.0")
        pending = TrustedDevice.objects.get(
            user=self.user,
            status=TrustedDevice.Status.pending,
        )
        self.assertEqual(pending_response.status_code, 200)

        admin_response = self.login(browser, user=self.admin)
        self.assertEqual(admin_response.status_code, 302)
        approval = browser.post(
            reverse("trusted-device-action", args=(pending.pk,)),
            {"action": "approve"},
        )
        self.assertEqual(approval.status_code, 302)

        browser.post(reverse("web-logout"))
        user_response = self.login(browser, user=self.user, user_agent="Firefox/130.0")

        pending.refresh_from_db()
        self.assertEqual(user_response.status_code, 302)
        self.assertTrue(user_response.wsgi_request.user.is_authenticated)
        self.assertEqual(pending.status, TrustedDevice.Status.approved)
        self.assertEqual(TrustedDevice.objects.filter(user=self.user).count(), 2)

    def test_two_users_keep_their_approved_devices_on_one_browser(self):
        second_user = self.create_user(
            User.Role.employee,
            "second-browser-user@example.test",
        )
        browser = self.client_class()
        self.assertEqual(self.login(browser, user=self.user).status_code, 302)
        self.assertEqual(browser.post(reverse("web-logout")).status_code, 302)
        self.assertEqual(self.login(browser, user=second_user).status_code, 302)
        self.assertEqual(browser.post(reverse("web-logout")).status_code, 302)

        first_cookie = browser.cookies[self.device_cookie_name(self.user)].value
        second_cookie = browser.cookies[self.device_cookie_name(second_user)].value
        self.assertNotEqual(first_cookie, second_cookie)
        self.assertEqual(self.login(browser, user=self.user).status_code, 302)
        self.assertEqual(browser.post(reverse("web-logout")).status_code, 302)
        self.assertEqual(self.login(browser, user=second_user).status_code, 302)

        first_device = TrustedDevice.objects.get(user=self.user)
        second_device = TrustedDevice.objects.get(user=second_user)
        self.assertEqual(first_device.status, TrustedDevice.Status.approved)
        self.assertEqual(second_device.status, TrustedDevice.Status.approved)
        self.assertEqual(
            hashlib.sha256(
                browser.cookies[self.device_cookie_name(self.user)].value.encode(
                    "utf-8"
                )
            ).hexdigest(),
            first_device.token_hash,
        )
        self.assertEqual(
            hashlib.sha256(
                browser.cookies[self.device_cookie_name(second_user)].value.encode(
                    "utf-8"
                )
            ).hexdigest(),
            second_device.token_hash,
        )

    def test_legacy_approved_device_cookie_migrates_to_per_user_cookie_once(self):
        first_response = self.login(self.client)
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(self.client.post(reverse("web-logout")).status_code, 302)
        device = TrustedDevice.objects.get(user=self.user)
        per_user_cookie = self.device_cookie_name(self.user)
        legacy_token = self.client.cookies[per_user_cookie].value
        del self.client.cookies[per_user_cookie]
        self.client.cookies["lexie_device"] = legacy_token

        migrated_response = self.login(self.client)

        self.assertEqual(migrated_response.status_code, 302)
        self.assertTrue(migrated_response.wsgi_request.user.is_authenticated)
        self.assertIn(per_user_cookie, migrated_response.cookies)
        migrated_token = migrated_response.cookies[per_user_cookie].value
        self.assertNotEqual(migrated_token, legacy_token)
        device.refresh_from_db()
        self.assertEqual(
            hashlib.sha256(migrated_token.encode("utf-8")).hexdigest(),
            device.token_hash,
        )

        del self.client.cookies[per_user_cookie]
        self.client.cookies["lexie_device"] = legacy_token
        self.client.post(reverse("web-logout"))
        rejected_legacy_response = self.login(self.client)
        self.assertEqual(rejected_legacy_response.status_code, 200)
        self.assertFalse(rejected_legacy_response.wsgi_request.user.is_authenticated)
        device.refresh_from_db()
        self.assertEqual(device.status, TrustedDevice.Status.approved)
        self.assertNotEqual(
            hashlib.sha256(legacy_token.encode("utf-8")).hexdigest(),
            device.token_hash,
        )

    def test_user_can_approve_pending_device_from_a_trusted_device(self):
        trusted_client = self.client
        self.enroll_first_device(trusted_client)
        pending_client = self.client_class()
        self.login(pending_client, user_agent="Firefox/130.0")
        pending = TrustedDevice.objects.get(user=self.user, status=TrustedDevice.Status.pending)

        response = trusted_client.post(
            reverse("own-device-action", args=(pending.pk,)),
            {"action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TrustedDevice.objects.get(pk=pending.pk).status,
            TrustedDevice.Status.approved,
        )
        self.assertEqual(self.login(pending_client, user_agent="Firefox/130.0").status_code, 302)

    def test_revoked_device_is_blocked_again(self):
        trusted_client = self.client
        device = self.enroll_first_device(trusted_client)
        cookie_name = self.device_cookie_name(self.user)
        raw_token = trusted_client.cookies[cookie_name].value
        admin_client = self.client_class()
        admin_client.force_login(self.admin)
        admin_client.post(
            reverse("trusted-device-action", args=(device.pk,)),
            {"action": "remove"},
        )
        trusted_client.logout()
        trusted_client.cookies[cookie_name] = raw_token

        response = self.login(trusted_client)

        device.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(device.status, TrustedDevice.Status.revoked)
        self.assertEqual(
            TrustedDevice.objects.filter(
                user=self.user,
                status=TrustedDevice.Status.pending,
            ).count(),
            1,
        )

    def test_users_cannot_manage_another_users_device(self):
        device = self.enroll_first_device()
        another_user = User.objects.create_user(
            email="another-device-user@example.test",
            password=self.password,
            name="Another User",
            role=User.Role.employee,
        )
        client = self.client_class()
        client.force_login(another_user)

        response = client.post(
            reverse("own-device-action", args=(device.pk,)),
            {"action": "approve"},
        )

        self.assertEqual(response.status_code, 404)

    def test_user_cannot_remove_the_device_they_are_using(self):
        device = self.enroll_first_device()

        response = self.client.post(
            reverse("own-device-action", args=(device.pk,)),
            {"action": "remove"},
        )

        self.assertEqual(response.status_code, 403)
        device.refresh_from_db()
        self.assertEqual(device.status, TrustedDevice.Status.approved)

    def test_devices_page_is_admin_management_only_and_management_scope_is_limited(self):
        management = User.objects.create_user(
            email="device-management@example.test",
            password=self.password,
            name="Device Management",
            role=User.Role.management,
        )
        hr_user = User.objects.create_user(
            email="device-hr@example.test",
            password=self.password,
            name="Device HR",
            role=User.Role.hr_manager,
        )
        protected_devices = [
            TrustedDevice.objects.create(
                user=protected_user,
                token_hash=hashlib.sha256(protected_user.email.encode("utf-8")).hexdigest(),
                label=f"{protected_user.name} device",
                status=TrustedDevice.Status.pending,
            )
            for protected_user in (self.admin, management, hr_user)
        ]
        admin_client = self.client_class()
        admin_client.force_login(self.admin)
        self.assertEqual(admin_client.get(reverse("trusted-device-list")).status_code, 200)

        management_client = self.client_class()
        management_client.force_login(management)
        listing = management_client.get(reverse("trusted-device-list"))
        self.assertEqual(listing.status_code, 200)
        for protected_device in protected_devices:
            self.assertNotContains(listing, protected_device.label)
            self.assertEqual(
                management_client.post(
                    reverse("trusted-device-action", args=(protected_device.pk,)),
                    {"action": "approve"},
                ).status_code,
                404,
            )

        staff_client = self.client_class()
        staff_client.force_login(self.user)
        self.assertEqual(staff_client.get(reverse("trusted-device-list")).status_code, 403)
        hr_client = self.client_class()
        hr_client.force_login(hr_user)
        self.assertEqual(hr_client.get(reverse("trusted-device-list")).status_code, 403)
        client_user = self.create_user(User.Role.client, "device-client@example.test")
        client = self.client_class()
        client.force_login(client_user)
        self.assertEqual(client.get(reverse("trusted-device-list")).status_code, 403)

    def test_admin_can_approve_devices_for_every_role(self):
        targets = [
            self.create_user(User.Role.admin, "second-admin@example.test"),
            self.create_user(User.Role.management, "target-management@example.test"),
            self.create_user(User.Role.hr_manager, "target-hr@example.test"),
            self.create_user(User.Role.team_leader, "target-leader@example.test"),
            self.create_user(User.Role.staff, "target-staff@example.test"),
            self.create_user(User.Role.employee, "target-employee@example.test"),
            self.create_user(User.Role.client, "target-client@example.test"),
        ]
        admin_client = self.client_class()
        admin_client.force_login(self.admin)

        for target in targets:
            device = self.create_device(target)
            response = admin_client.post(
                reverse("trusted-device-action", args=(device.pk,)),
                {"action": "approve"},
            )
            self.assertEqual(response.status_code, 302)
            device.refresh_from_db()
            self.assertEqual(device.status, TrustedDevice.Status.approved)
            self.assertEqual(device.approved_by, self.admin)

    def test_management_can_approve_only_team_leader_staff_and_client_devices(self):
        management = self.create_user(
            User.Role.management,
            "device-approving-management@example.test",
        )
        permitted_targets = [
            self.create_user(User.Role.team_leader, "managed-leader@example.test"),
            self.create_user(User.Role.staff, "managed-staff@example.test"),
            self.create_user(User.Role.employee, "managed-employee@example.test"),
            self.create_user(User.Role.client, "managed-client@example.test"),
        ]
        forbidden_targets = [
            self.admin,
            management,
            self.create_user(User.Role.hr_manager, "managed-hr@example.test"),
        ]
        permitted_devices = [
            (target, self.create_device(target)) for target in permitted_targets
        ]
        forbidden_devices = [
            (target, self.create_device(target)) for target in forbidden_targets
        ]
        management_client = self.client_class()
        management_client.force_login(management)

        listing = management_client.get(reverse("trusted-device-list"))
        for _, device in permitted_devices:
            self.assertContains(listing, device.label)
        for _, device in forbidden_devices:
            self.assertNotContains(listing, device.label)

        for _, device in permitted_devices:
            response = management_client.post(
                reverse("trusted-device-action", args=(device.pk,)),
                {"action": "approve"},
            )
            self.assertEqual(response.status_code, 302)

        for _, device in forbidden_devices:
            response = management_client.post(
                reverse("trusted-device-action", args=(device.pk,)),
                {"action": "approve"},
            )
            self.assertEqual(response.status_code, 404)
            device.refresh_from_db()
            self.assertEqual(device.status, TrustedDevice.Status.pending)

    def test_team_leader_can_approve_only_staff_on_their_own_team(self):
        team_leader = self.create_user(
            User.Role.team_leader,
            "approving-team-leader@example.test",
        )
        other_team_leader = self.create_user(
            User.Role.team_leader,
            "other-team-leader@example.test",
        )
        own_staff = self.create_user(User.Role.staff, "own-team-staff@example.test")
        other_staff = self.create_user(User.Role.employee, "other-team-staff@example.test")
        self.assign_to_team(own_staff, team_leader, "Approver Team")
        self.assign_to_team(other_staff, other_team_leader, "Other Team")
        targets = [
            own_staff,
            other_staff,
            other_team_leader,
            self.create_user(User.Role.management, "team-test-management@example.test"),
        ]
        devices = [(target, self.create_device(target)) for target in targets]
        leader_client = self.client_class()
        leader_client.force_login(team_leader)
        listing = leader_client.get(reverse("trusted-device-list"))
        self.assertContains(listing, devices[0][1].label)
        for _, device in devices[1:]:
            self.assertNotContains(listing, device.label)

        for target, device in devices:
            response = leader_client.post(
                reverse("trusted-device-action", args=(device.pk,)),
                {"action": "approve"},
            )
            if target == own_staff:
                self.assertEqual(response.status_code, 302)
                device.refresh_from_db()
                self.assertEqual(device.status, TrustedDevice.Status.approved)
            else:
                self.assertEqual(response.status_code, 404)
                device.refresh_from_db()
                self.assertEqual(device.status, TrustedDevice.Status.pending)

    def test_user_cannot_approve_their_own_device_from_devices_page(self):
        device = self.create_device(self.admin, "Admin pending device")
        admin_client = self.client_class()
        admin_client.force_login(self.admin)

        listing = admin_client.get(reverse("trusted-device-list"))
        self.assertContains(listing, "Admin pending device")
        self.assertNotContains(listing, 'name="action" value="approve"')
        response = admin_client.post(
            reverse("trusted-device-action", args=(device.pk,)),
            {"action": "approve"},
        )

        self.assertEqual(response.status_code, 403)
        device.refresh_from_db()
        self.assertEqual(device.status, TrustedDevice.Status.pending)

    def test_admin_new_device_is_approved_and_notifies_other_active_admins(self):
        another_admin = self.create_user(
            User.Role.admin,
            "another-active-admin@example.test",
        )
        first_client = self.client_class()
        first_login = self.login(first_client, user=self.admin)
        self.assertEqual(first_login.status_code, 302)
        first_device = TrustedDevice.objects.get(user=self.admin)
        self.assertEqual(first_device.status, TrustedDevice.Status.approved)
        self.assertEqual(first_device.approved_by, self.admin)
        self.assertTrue(
            Notification.objects.filter(
                recipient=another_admin,
                title="Admin signed in from a new device",
            ).exists()
        )

        new_client = self.client_class()
        response = self.login(
            new_client,
            user=self.admin,
            user_agent="Firefox/130.0",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        new_device = TrustedDevice.objects.exclude(pk=first_device.pk).get(user=self.admin)
        self.assertEqual(new_device.status, TrustedDevice.Status.approved)
        self.assertEqual(new_device.approved_by, self.admin)
        self.assertEqual(
            Notification.objects.filter(
                recipient=another_admin,
                title="Admin signed in from a new device",
            ).count(),
            2,
        )
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.admin,
                title="Admin signed in from a new device",
            ).exists()
        )
        another_admin_client = self.client_class()
        another_admin_client.force_login(another_admin)
        listing = another_admin_client.get(reverse("trusted-device-list"))
        self.assertContains(listing, new_device.label)
        removal = another_admin_client.post(
            reverse("trusted-device-action", args=(new_device.pk,)),
            {"action": "remove"},
        )
        self.assertEqual(removal.status_code, 302)
        new_device.refresh_from_db()
        self.assertEqual(new_device.status, TrustedDevice.Status.revoked)

    def test_admin_device_can_require_approval(self):
        another_admin = self.create_user(
            User.Role.admin,
            "approval-admin@example.test",
        )
        first_client = self.client_class()
        self.assertEqual(self.login(first_client, user=self.admin).status_code, 302)

        pending_client = self.client_class()
        with override_settings(ADMIN_DEVICE_APPROVAL_REQUIRED=True):
            response = self.login(
                pending_client,
                user=self.admin,
                user_agent="Firefox/130.0",
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        pending = TrustedDevice.objects.get(
            user=self.admin,
            status=TrustedDevice.Status.pending,
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=another_admin,
                title="Admin signed in from a new device",
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.admin,
                title="New device detected",
            ).exists()
        )
        self.assertEqual(pending.approved_by, None)

    def test_pending_device_notifications_only_go_to_eligible_approvers_and_owner(self):
        team_leader = self.create_user(
            User.Role.team_leader,
            "eligible-team-leader@example.test",
        )
        other_team_leader = self.create_user(
            User.Role.team_leader,
            "ineligible-team-leader@example.test",
        )
        management = self.create_user(
            User.Role.management,
            "eligible-management@example.test",
        )
        hr_manager = self.create_user(
            User.Role.hr_manager,
            "ineligible-hr@example.test",
        )
        self.assign_to_team(self.user, team_leader, "Notification Team")
        self.assign_to_team(
            self.create_user(User.Role.staff, "other-team-member@example.test"),
            other_team_leader,
            "Notification Other Team",
        )
        self.enroll_first_device()

        new_client = self.client_class()
        response = self.login(new_client, user_agent="Firefox/130.0")

        self.assertEqual(response.status_code, 200)
        recipients = set(
            Notification.objects.filter(title="New device detected").values_list(
                "recipient_id",
                flat=True,
            )
        )
        self.assertEqual(
            recipients,
            {self.user.pk, self.admin.pk, team_leader.pk, management.pk},
        )
        self.assertNotIn(other_team_leader.pk, recipients)
        self.assertNotIn(hr_manager.pk, recipients)

    def test_approve_device_command_supports_one_or_all_pending_devices(self):
        first = self.create_device(self.user, "First terminal device")
        second = self.create_device(self.user, "Second terminal device")
        output = StringIO()

        call_command("approve_device", self.user.email, stdout=output)

        self.assertEqual(
            TrustedDevice.objects.filter(
                user=self.user,
                status=TrustedDevice.Status.approved,
            ).count(),
            1,
        )
        self.assertEqual(
            TrustedDevice.objects.filter(
                user=self.user,
                status=TrustedDevice.Status.pending,
            ).count(),
            1,
        )
        call_command("approve_device", self.user.email, all_pending=True, stdout=output)
        self.assertEqual(
            TrustedDevice.objects.filter(
                user=self.user,
                status=TrustedDevice.Status.pending,
            ).count(),
            0,
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, TrustedDevice.Status.approved)
        self.assertEqual(second.status, TrustedDevice.Status.approved)

    def test_devices_list_shows_device_details_pending_first_and_approver_badge(self):
        pending_user = self.create_user(
            User.Role.staff,
            "pending-list-user@example.test",
        )
        pending = self.create_device(pending_user, "Pending laptop")
        pending.ip_address = "192.0.2.10"
        pending.save(update_fields=("ip_address",))
        approved_user = self.create_user(
            User.Role.client,
            "approved-list-user@example.test",
        )
        approved = self.create_device(approved_user, "Approved tablet")
        approved.status = TrustedDevice.Status.approved
        approved.save(update_fields=("status",))
        admin_client = self.client_class()
        admin_client.force_login(self.admin)

        response = admin_client.get(reverse("trusted-device-list"))

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            response.content.index(b"Pending laptop"),
            response.content.index(b"Approved tablet"),
        )
        self.assertContains(response, "Pending List User")
        self.assertContains(response, "Staff")
        self.assertContains(response, "192.0.2.10")
        self.assertContains(response, "Devices")
        self.assertContains(response, '<span class="status-badge">1</span>', html=True)

    def test_feature_can_be_disabled(self):
        with override_settings(DEVICE_VERIFICATION_ENABLED=False):
            response = self.login(self.client)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertFalse(TrustedDevice.objects.exists())
        self.assertNotIn(self.device_cookie_name(self.user), response.cookies)

    def test_device_actions_require_post_and_csrf(self):
        device = self.enroll_first_device()
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.admin)

        response = client.post(
            reverse("trusted-device-action", args=(device.pk,)),
            {"action": "remove"},
        )

        self.assertEqual(response.status_code, 403)
