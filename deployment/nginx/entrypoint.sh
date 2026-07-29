#!/usr/bin/env sh

set -eu

: "${NGINX_SERVER_NAME:?NGINX_SERVER_NAME is required}"
: "${NGINX_UPSTREAM_HOST:?NGINX_UPSTREAM_HOST is required}"
: "${NGINX_UPSTREAM_PORT:?NGINX_UPSTREAM_PORT is required}"
: "${NGINX_HTTP_LISTEN_PORT:?NGINX_HTTP_LISTEN_PORT is required}"
: "${NGINX_HTTPS_LISTEN_PORT:?NGINX_HTTPS_LISTEN_PORT is required}"
: "${NGINX_TLS_CERTIFICATE_PATH:?NGINX_TLS_CERTIFICATE_PATH is required}"
: "${NGINX_TLS_PRIVATE_KEY_PATH:?NGINX_TLS_PRIVATE_KEY_PATH is required}"

if ! printf '%s' "${NGINX_SERVER_NAME}" \
  | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
  echo "NGINX_SERVER_NAME must be one hostname." >&2
  exit 1
fi

if ! printf '%s' "${NGINX_UPSTREAM_HOST}" \
  | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
  echo "NGINX_UPSTREAM_HOST must be a hostname or IPv4 address." >&2
  exit 1
fi

for path_value in \
  "${NGINX_TLS_CERTIFICATE_PATH}" \
  "${NGINX_TLS_PRIVATE_KEY_PATH}"; do
  if ! printf '%s' "${path_value}" | grep -Eq '^/[A-Za-z0-9_./-]+$' \
    || printf '%s' "${path_value}" | grep -Eq '(^|/)[.][.](/|$)'; then
    echo "Nginx TLS paths must be safe absolute paths." >&2
    exit 1
  fi

  if [ ! -r "${path_value}" ]; then
    echo "Nginx cannot read TLS file: ${path_value}" >&2
    exit 1
  fi
done

for port_value in \
  "${NGINX_UPSTREAM_PORT}" \
  "${NGINX_HTTP_LISTEN_PORT}" \
  "${NGINX_HTTPS_LISTEN_PORT}"; do
  case "${port_value}" in
    *[!0-9]*|'')
      echo "Nginx ports must be numbers." >&2
      exit 1
      ;;
  esac

  if [ "${port_value}" -lt 1 ] || [ "${port_value}" -gt 65535 ]; then
    echo "Nginx ports must be between 1 and 65535." >&2
    exit 1
  fi
done

sed \
  -e "s|__NGINX_SERVER_NAME__|${NGINX_SERVER_NAME}|g" \
  -e "s|__NGINX_UPSTREAM_HOST__|${NGINX_UPSTREAM_HOST}|g" \
  -e "s|__NGINX_UPSTREAM_PORT__|${NGINX_UPSTREAM_PORT}|g" \
  -e "s|__NGINX_HTTP_LISTEN_PORT__|${NGINX_HTTP_LISTEN_PORT}|g" \
  -e "s|__NGINX_HTTPS_LISTEN_PORT__|${NGINX_HTTPS_LISTEN_PORT}|g" \
  -e "s|__NGINX_TLS_CERTIFICATE_PATH__|${NGINX_TLS_CERTIFICATE_PATH}|g" \
  -e "s|__NGINX_TLS_PRIVATE_KEY_PATH__|${NGINX_TLS_PRIVATE_KEY_PATH}|g" \
  /etc/nginx/nginx.conf.template \
  > /tmp/nginx.conf

nginx -t -c /tmp/nginx.conf

if [ "${1:-}" = "--test" ]; then
  exit 0
fi

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
