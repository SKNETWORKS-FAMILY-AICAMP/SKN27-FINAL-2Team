from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .backends import UserAccountsBackend
from .models import UserAccounts
from .oauth import _find_or_create_social_user


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


class OAuthAccountTest(SimpleTestCase):
    def test_social_nickname_is_limited_to_database_length(self) -> None:
        provider_query = MagicMock()
        provider_query.first.return_value = None
        email_query = MagicMock()
        email_query.exists.return_value = False

        with patch(
            "user.oauth.UserAccounts.objects.filter",
            side_effect=[provider_query, email_query],
        ), patch(
            "user.oauth.UserAccounts.objects.create",
        ) as create_user:
            _find_or_create_social_user(
                provider="google",
                provider_id="provider-user-id",
                email="social@example.com",
                nickname="가" * 50,
            )

        self.assertEqual(
            create_user.call_args.kwargs["nickname"],
            "가" * 30,
        )
