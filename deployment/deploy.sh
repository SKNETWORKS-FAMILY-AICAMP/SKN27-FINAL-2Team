#!/usr/bin/env bash

set -euo pipefail

script_directory="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  pwd
)"
release_file="${script_directory}/release.env"
declare -A release_variables_seen=()

if [[ ! -r "${release_file}" ]]; then
  echo "Release file is missing or unreadable: ${release_file}" >&2
  exit 1
fi

while IFS='=' read -r variable_name variable_value; do
  variable_value="${variable_value%$'\r'}"
  case "${variable_name}" in
    AWS_REGION|ECR_REGISTRY|IMAGE_TAG|IMAGE_DIGEST|IMAGE_URI|\
    EC2_WEB_CONTAINER_NAME|EC2_WEB_DOCKER_NETWORK|EC2_WEB_BIND_HOST|\
    EC2_WEB_HOST_PORT|EC2_WEB_CONTAINER_PORT|EC2_WEB_ENV_FILE|\
    EC2_WEB_SSM_PARAMETER_PREFIX|EC2_WEB_HEALTH_TIMEOUT_SECONDS|\
    EC2_WEB_HEALTH_POLL_SECONDS|EC2_WEB_MEMORY_LIMIT|\
    EC2_WEB_CPU_LIMIT|EC2_WEB_PIDS_LIMIT|EC2_WEB_TMPFS_SIZE|\
    EC2_WEB_SERVER_NAME|EC2_WEB_NGINX_CONFIG_PATH|\
    EC2_WEB_TLS_CERTIFICATE_PATH|EC2_WEB_TLS_PRIVATE_KEY_PATH|\
    EC2_WEB_CERTBOT_WEBROOT|EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH|\
    EC2_WEB_PRIVATE_NETWORK_CIDR)
      if [[ -n "${release_variables_seen[${variable_name}]+present}" ]]; then
        echo "Duplicate release variable: ${variable_name}" >&2
        exit 1
      fi
      release_variables_seen["${variable_name}"]="present"
      printf -v "${variable_name}" '%s' "${variable_value}"
      ;;
    "")
      ;;
    *)
      echo "Unexpected release variable: ${variable_name}" >&2
      exit 1
      ;;
  esac
done < "${release_file}"

