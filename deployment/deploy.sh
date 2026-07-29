#!/usr/bin/env bash

set -euo pipefail

script_directory="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  pwd
)"
release_file="${script_directory}/release.env"

if [[ ! -r "${release_file}" ]]; then
  echo "Release file is missing or unreadable: ${release_file}" >&2
  exit 1
fi

source "${release_file}"

required_variables=(
  AWS_REGION
  ECR_REGISTRY
  IMAGE_TAG
  IMAGE_URI
  EC2_WEB_CONTAINER_NAME
  EC2_WEB_DOCKER_NETWORK
  EC2_WEB_HOST_PORT
  EC2_WEB_CONTAINER_PORT
  EC2_WEB_ENV_FILE
  EC2_WEB_HEALTH_TIMEOUT_SECONDS
  EC2_WEB_HEALTH_POLL_SECONDS
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Missing release variable: ${variable_name}" >&2
    exit 1
  fi
done

if [[ ! -r "${EC2_WEB_ENV_FILE}" ]]; then
  echo "Django environment file is missing or unreadable: ${EC2_WEB_ENV_FILE}" >&2
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

for command_name in aws docker; do
  if ! command -v "${command_name}" > /dev/null 2>&1; then
    echo "Required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

if ! docker network inspect "${EC2_WEB_DOCKER_NETWORK}" > /dev/null 2>&1; then
  echo "Docker network does not exist: ${EC2_WEB_DOCKER_NETWORK}" >&2
  exit 1
fi

run_web_container() {
  local container_image="$1"
  local image_tag="$2"

  docker run \
    --detach \
    --name "${EC2_WEB_CONTAINER_NAME}" \
    --restart unless-stopped \
    --network "${EC2_WEB_DOCKER_NETWORK}" \
    --env-file "${EC2_WEB_ENV_FILE}" \
    --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
    --publish "${EC2_WEB_HOST_PORT}:${EC2_WEB_CONTAINER_PORT}" \
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
docker logout "${ECR_REGISTRY}" > /dev/null 2>&1 || true

echo "Checking Django production settings."
docker run \
  --rm \
  --network "${EC2_WEB_DOCKER_NETWORK}" \
  --env-file "${EC2_WEB_ENV_FILE}" \
  --env "WEB_CONTAINER_PORT=${EC2_WEB_CONTAINER_PORT}" \
  --entrypoint python \
  "${IMAGE_URI}" \
  app/manage.py check --deploy --fail-level ERROR

echo "Applying Django migrations before replacing the running container."
docker run \
  --rm \
  --network "${EC2_WEB_DOCKER_NETWORK}" \
  --env-file "${EC2_WEB_ENV_FILE}" \
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
