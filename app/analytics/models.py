# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models

from question.models import SolveSessions


class StudyPlanMypage(models.Model):
    studyplan_id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey('user.UserAccounts', models.DO_NOTHING)
    study_plans = models.TextField(blank=True, null=True)
    study_plan_items = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField()
    modified_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'study_plan_mypage'


class NoteMypage(models.Model):
    note_id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey('user.UserAccounts', models.DO_NOTHING)
    created_at = models.DateTimeField()
    modified_at = models.DateTimeField()
    title = models.CharField(max_length=50)
    era = models.CharField(max_length=50, blank=True, null=True)
    topic = models.CharField(max_length=50, blank=True, null=True)
    difficulty = models.CharField(max_length=50, blank=True, null=True)
    question_type = models.CharField(max_length=20, blank=True, null=True)
    content = models.TextField()
    answer_no = models.IntegerField(blank=True, null=True)
    answer_explanation = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'note_mypage'


class Analytics(models.Model):
    analytics_id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(SolveSessions, models.DO_NOTHING)
    key_concept = models.CharField(max_length=50)
    classification = models.CharField(max_length=20)
    avg_time_sec = models.IntegerField(blank=True, null=True)
    topic_rate = models.FloatField()
    date = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'analytics'
