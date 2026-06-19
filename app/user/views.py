from django.shortcuts import render


def login_page(request):
    return render(request, "user/login.html")


def register_page(request):
    return render(request, "user/register.html")


def mypage(request):
    return render(request, "user/mypage.html")
