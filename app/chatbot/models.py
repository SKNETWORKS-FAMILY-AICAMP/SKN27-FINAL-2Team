# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class ChatSessions(models.Model):
    session_id = models.CharField(primary_key=True, max_length=50)
    chat_type = models.CharField(max_length=20)
    turn_count = models.IntegerField()
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField()
    user = models.ForeignKey('user.UserAccounts', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'chat_sessions'


class ChatMessages(models.Model):
    message_id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(ChatSessions, models.DO_NOTHING)
    sender_type = models.CharField(max_length=10)
    content = models.TextField()
    used_tokens = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'chat_messages'
