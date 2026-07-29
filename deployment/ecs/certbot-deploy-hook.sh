#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "Usage: $0 <runtime-tls-directory> <nginx-container-name> <nginx-group-id>" >&2
  exit 1
fi

runtime_tls_directory="$1"
nginx_container_name="$2"
nginx_group_id="$3"

: "${RENEWED_LINEAGE:?Certbot must provide RENEWED_LINEAGE}"

if [[ ! "${runtime_tls_directory}" =~ ^/[A-Za-z0-9_./-]+$ ]] \
  || [[ "${runtime_tls_directory}" == *"/../"* ]] \
  || [[ "${runtime_tls_directory}" == */.. ]]; then
  echo "Runtime TLS directory must be a safe absolute path." >&2
  exit 1
fi

if [[ ! "${nginx_container_name}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,254}$ ]]; then
  echo "Nginx container name contains unsupported characters." >&2
  exit 1
fi

if [[ ! "${nginx_group_id}" =~ ^[0-9]+$ ]]; then
  echo "Nginx group ID must be a number." >&2
  exit 1
fi

install \
  --directory \
  --mode 0750 \
  --owner root \
  --group "${nginx_group_id}" \
  "${runtime_tls_directory}"
install \
  --mode 0444 \
  --owner root \
  --group root \
  "${RENEWED_LINEAGE}/fullchain.pem" \
  "${runtime_tls_directory}/fullchain.pem"
install \
  --mode 0440 \
  --owner root \
  --group "${nginx_group_id}" \
  "${RENEWED_LINEAGE}/privkey.pem" \
  "${runtime_tls_directory}/privkey.pem"

nginx_container_id="$(
  docker ps \
    --filter "label=com.amazonaws.ecs.container-name=${nginx_container_name}" \
    --format '{{.ID}}' \
    | sed -n '1p'
)"
if [[ -n "${nginx_container_id}" ]]; then
  docker kill --signal HUP "${nginx_container_id}" > /dev/null
fi