required_variables=(
  AWS_REGION
  ECR_REGISTRY
  IMAGE_TAG
  IMAGE_DIGEST
  IMAGE_URI
  EC2_WEB_CONTAINER_NAME
  EC2_WEB_DOCKER_NETWORK
  EC2_WEB_BIND_HOST
  EC2_WEB_HOST_PORT
  EC2_WEB_CONTAINER_PORT
  EC2_WEB_ENV_FILE
  EC2_WEB_SSM_PARAMETER_PREFIX
  EC2_WEB_HEALTH_TIMEOUT_SECONDS
  EC2_WEB_HEALTH_POLL_SECONDS
  EC2_WEB_MEMORY_LIMIT
  EC2_WEB_CPU_LIMIT
  EC2_WEB_PIDS_LIMIT
  EC2_WEB_TMPFS_SIZE
  EC2_WEB_SERVER_NAME
  EC2_WEB_NGINX_CONFIG_PATH
  EC2_WEB_TLS_CERTIFICATE_PATH
  EC2_WEB_TLS_PRIVATE_KEY_PATH
  EC2_WEB_CERTBOT_WEBROOT
  EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH
  EC2_WEB_PRIVATE_NETWORK_CIDR
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Missing release variable: ${variable_name}" >&2
    exit 1
  fi
done

for command_name in aws cp docker grep install mktemp nginx sed stat systemctl; do
  if ! command -v "${command_name}" > /dev/null 2>&1; then
    echo "Required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

if [[ ! -f "${EC2_WEB_ENV_FILE}" || -L "${EC2_WEB_ENV_FILE}" || ! -r "${EC2_WEB_ENV_FILE}" ]]; then
  echo "Django environment file must be a readable regular file, not a symlink: ${EC2_WEB_ENV_FILE}" >&2
  exit 1
fi

read -r environment_owner_uid environment_mode < <(
  stat -c '%u %a' -- "${EC2_WEB_ENV_FILE}"
)
if [[ "${environment_owner_uid}" != "0" || "${environment_mode}" != "600" ]]; then
  echo "Django environment file must be owned by root with mode 0600: ${EC2_WEB_ENV_FILE}" >&2
  exit 1
fi

required_application_variables=(
  POSTGRES_DB
  POSTGRES_HOST
  POSTGRES_USER
  POSTGRES_PORT
  POSTGRES_CONNECT_TIMEOUT_SECONDS
  POSTGRES_SSLMODE
  POSTGRES_SSLROOTCERT
  POSTGRES_REQUIRED_TABLES
  NEO4J_URI
  NEO4J_USER
  NEO4J_CONNECT_TIMEOUT_SECONDS
  EMAIL_BACKEND
  DEFAULT_FROM_EMAIL
  EMAIL_HOST
  EMAIL_PORT
  EMAIL_HOST_USER
  EMAIL_USE_TLS
  DJANGO_DEBUG
  DJANGO_ALLOWED_HOSTS
  DJANGO_HEALTHCHECK_HOST
  DJANGO_CSRF_TRUSTED_ORIGINS
  DJANGO_TRUST_X_FORWARDED_PROTO
  DJANGO_SECURE_SSL_REDIRECT
  DJANGO_SESSION_COOKIE_SECURE
  DJANGO_CSRF_COOKIE_SECURE
  DJANGO_SECURE_HSTS_SECONDS
)

for variable_name in "${required_application_variables[@]}"; do
  variable_count="$(
    grep -Ec "^[[:space:]]*${variable_name}=" "${EC2_WEB_ENV_FILE}" || true
  )"
  if [[ "${variable_count}" != "1" ]]; then
    echo \
      "EC2 application environment variable must appear exactly once: ${variable_name}" \
      >&2
    exit 1
  elif ! grep -Eq \
    "^[[:space:]]*${variable_name}=.+$" \
    "${EC2_WEB_ENV_FILE}"; then
    echo "EC2 application environment variable is empty: ${variable_name}" >&2
    exit 1
  fi
done

secret_application_variables=(
  POSTGRES_PASSWORD
  NEO4J_PASSWORD
  OPENAI_API_KEY
  DJANGO_SECRET_KEY
  EMAIL_HOST_PASSWORD
)

for variable_name in "${secret_application_variables[@]}"; do
  if grep -Eq \
    "^[[:space:]]*${variable_name}=" \
    "${EC2_WEB_ENV_FILE}"; then
    echo \
      "Remove ${variable_name} from ${EC2_WEB_ENV_FILE}; it must come from SSM Parameter Store." \
      >&2
    exit 1
  fi
done

hsts_seconds="$(
  sed -nE \
    's/^[[:space:]]*DJANGO_SECURE_HSTS_SECONDS=([0-9]+)[[:space:]]*$/\1/p' \
    "${EC2_WEB_ENV_FILE}"
)"
if [[ ! "${hsts_seconds}" =~ ^[0-9]+$ ]] || (( hsts_seconds < 3600 )); then
  echo "DJANGO_SECURE_HSTS_SECONDS must be at least 3600 on EC2." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_SSM_PARAMETER_PREFIX}" =~ ^/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$ ]]; then
  echo "EC2_WEB_SSM_PARAMETER_PREFIX must be an absolute SSM parameter path." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_SERVER_NAME}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "EC2_WEB_SERVER_NAME must be one explicit DNS name." >&2
  exit 1
fi

nginx_path_variables=(
  EC2_WEB_NGINX_CONFIG_PATH
  EC2_WEB_TLS_CERTIFICATE_PATH
  EC2_WEB_TLS_PRIVATE_KEY_PATH
  EC2_WEB_CERTBOT_WEBROOT
  EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH
)

