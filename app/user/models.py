# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models
from django.utils import timezone


class UserAccounts(models.Model):
    user_id = models.BigAutoField(primary_key=True)
    email = models.CharField(unique=True, max_length=255)
    password_hash = models.CharField(max_length=255)
    nickname = models.CharField(max_length=30)
    login_fail_count = models.IntegerField()
    is_locked = models.BooleanField()
    locked_at = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    deleted_at = models.DateTimeField(blank=True, null=True)

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
        return self.status == "active" and self.deleted_at is None and not self.is_locked

    def get_username(self):
        return self.email


class EmailVerificationCode(models.Model):
    email = models.EmailField(max_length=255, db_index=True)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, default="register")
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "email_verification_codes"
        indexes = [
            models.Index(fields=["email", "purpose", "is_used"]),
        ]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def mark_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=["is_used", "used_at"])


class UserStudyProfile(models.Model):
    user = models.OneToOneField(
        UserAccounts,
        models.CASCADE,
        db_column="user_id",
        primary_key=True,
        related_name="study_profile",
    )
    daily_available_hours = models.DecimalField(max_digits=3, decimal_places=1, default=1.0)
    exam_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_study_profiles"
