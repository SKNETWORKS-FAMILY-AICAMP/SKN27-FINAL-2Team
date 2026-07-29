from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """미인증 요청을 403 대신 401 로 돌려준다.

    SessionAuthentication 만 쓰면 DRF 는 미인증에 403 을 주는데, 프런트가
    401 로 로그인 유도를 처리하므로 인증 예외만 401 로 맞춘다.
    """
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        response.status_code = 401

    return response
