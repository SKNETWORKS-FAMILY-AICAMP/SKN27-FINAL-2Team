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
  EC2_WEB_HOST_PORT
  EC2_WEB_CONTAINER_PORT
  EC2_WEB_ENV_FILE
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

for command_name in aws docker; do
  if ! command -v "${command_name}" > /dev/null 2>&1; then
    echo "Required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

aws ecr get-login-password \
  --region "${AWS_REGION}" \
  | docker login \
    --username AWS \
    --password-stdin \
    "${ECR_REGISTRY}"

docker pull "${IMAGE_URI}"
docker logout "${ECR_REGISTRY}" > /dev/null 2>&1 || true

previous_image=""
if docker container inspect "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1; then
  previous_image="$(
    docker container inspect \
      --format "{{.Config.Image}}" \
      "${EC2_WEB_CONTAINER_NAME}"
  )"
  docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null
fi

new_container_started="false"
if docker run \
  --detach \
  --name "${EC2_WEB_CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${EC2_WEB_ENV_FILE}" \
  --publish "${EC2_WEB_HOST_PORT}:${EC2_WEB_CONTAINER_PORT}" \
  --label "himate.git-sha=${IMAGE_TAG}" \
  "${IMAGE_URI}" \
  > /dev/null; then
  new_container_started="true"
elif [[ -n "${previous_image}" ]]; then
  echo "New container failed to start. Restoring previous image." >&2
  docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1 || true
  docker run \
    --detach \
    --name "${EC2_WEB_CONTAINER_NAME}" \
    --restart unless-stopped \
    --env-file "${EC2_WEB_ENV_FILE}" \
    --publish "${EC2_WEB_HOST_PORT}:${EC2_WEB_CONTAINER_PORT}" \
    "${previous_image}" \
    > /dev/null
elif [[ -z "${previous_image}" ]]; then
  docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1 || true
fi

if [[ "${new_container_started}" != "true" ]]; then
  exit 1
fi

sleep 3

container_state="$(
  docker container inspect \
    --format "{{.State.Status}}" \
    "${EC2_WEB_CONTAINER_NAME}"
)"

if [[ "${container_state}" == "running" ]]; then
  echo "Deployed ${IMAGE_URI} as ${EC2_WEB_CONTAINER_NAME}."
  exit 0
fi

echo "New container stopped after startup. Restoring previous image." >&2
docker rm --force "${EC2_WEB_CONTAINER_NAME}" > /dev/null 2>&1 || true

if [[ -n "${previous_image}" ]]; then
  docker run \
    --detach \
    --name "${EC2_WEB_CONTAINER_NAME}" \
    --restart unless-stopped \
    --env-file "${EC2_WEB_ENV_FILE}" \
    --publish "${EC2_WEB_HOST_PORT}:${EC2_WEB_CONTAINER_PORT}" \
    "${previous_image}" \
    > /dev/null
fi

exit 1
