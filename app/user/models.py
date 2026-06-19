# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class UserAccounts(models.Model):
    user_id = models.BigAutoField(primary_key=True)
    email = models.CharField(unique=True, max_length=255)
    password_hash = models.CharField(max_length=255)
    nickname = models.CharField(max_length=30)
    login_fail_count = models.IntegerField()
    is_locked = models.BooleanField()
    locked_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    deleted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'user_accounts'
