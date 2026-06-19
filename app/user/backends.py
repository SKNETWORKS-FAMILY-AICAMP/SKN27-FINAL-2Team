from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.utils import timezone

from .models import UserAccounts


class UserAccountsBackend:
    """Authenticate against the existing user_accounts table."""

    def authenticate(self, request, username=None, email=None, password=None, **kwargs):
        email = (email or username or "").strip().lower()
        if not email or not password:
            return None

        try:
            user = UserAccounts.objects.get(email=email, deleted_at__isnull=True)
        except UserAccounts.DoesNotExist:
            return None

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

        if not check_password(password, user.password_hash):
            user.login_fail_count = (user.login_fail_count or 0) + 1
            update_fields = ["login_fail_count"]
            if user.login_fail_count >= 5:
                user.is_locked = True
                user.locked_at = timezone.now()
                update_fields.extend(["is_locked", "locked_at"])
            user.save(update_fields=update_fields)
            return None

        if user.login_fail_count or user.is_locked:
            user.login_fail_count = 0
            user.is_locked = False
            user.locked_at = None
            user.save(update_fields=["login_fail_count", "is_locked", "locked_at"])

        return user

    def get_user(self, user_id):
        try:
            return UserAccounts.objects.get(user_id=user_id, deleted_at__isnull=True)
        except UserAccounts.DoesNotExist:
            return None
