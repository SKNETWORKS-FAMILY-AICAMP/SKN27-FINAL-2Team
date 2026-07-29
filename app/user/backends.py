from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone

from .models import UserAccounts


class UserAccountsBackend:
    """Authenticate against the existing user_accounts table."""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        email: str | None = None,
        password: str | None = None,
        **kwargs: object,
    ) -> UserAccounts | None:
        email = (email or username or "").strip().lower()
        if not email or not password:
            return None

        try:
            user = UserAccounts.objects.get(email=email, deleted_at__isnull=True)
        except UserAccounts.DoesNotExist:
            make_password(password)
            return None

        password_matches = check_password(password, user.password_hash)
        if user.is_locked:
            if user.locked_at and timezone.now() - user.locked_at > timedelta(minutes=30):
                user.is_locked = False
                user.login_fail_count = 0
                user.locked_at = None
                user.save(update_fields=["is_locked", "login_fail_count", "locked_at"])
            else:
                return None

        if user.status != "active":
            return None

        if not password_matches:
            self._register_login_failure(user)
            return None

        if user.login_fail_count or user.is_locked:
            user.login_fail_count = 0
            user.is_locked = False
            user.locked_at = None
            user.save(update_fields=["login_fail_count", "is_locked", "locked_at"])

        return user

    def _register_login_failure(self, user: UserAccounts) -> None:
        """실패 횟수는 기록하되 외부 요청만으로 계정을 잠그지는 않는다."""
        UserAccounts.objects.filter(pk=user.pk).update(
            login_fail_count=F("login_fail_count") + 1,
        )

    def get_user(self, user_id: int) -> UserAccounts | None:
        try:
            return UserAccounts.objects.get(user_id=user_id, deleted_at__isnull=True)
        except UserAccounts.DoesNotExist:
            return None
