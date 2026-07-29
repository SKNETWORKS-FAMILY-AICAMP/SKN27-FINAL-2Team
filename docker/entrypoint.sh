#!/usr/bin/env sh

set -eu

: "${WEB_CONTAINER_PORT:?WEB_CONTAINER_PORT is required}"

case "${WEB_CONTAINER_PORT}" in
  *[!0-9]*|'')
    echo "WEB_CONTAINER_PORT must be a number." >&2
    exit 1
    ;;
esac

python app/manage.py collectstatic --noinput

if [ "${1:-}" = "gunicorn" ]; then
  set -- "$@" --bind "0.0.0.0:${WEB_CONTAINER_PORT}"
fi

exec "$@"
