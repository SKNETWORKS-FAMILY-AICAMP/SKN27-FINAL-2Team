from django.shortcuts import render


def question_create(request):
    return render(request, "question/create.html")


def question_exam(request):
    return render(request, "question/question_exam.html")


def question_result(request):
    return render(request, "question/question_result.html")
