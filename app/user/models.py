from django.db import models
from django.utils import timezone


class UserAccounts(models.Model):
    user_id = models.BigAutoField(primary_key=True)
    email = models.CharField(max_length=255, unique=True)
    # 소셜 로그인 사용자는 비밀번호가 없으므로 null 허용.
    password_hash = models.CharField(max_length=255, blank=True, null=True)
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
    # 소셜 로그인 식별. (provider, provider_id) 조합으로 계정을 찾는다.
    provider = models.CharField(max_length=20, blank=True, null=True)
    provider_id = models.CharField(max_length=255, blank=True, null=True)

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
    # 평문이 아니라 make_password 해시를 저장하므로 길이를 넓힌다.
    # DB 컬럼도 함께 넓혀야 한다(user/migrations 의 RunSQL 참고).
    code = models.CharField(max_length=128)
    purpose = models.CharField(max_length=20, default='register')
    is_used = models.BooleanField(default=False)
    attempt_count = models.PositiveSmallIntegerField(default=0)
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


