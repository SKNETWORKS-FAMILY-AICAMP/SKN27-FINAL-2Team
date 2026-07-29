#!/usr/bin/env sh

set -eu

python app/manage.py collectstatic --noinput

exec "$@"