for variable_name in "${nginx_path_variables[@]}"; do
  if [[ ! "${!variable_name}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    echo "${variable_name} must be an absolute Linux path without whitespace." >&2
    exit 1
  fi
done

if [[ ! "${EC2_WEB_PRIVATE_NETWORK_CIDR}" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]]; then
  echo "EC2_WEB_PRIVATE_NETWORK_CIDR must be an IPv4 CIDR." >&2
  exit 1
fi

if [[ "${EC2_WEB_BIND_HOST}" != "127.0.0.1" ]]; then
  echo "EC2_WEB_BIND_HOST must be 127.0.0.1 behind the local HTTPS proxy." >&2
  exit 1
fi

if [[ ! "${IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || [[ "${IMAGE_URI}" != "${ECR_REGISTRY}/"*@"${IMAGE_DIGEST}" ]]; then
  echo "IMAGE_URI must reference the validated ECR sha256 digest." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_MEMORY_LIMIT}" =~ ^[1-9][0-9]*(m|g)$ ]] \
  || [[ ! "${EC2_WEB_TMPFS_SIZE}" =~ ^[1-9][0-9]*(m|g)$ ]]; then
  echo "Docker memory and tmpfs limits must use a positive m or g suffix." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_CPU_LIMIT}" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || [[ "${EC2_WEB_CPU_LIMIT}" =~ ^0+([.]0+)?$ ]]; then
  echo "EC2_WEB_CPU_LIMIT must be a positive number." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_PIDS_LIMIT}" =~ ^[0-9]+$ ]] \
  || (( EC2_WEB_PIDS_LIMIT < 32 || EC2_WEB_PIDS_LIMIT > 4096 )); then
  echo "EC2_WEB_PIDS_LIMIT must be between 32 and 4096." >&2
  exit 1
fi

if ! grep -Eiq \
  '^[[:space:]]*DJANGO_DEBUG=false[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "DJANGO_DEBUG must be false on EC2." >&2
  exit 1
fi

secure_boolean_variables=(
  DJANGO_TRUST_X_FORWARDED_PROTO
  DJANGO_SECURE_SSL_REDIRECT
  DJANGO_SESSION_COOKIE_SECURE
  DJANGO_CSRF_COOKIE_SECURE
)

for variable_name in "${secure_boolean_variables[@]}"; do
  if ! grep -Eiq \
    "^[[:space:]]*${variable_name}=true[[:space:]]*$" \
    "${EC2_WEB_ENV_FILE}"; then
    echo "${variable_name} must be true on EC2." >&2
    exit 1
  fi
done

if ! grep -Eiq \
  '^[[:space:]]*POSTGRES_SSLMODE=verify-full[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "POSTGRES_SSLMODE must be verify-full on EC2." >&2
  exit 1
fi

if ! grep -Eq \
  '^[[:space:]]*NEO4J_URI=(neo4j|bolt)\+s://[^[:space:]]+[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "NEO4J_URI must use certificate-verified TLS with neo4j+s or bolt+s." >&2
  exit 1
fi

if ! grep -Eiq \
  '^[[:space:]]*EMAIL_BACKEND=django\.core\.mail\.backends\.smtp\.EmailBackend[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "EMAIL_BACKEND must use Django's SMTP backend on EC2." >&2
  exit 1
fi

if ! grep -Eiq \
  '^[[:space:]]*EMAIL_USE_TLS=true[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "EMAIL_USE_TLS must be true on EC2." >&2
  exit 1
fi

if grep -Eq \
  '^[[:space:]]*DJANGO_ALLOWED_HOSTS=.*\*' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "DJANGO_ALLOWED_HOSTS must not contain a wildcard." >&2
  exit 1
fi

allowed_hosts_value="$(
  sed -nE \
    's/^[[:space:]]*DJANGO_ALLOWED_HOSTS=(.*)[[:space:]]*$/\1/p' \
    "${EC2_WEB_ENV_FILE}"
)"
server_name_is_allowed="false"
IFS=',' read -r -a allowed_host_values <<< "${allowed_hosts_value}"
for allowed_host in "${allowed_host_values[@]}"; do
  if [[ "${allowed_host//[[:space:]]/}" == "${EC2_WEB_SERVER_NAME}" ]]; then
    server_name_is_allowed="true"
    break
  fi
