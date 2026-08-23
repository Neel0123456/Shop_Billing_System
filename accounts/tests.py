"""Authentication tests: login, throttling, logout, forgot password, profile."""
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()

PASSWORD = "shop-billing-pass-1"


class UserModelTests(TestCase):
    def test_email_is_the_login_field(self):
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_create_user_normalises_the_email(self):
        user = User.objects.create_user(email="  OWNER@Shop.TEST ", password=PASSWORD)
        self.assertEqual(user.email, "owner@shop.test")

    def test_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password=PASSWORD)

    def test_password_is_hashed(self):
        user = User.objects.create_user(email="a@b.test", password=PASSWORD)
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_superuser_flags(self):
        admin = User.objects.create_superuser(email="admin@b.test", password=PASSWORD)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_display_helpers(self):
        user = User.objects.create_user(
            email="raj@b.test", password=PASSWORD, full_name="Raj Kumar Patel"
        )
        self.assertEqual(user.get_full_name(), "Raj Kumar Patel")
        self.assertEqual(user.get_short_name(), "Raj")
        self.assertEqual(user.display_initial, "R")

    def test_display_helpers_fall_back_to_email(self):
        user = User.objects.create_user(email="noname@b.test", password=PASSWORD)
        self.assertEqual(user.get_full_name(), "noname@b.test")


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="owner@shop.test", password=PASSWORD, full_name="Shop Owner"
        )
        self.url = reverse("accounts:login")

    def test_login_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Forgot your password")

    def test_correct_credentials_sign_in(self):
        response = self.client.post(
            self.url, {"username": "owner@shop.test", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("billing:invoice_list"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_email_case_is_ignored(self):
        response = self.client.post(
            self.url, {"username": "OWNER@SHOP.TEST", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("billing:invoice_list"))

    def test_wrong_password_is_rejected_with_a_friendly_message(self):
        response = self.client.post(
            self.url, {"username": "owner@shop.test", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email or password is not correct")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_is_rejected(self):
        response = self.client.post(
            self.url, {"username": "nobody@shop.test", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_disabled_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            self.url, {"username": "owner@shop.test", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_me_keeps_the_session_after_the_browser_closes(self):
        self.client.post(
            self.url,
            {"username": "owner@shop.test", "password": PASSWORD, "remember_me": "on"},
        )
        self.assertNotEqual(self.client.session.get_expiry_age(), 0)

    def test_without_remember_me_the_session_ends_with_the_browser(self):
        self.client.post(self.url, {"username": "owner@shop.test", "password": PASSWORD})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    @override_settings(LOGIN_MAX_ATTEMPTS=3)
    def test_repeated_failures_lock_the_form(self):
        for _ in range(3):
            self.client.post(self.url, {"username": "owner@shop.test", "password": "nope"})

        # Locked out: even the correct password is refused for now.
        response = self.client.post(
            self.url, {"username": "owner@shop.test", "password": PASSWORD}, follow=True
        )
        self.assertContains(response, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_MAX_ATTEMPTS=3)
    def test_a_successful_login_clears_the_counter(self):
        self.client.post(self.url, {"username": "owner@shop.test", "password": "nope"})
        self.client.post(self.url, {"username": "owner@shop.test", "password": PASSWORD})
        self.assertIn("_auth_user_id", self.client.session)

    def test_already_signed_in_users_are_redirected_away(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="owner@shop.test", password=PASSWORD)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_post_signs_the_user_out(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@shop.test", password=PASSWORD, full_name="Shop Owner"
        )
        self.url = reverse("accounts:password_reset")

    def test_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forgot your password")

    def test_a_known_email_receives_a_reset_link(self):
        response = self.client.post(self.url, {"email": "owner@shop.test"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("owner@shop.test", mail.outbox[0].to)
        self.assertIn("/accounts/password-reset/", mail.outbox[0].body)

    def test_email_case_does_not_matter(self):
        self.client.post(self.url, {"email": "OWNER@SHOP.TEST"})
        self.assertEqual(len(mail.outbox), 1)

    def test_an_unknown_email_reveals_nothing(self):
        response = self.client.post(self.url, {"email": "stranger@nowhere.test"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_the_full_reset_flow_changes_the_password(self):
        self.client.post(self.url, {"email": "owner@shop.test"})
        body = mail.outbox[0].body
        link = next(
            line.strip()
            for line in body.splitlines()
            if "/accounts/password-reset/" in line and "/sent/" not in line
        )
        path = link[link.index("/accounts/") :]

        # Django swaps the token for a session-held one and redirects.
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)

        new_password = "a-brand-new-pass-9"
        response = self.client.post(
            response.request["PATH_INFO"],
            {"new_password1": new_password, "new_password2": new_password},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.assertFalse(self.user.check_password(PASSWORD))

    def test_mismatched_passwords_are_rejected(self):
        self.client.post(self.url, {"email": "owner@shop.test"})
        body = mail.outbox[0].body
        link = next(
            line.strip()
            for line in body.splitlines()
            if "/accounts/password-reset/" in line and "/sent/" not in line
        )
        path = link[link.index("/accounts/") :]
        response = self.client.get(path, follow=True)

        response = self.client.post(
            response.request["PATH_INFO"],
            {"new_password1": "one-password-11", "new_password2": "another-one-22"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_a_tampered_link_shows_the_invalid_page(self):
        response = self.client.get(
            reverse(
                "accounts:password_reset_confirm",
                kwargs={"uidb64": "MQ", "token": "bad-token-123"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no longer usable")

    def test_a_short_password_is_rejected(self):
        self.client.post(self.url, {"email": "owner@shop.test"})
        body = mail.outbox[0].body
        link = next(
            line.strip()
            for line in body.splitlines()
            if "/accounts/password-reset/" in line and "/sent/" not in line
        )
        path = link[link.index("/accounts/") :]
        response = self.client.get(path, follow=True)

        response = self.client.post(
            response.request["PATH_INFO"],
            {"new_password1": "abc", "new_password2": "abc"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@shop.test", password=PASSWORD, full_name="Shop Owner"
        )
        self.client.force_login(self.user)

    def test_profile_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)

    def test_profile_can_be_updated(self):
        response = self.client.post(
            reverse("accounts:profile"),
            {"full_name": "Renamed Owner", "phone": "9876500000", "email": "owner@shop.test"},
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Renamed Owner")
        self.assertEqual(self.user.phone, "9876500000")

    def test_a_duplicate_email_is_rejected(self):
        User.objects.create_user(email="taken@shop.test", password=PASSWORD)
        response = self.client.post(
            reverse("accounts:profile"),
            {"full_name": "Owner", "phone": "", "email": "taken@shop.test"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "owner@shop.test")

    def test_password_change_works(self):
        new_password = "changed-in-app-77"
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": PASSWORD,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

    def test_password_change_needs_the_current_password(self):
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "not-the-password",
                "new_password1": "whatever-99",
                "new_password2": "whatever-99",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
