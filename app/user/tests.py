from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .backends import UserAccountsBackend
from .models import UserAccounts


class AuthenticationSecurityTests(TestCase):
    def test_unknown_email_still_runs_password_hash_work(self):
        backend = UserAccountsBackend()

        with patch(
            "user.backends.UserAccounts.objects.get",
            side_effect=UserAccounts.DoesNotExist,
        ), patch("user.backends.make_password") as make_dummy_password:
            user = backend.authenticate(
                None,
                email="unknown@example.com",
                password="not-a-real-password",
            )

        self.assertIsNone(user)
        make_dummy_password.assert_called_once_with("not-a-real-password")

    def test_logout_rejects_get_and_accepts_post(self):
        logout_url = reverse("user:logout")

        self.assertEqual(self.client.get(logout_url).status_code, 405)
        self.assertEqual(self.client.post(logout_url).status_code, 302)