done
if [[ "${server_name_is_allowed}" != "true" ]]; then
  echo "DJANGO_ALLOWED_HOSTS must include EC2_WEB_SERVER_NAME." >&2
  exit 1
fi

if grep -Eiq \
  '^[[:space:]]*DJANGO_CSRF_TRUSTED_ORIGINS=.*(http://|\*)' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "DJANGO_CSRF_TRUSTED_ORIGINS must contain only explicit HTTPS origins." >&2
  exit 1
fi

csrf_origins_value="$(
  sed -nE \
    's/^[[:space:]]*DJANGO_CSRF_TRUSTED_ORIGINS=(.*)[[:space:]]*$/\1/p' \
    "${EC2_WEB_ENV_FILE}"
)"
server_origin_is_trusted="false"
IFS=',' read -r -a csrf_origin_values <<< "${csrf_origins_value}"
for csrf_origin in "${csrf_origin_values[@]}"; do
  if [[ "${csrf_origin//[[:space:]]/}" == "https://${EC2_WEB_SERVER_NAME}" ]]; then
    server_origin_is_trusted="true"
    break
  fi
done
if [[ "${server_origin_is_trusted}" != "true" ]]; then
  echo "DJANGO_CSRF_TRUSTED_ORIGINS must include the HTTPS server origin." >&2
  exit 1
fi

if grep -Eq \
  '^[[:space:]]*POSTGRES_HOST=(localhost|127\.0\.0\.1)[[:space:]]*$' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "POSTGRES_HOST must be reachable from inside the web container." >&2
  exit 1
fi

if grep -Eq \
  '^[[:space:]]*NEO4J_URI=bolt(\+s|\+ssc)?://(localhost|127\.0\.0\.1)(:|/|$)' \
  "${EC2_WEB_ENV_FILE}"; then
  echo "NEO4J_URI must be reachable from inside the web container." >&2
  exit 1
fi

