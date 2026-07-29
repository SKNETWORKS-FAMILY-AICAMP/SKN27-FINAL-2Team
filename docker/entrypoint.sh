#!/usr/bin/env sh

set -eu

: "${WEB_CONTAINER_PORT:?WEB_CONTAINER_PORT is required}"

case "${WEB_CONTAINER_PORT}" in
  *[!0-9]*|'')
    echo "WEB_CONTAINER_PORT must be a number." >&2
    exit 1
    ;;
esac

if [ "${1:-}" = "gunicorn" ]; then
  # 기본이 sync 1 worker 라 SSE 스트리밍이나 이미지 프록시 한 건이
  # 전체 요청을 막을 수 있다. gthread 다중 워커로 동시성을 확보한다.
  # 값은 인스턴스 사양에 맞춰 env 로 조정한다.
  set -- "$@" \
    --bind "0.0.0.0:${WEB_CONTAINER_PORT}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}"
fi

exec "$@"
