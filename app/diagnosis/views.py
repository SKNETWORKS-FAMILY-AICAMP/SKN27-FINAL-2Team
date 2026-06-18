from django.shortcuts import render


def diagnosis_intro(request):
    return render(request, "diagnosis/intro.html")


def diagnosis_exam(request):
    return render(request, "diagnosis/exam.html")
