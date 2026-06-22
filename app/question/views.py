from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def question_create(request):
    return render(request, "question/create.html")


@login_required
def question_exam(request):
    return render(request, "question/question_exam.html")


@login_required
def question_result(request):
    return render(request, "question/question_result.html")
