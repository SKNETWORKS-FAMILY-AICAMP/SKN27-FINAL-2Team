# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class Questions(models.Model):
    question_id = models.BigAutoField(primary_key=True)
    question_no = models.IntegerField(blank=True, null=True)
    q_score = models.IntegerField()
    era = models.CharField(max_length=50)
    topic = models.CharField(max_length=50)
    question_type = models.CharField(max_length=50)
    question_subtype = models.CharField(max_length=50)
    content = models.TextField()
    passage = models.TextField(blank=True, null=True)
    image_caption = models.TextField(blank=True, null=True)
    question_image_path = models.TextField(blank=True, null=True)
    answer_no = models.IntegerField()
    answer_explanation = models.TextField()
    core_concept = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = 'questions'


class QuestionOptions(models.Model):
    choice_id = models.BigAutoField(primary_key=True)
    question = models.ForeignKey(Questions, models.DO_NOTHING)
    choice_no = models.IntegerField()
    content = models.TextField()
    choice_image_path = models.TextField(blank=True, null=True)
    is_answer = models.BooleanField()
    choice_explanation = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'question_options'


class SolveSessions(models.Model):
    session_id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey('user.UserAccounts', models.DO_NOTHING)
    session_type = models.CharField(max_length=20)
    total_count = models.IntegerField()
    elapsed_sec = models.IntegerField(blank=True, null=True)
    status = models.CharField(max_length=20)
    answer_rate = models.FloatField(blank=True, null=True)
    total_score = models.IntegerField(blank=True, null=True)
    recorded_date = models.DateField()
    review_type = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'solve_sessions'


class SolveRecords(models.Model):
    record_id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(SolveSessions, models.DO_NOTHING)
    question = models.ForeignKey(Questions, models.DO_NOTHING)
    selected_no = models.IntegerField(blank=True, null=True)
    is_correct = models.BooleanField()
    time_spent_ms = models.IntegerField(blank=True, null=True)
    is_saved = models.BooleanField(default=False)
    saved_at = models.DateTimeField(blank=True, null=True)
    studyplan_id = models.BigIntegerField(blank=True, null=True)
    study_plan_block_id = models.CharField(max_length=36, blank=True, null=True)
    q_type = models.CharField(max_length=20)
    topic = models.CharField(max_length=50)
    era = models.CharField(max_length=20)
    q_score = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'solve_records'

