from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def diagnosis_intro(request):
    return render(request, "diagnosis/intro.html")


@login_required
def diagnosis_exam(request):
    return render(request, "diagnosis/exam.html")


@login_required
def diagnosis_result(request):
    return render(request, "diagnosis/result.html")
