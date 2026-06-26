from django.db import models
from django.utils import timezone


class UserAccounts(models.Model):
    user_id = models.BigAutoField(primary_key=True)
    email = models.CharField(max_length=255, unique=True)
    password_hash = models.CharField(max_length=255)
    nickname = models.CharField(max_length=50, blank=True, null=True)
    login_fail_count = models.IntegerField(default=0)
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, default='active')
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    daily_available_hours = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=1.0,
    )
    exam_date = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'user_accounts'

    @property
    def username(self):
        return self.email

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return self.status == 'active'

    def get_username(self):
        return self.email


class EmailVerificationCode(models.Model):
    email = models.EmailField(max_length=255, db_index=True)
    code = models.CharField(max_length=10)
    purpose = models.CharField(max_length=20, default='register')
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(blank=True, null=True)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'email_verification_codes'
        indexes = [
            models.Index(fields=['email', 'purpose']),
        ]

    @property
    def is_expired(self):
        if self.expires_at is None:
            return True
        return timezone.now() > self.expires_at

    def mark_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=['is_used', 'used_at'])