if [[ ! "${EC2_WEB_HEALTH_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] \
  || [[ ! "${EC2_WEB_HEALTH_POLL_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "Health timeout and polling interval must be numbers." >&2
  exit 1
fi

nginx_template_file="${script_directory}/nginx.conf.template"
if [[ ! -r "${nginx_template_file}" ]]; then
  echo "Nginx template is missing or unreadable: ${nginx_template_file}" >&2
  exit 1
fi

for certificate_file in \
  "${EC2_WEB_TLS_CERTIFICATE_PATH}" \
  "${EC2_WEB_TLS_PRIVATE_KEY_PATH}"; do
  if [[ ! -f "${certificate_file}" || ! -r "${certificate_file}" ]]; then
    echo "TLS certificate file must be a readable regular file: ${certificate_file}" >&2
    exit 1
  fi
done

read -r certificate_owner_uid certificate_mode < <(
  stat -Lc '%u %a' -- "${EC2_WEB_TLS_CERTIFICATE_PATH}"
)
if [[ "${certificate_owner_uid}" != "0" \
  || ! "${certificate_mode}" =~ ^[0-7]{3,4}$ ]] \
  || (( (8#${certificate_mode} & 8#022) != 0 )); then
  echo "TLS certificate must be root-owned and not group/world-writable." >&2
  exit 1
fi

read -r private_key_owner_uid private_key_mode < <(
  stat -Lc '%u %a' -- "${EC2_WEB_TLS_PRIVATE_KEY_PATH}"
)
if [[ "${private_key_owner_uid}" != "0" \
  || ! "${private_key_mode}" =~ ^[0-7]{3,4}$ ]] \
  || (( (8#${private_key_mode} & 8#077) != 0 )); then
  echo "TLS private key must be root-owned with no group/world permissions." >&2
  exit 1
fi

umask 077
secret_environment_file="$(mktemp /run/himate-secrets.XXXXXX)"
docker_config_directory="$(mktemp -d /run/himate-docker.XXXXXX)"
secret_volume_name="${EC2_WEB_CONTAINER_NAME}-secrets"
export DOCKER_CONFIG="${docker_config_directory}"

cleanup_deployment_files() {
  docker logout "${ECR_REGISTRY}" > /dev/null 2>&1 || true
  rm -f -- \
    "${secret_environment_file}" \
    "${docker_config_directory}/config.json"
  rmdir -- "${docker_config_directory}" > /dev/null 2>&1 || true
}

trap cleanup_deployment_files EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

configure_nginx() {
  local certbot_timer=""
  local timer_candidate=""
  local nginx_candidate=""
  local nginx_backup=""
  local nginx_configuration=""
  local nginx_had_previous="false"
  local renewal_hook_candidate=""

  for timer_candidate in certbot.timer snap.certbot.renew.timer; do
    if systemctl list-unit-files \
      --type=timer \
      --no-legend \
      "${timer_candidate}" \
      2> /dev/null \
      | grep -q "^${timer_candidate}[[:space:]]"; then
      certbot_timer="${timer_candidate}"
      break
    fi
  done
  if [[ -z "${certbot_timer}" ]]; then
    echo "A Certbot systemd renewal timer is required before deployment." >&2
    return 1
  fi

  install -d -m 0755 "${EC2_WEB_CERTBOT_WEBROOT}"
  install -d -m 0755 "$(dirname -- "${EC2_WEB_NGINX_CONFIG_PATH}")"
  install -d -m 0755 "$(dirname -- "${EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH}")"

  nginx_candidate="$(mktemp /run/himate-nginx.XXXXXX)"
  nginx_backup="$(mktemp /run/himate-nginx-backup.XXXXXX)"
  renewal_hook_candidate="$(mktemp /run/himate-certbot-hook.XXXXXX)"

  sed \
    -e "s|__EC2_WEB_BIND_HOST__|${EC2_WEB_BIND_HOST}|g" \
    -e "s|__EC2_WEB_HOST_PORT__|${EC2_WEB_HOST_PORT}|g" \
    -e "s|__EC2_WEB_SERVER_NAME__|${EC2_WEB_SERVER_NAME}|g" \
    -e "s|__EC2_WEB_CERTBOT_WEBROOT__|${EC2_WEB_CERTBOT_WEBROOT}|g" \
    -e "s|__EC2_WEB_TLS_CERTIFICATE_PATH__|${EC2_WEB_TLS_CERTIFICATE_PATH}|g" \
    -e "s|__EC2_WEB_TLS_PRIVATE_KEY_PATH__|${EC2_WEB_TLS_PRIVATE_KEY_PATH}|g" \
    -e "s|__EC2_WEB_PRIVATE_NETWORK_CIDR__|${EC2_WEB_PRIVATE_NETWORK_CIDR}|g" \
    "${nginx_template_file}" \
    > "${nginx_candidate}"

  if [[ -e "${EC2_WEB_NGINX_CONFIG_PATH}" ]]; then
    cp --preserve=mode,ownership,timestamps \
      "${EC2_WEB_NGINX_CONFIG_PATH}" \
      "${nginx_backup}"
    nginx_had_previous="true"
  fi

  install -m 0644 "${nginx_candidate}" "${EC2_WEB_NGINX_CONFIG_PATH}"
  if ! nginx -t; then
    if [[ "${nginx_had_previous}" == "true" ]]; then
      install -m 0644 "${nginx_backup}" "${EC2_WEB_NGINX_CONFIG_PATH}"
    else
      rm -f -- "${EC2_WEB_NGINX_CONFIG_PATH}"
    fi
    rm -f -- "${nginx_candidate}" "${nginx_backup}" "${renewal_hook_candidate}"
    echo "Generated Nginx configuration is invalid." >&2
    return 1
  fi

  nginx_configuration="$(nginx -T 2>&1)"
  if ! grep -Fq \
    "# configuration file ${EC2_WEB_NGINX_CONFIG_PATH}:" \
    <<< "${nginx_configuration}"; then
    if [[ "${nginx_had_previous}" == "true" ]]; then
      install -m 0644 "${nginx_backup}" "${EC2_WEB_NGINX_CONFIG_PATH}"
    else
      rm -f -- "${EC2_WEB_NGINX_CONFIG_PATH}"
    fi
    rm -f -- "${nginx_candidate}" "${nginx_backup}" "${renewal_hook_candidate}"
    echo "EC2_WEB_NGINX_CONFIG_PATH is not included by nginx.conf." >&2
    return 1
  fi

  if ! systemctl enable --now nginx \
    || ! systemctl reload nginx; then
    if [[ "${nginx_had_previous}" == "true" ]]; then
      install -m 0644 "${nginx_backup}" "${EC2_WEB_NGINX_CONFIG_PATH}"
      nginx -t && systemctl reload nginx || true
    else
      rm -f -- "${EC2_WEB_NGINX_CONFIG_PATH}"
    fi
    rm -f -- "${nginx_candidate}" "${nginx_backup}" "${renewal_hook_candidate}"
    echo "Failed to start or reload Nginx." >&2
    return 1
  fi

  printf '%s\n' \
    '#!/usr/bin/env sh' \
    'set -eu' \
    'nginx -t' \
    'systemctl reload nginx' \
    > "${renewal_hook_candidate}"
  install \
    -m 0755 \
    "${renewal_hook_candidate}" \
    "${EC2_WEB_CERTBOT_RENEWAL_HOOK_PATH}"

  if ! systemctl enable --now "${certbot_timer}"; then
    rm -f -- "${nginx_candidate}" "${nginx_backup}" "${renewal_hook_candidate}"
    echo "Failed to enable the Certbot renewal timer." >&2
    return 1
  fi

  rm -f -- "${nginx_candidate}" "${nginx_backup}" "${renewal_hook_candidate}"
}

configure_nginx

for variable_name in "${secret_application_variables[@]}"; do
  parameter_name="${EC2_WEB_SSM_PARAMETER_PREFIX%/}/${variable_name}"
  parameter_value=""

  if ! parameter_value="$(
    aws ssm get-parameter \
      --region "${AWS_REGION}" \
      --name "${parameter_name}" \
      --with-decryption \
      --query "Parameter.Value" \
      --output text
  )"; then
    echo "Failed to read SSM SecureString parameter: ${parameter_name}" >&2
    exit 1
  fi

  if [[ -z "${parameter_value}" || "${parameter_value}" == "None" ]]; then
    echo "SSM SecureString parameter is empty: ${parameter_name}" >&2
    exit 1
  fi

  if [[ "${parameter_value}" == *$'\n'* || "${parameter_value}" == *$'\r'* ]]; then
    echo "SSM SecureString parameter must be a single-line value: ${parameter_name}" >&2
    exit 1
  fi

  normalized_parameter_value="${parameter_value,,}"
  case "${normalized_parameter_value}" in
    change_me|password|test|himate1234|replace_with*|your_*)
      echo "SSM SecureString parameter contains a placeholder: ${parameter_name}" >&2
      exit 1
      ;;
  esac

  if [[ "${variable_name}" == "DJANGO_SECRET_KEY" ]]; then
    if (( ${#parameter_value} < 50 )) \
      || [[ ! "${parameter_value}" =~ [a-z] ]] \
      || [[ ! "${parameter_value}" =~ [A-Z] ]] \
      || [[ ! "${parameter_value}" =~ [0-9] ]]; then
      echo "DJANGO_SECRET_KEY must be at least 50 characters with mixed character classes." >&2
      exit 1
    fi
  elif (( ${#parameter_value} < 12 )); then
    echo "SSM SecureString parameter is too short: ${parameter_name}" >&2
    exit 1
  fi

  printf '%s=%s\n' \
    "${variable_name}" \
    "${parameter_value}" \
    >> "${secret_environment_file}"
  unset normalized_parameter_value
  unset parameter_value
done

if ! docker network inspect "${EC2_WEB_DOCKER_NETWORK}" > /dev/null 2>&1; then
    echo "Docker network does not exist: ${EC2_WEB_DOCKER_NETWORK}" >&2
    exit 1
fi

prepare_secret_volume() {
  local container_image="$1"

  docker volume create \
    --label "himate.container=${EC2_WEB_CONTAINER_NAME}" \
    "${secret_volume_name}" \
    > /dev/null
  docker run \
    --rm \
    --network none \
    --user root \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add FOWNER \
    --security-opt no-new-privileges:true \
    --read-only \
    --volume "${secret_environment_file}:/run/source-secrets.env:ro" \
    --volume "${secret_volume_name}:/run/himate-secrets:rw" \
    --entrypoint /bin/sh \
    "${container_image}" \
    -c '
      set -eu
      cp /run/source-secrets.env /run/himate-secrets/env.next
      chown app:app /run/himate-secrets/env.next
      chmod 0400 /run/himate-secrets/env.next
      mv -f /run/himate-secrets/env.next /run/himate-secrets/env
    '
}

run_web_container() {
  local container_image="$1"
  local image_tag="$2"

  docker run \
    --detach \
    --name "${EC2_WEB_CONTAINER_NAME}" \
    --restart unless-stopped \
    --network "${EC2_WEB_DOCKER_NETWORK}" \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --read-only \
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${EC2_WEB_TMPFS_SIZE}" \
    --memory "${EC2_WEB_MEMORY_LIMIT}" \
    --cpus "${EC2_WEB_CPU_LIMIT}" \
    --pids-limit "${EC2_WEB_PIDS_LIMIT}" \
    --env-file "${EC2_WEB_ENV_FILE}" \
    --volume "${secret_volume_name}:/run/himate-secrets:ro" \
    --env "SECRETS_FILE=/run/himate-secrets/env" \
    --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
    --publish "${EC2_WEB_BIND_HOST}:${EC2_WEB_HOST_PORT}:${EC2_WEB_CONTAINER_PORT}" \
    --label "himate.git-sha=${image_tag}" \
    "${container_image}" \
    > /dev/null
}

restore_previous_container() {
  local previous_container_image="$1"
  local previous_image_tag="$2"

  docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1 || true

  if [[ -z "${previous_container_image}" ]]; then
    echo "No previous image is available for rollback." >&2
    return 0
  fi

  echo "Restoring previous image: ${previous_container_image}" >&2
  run_web_container "${previous_container_image}" "${previous_image_tag}"
}

aws ecr get-login-password \
  --region "${AWS_REGION}" \
  | docker login \
    --username AWS \
    --password-stdin \
    "${ECR_REGISTRY}"

docker pull "${IMAGE_URI}"
prepare_secret_volume "${IMAGE_URI}"

echo "Checking Django production settings."
docker run \
  --rm \
  --network "${EC2_WEB_DOCKER_NETWORK}" \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --read-only \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${EC2_WEB_TMPFS_SIZE}" \
  --memory "${EC2_WEB_MEMORY_LIMIT}" \
  --cpus "${EC2_WEB_CPU_LIMIT}" \
  --pids-limit "${EC2_WEB_PIDS_LIMIT}" \
  --env-file "${EC2_WEB_ENV_FILE}" \
  --volume "${secret_volume_name}:/run/himate-secrets:ro" \
  --env "SECRETS_FILE=/run/himate-secrets/env" \
  --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
  --entrypoint python \
  "${IMAGE_URI}" \
  app/manage.py check --deploy --fail-level ERROR

echo "Checking required PostgreSQL schema before applying migrations."
docker run \
  --rm \
  --network "${EC2_WEB_DOCKER_NETWORK}" \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --read-only \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${EC2_WEB_TMPFS_SIZE}" \
  --memory "${EC2_WEB_MEMORY_LIMIT}" \
  --cpus "${EC2_WEB_CPU_LIMIT}" \
  --pids-limit "${EC2_WEB_PIDS_LIMIT}" \
  --env-file "${EC2_WEB_ENV_FILE}" \
  --volume "${secret_volume_name}:/run/himate-secrets:ro" \
  --env "SECRETS_FILE=/run/himate-secrets/env" \
  --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
  --entrypoint python \
  "${IMAGE_URI}" \
  app/manage.py check_database_schema

echo "Applying Django migrations before replacing the running container."
docker run \
  --rm \
  --network "${EC2_WEB_DOCKER_NETWORK}" \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --read-only \
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=${EC2_WEB_TMPFS_SIZE}" \
  --memory "${EC2_WEB_MEMORY_LIMIT}" \
  --cpus "${EC2_WEB_CPU_LIMIT}" \
  --pids-limit "${EC2_WEB_PIDS_LIMIT}" \
  --env-file "${EC2_WEB_ENV_FILE}" \
  --volume "${secret_volume_name}:/run/himate-secrets:ro" \
  --env "SECRETS_FILE=/run/himate-secrets/env" \
  --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
  --entrypoint python \
  "${IMAGE_URI}" \
  app/manage.py migrate --noinput

previous_image=""
previous_image_tag="rollback"
if docker container inspect "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1; then
  previous_image="$(
    docker container inspect \
      --format "{{.Config.Image}}" \
      "${EC2_WEB_CONTAINER_NAME}"
  )"
  inspected_image_tag="$(
    docker container inspect \
      --format '{{index .Config.Labels "himate.git-sha"}}' \
      "${EC2_WEB_CONTAINER_NAME}"
  )"
  if [[ -n "${inspected_image_tag}" && "${inspected_image_tag}" != "<no value>" ]]; then
    previous_image_tag="${inspected_image_tag}"
  fi
  docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null
fi

if ! run_web_container "${IMAGE_URI}" "${IMAGE_TAG}"; then
  echo "New container failed to start." >&2
  restore_previous_container "${previous_image}" "${previous_image_tag}"
  exit 1
fi

health_deadline=$((SECONDS + EC2_WEB_HEALTH_TIMEOUT_SECONDS))
health_status="starting"
container_status="running"

while (( SECONDS < health_deadline )); do
  if ! docker container inspect "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1; then
    container_status="missing"
    break
  fi

  container_status="$(
    docker container inspect \
      --format "{{.State.Status}}" \
      "${EC2_WEB_CONTAINER_NAME}"
  )"
  health_status="$(
    docker container inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
      "${EC2_WEB_CONTAINER_NAME}"
  )"

  if [[ "${container_status}" != "running" ]]; then
    break
  fi

  if [[ "${health_status}" == "healthy" ]]; then
    echo "Deployed ${IMAGE_URI} as ${EC2_WEB_CONTAINER_NAME}."
    exit 0
  elif [[ "${health_status}" == "unhealthy" \
    || "${health_status}" == "missing" ]]; then
    break
  fi

  sleep "${EC2_WEB_HEALTH_POLL_SECONDS}"
done

echo \
  "New container failed readiness check: container=${container_status}, health=${health_status}" \
  >&2
docker logs --tail 100 "${EC2_WEB_CONTAINER_NAME}" >&2 || true
restore_previous_container "${previous_image}" "${previous_image_tag}"
exit 1
