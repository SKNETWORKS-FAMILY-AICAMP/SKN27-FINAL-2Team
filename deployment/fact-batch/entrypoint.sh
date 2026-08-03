#!/usr/bin/env bash

set -euo pipefail

required_variables=(
  FACT_BATCH_SPEC_PATH
  FACT_BATCH_OUTPUT_DIR
  FACT_BATCH_VARIANTS_PER_PACK
  FACT_BATCH_MOCK_EXAM
  FACT_BATCH_MAX_TOTAL_CALLS
  FACT_BATCH_MAX_SECONDS
  FACT_BATCH_SEED
  FACT_BATCH_DEPENDENCY_TIMEOUT_SECONDS
  FACT_BATCH_DEPENDENCY_RETRY_INTERVAL_SECONDS
  OPENAI_API_KEY
  POSTGRES_DB
  POSTGRES_HOST
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_PORT
  FACT_NEO4J_URI
  FACT_NEO4J_USER
  FACT_NEO4J_PASSWORD
  RUNPOD_ENDPOINT_ID
  RUNPOD_API_KEY
  RUNPOD_SLLM_MODEL
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Missing Fact batch environment variable: ${variable_name}" >&2
    exit 1
  fi
done

for positive_integer_name in \
  FACT_BATCH_VARIANTS_PER_PACK \
  FACT_BATCH_MAX_TOTAL_CALLS \
  FACT_BATCH_MAX_SECONDS; do
  if [[ ! "${!positive_integer_name}" =~ ^[1-9][0-9]*$ ]]; then
    echo "${positive_integer_name} must be a positive integer." >&2
    exit 1
  fi
done

if [[ ! "${FACT_BATCH_SEED}" =~ ^[0-9]+$ ]]; then
  echo "FACT_BATCH_SEED must be a non-negative integer." >&2
  exit 1
fi

if [[ ! -f "${FACT_BATCH_SPEC_PATH}" ]]; then
  echo "Fact batch spec does not exist: ${FACT_BATCH_SPEC_PATH}" >&2
  exit 1
fi

pack_model="${OPENAI_PACK_MODEL:-${OPENAI_CHAT_MODEL:-}}"
if [[ -z "${pack_model}" ]]; then
  echo "OPENAI_PACK_MODEL or OPENAI_CHAT_MODEL is required." >&2
  exit 1
fi

umask 077
mkdir -p "${FACT_BATCH_OUTPUT_DIR}"

python /code/deployment/fact-batch/wait_for_dependencies.py

pack_bank_path="${FACT_BATCH_OUTPUT_DIR}/pack_bank.json"
question_output_directory="${FACT_BATCH_OUTPUT_DIR}/questions"
usage_manifest_path="${FACT_BATCH_USAGE_MANIFEST_PATH:-${FACT_BATCH_OUTPUT_DIR}/usage_manifest.json}"

graph_arguments=(
  --spec "${FACT_BATCH_SPEC_PATH}"
  --output "${pack_bank_path}"
  --model "${pack_model}"
)
if [[ -n "${FACT_BATCH_EXISTING_BANK_PATH:-}" ]]; then
  if [[ ! -f "${FACT_BATCH_EXISTING_BANK_PATH}" ]]; then
    echo "Existing pack bank does not exist: ${FACT_BATCH_EXISTING_BANK_PATH}" >&2
    exit 1
  fi
  graph_arguments+=(--existing-bank "${FACT_BATCH_EXISTING_BANK_PATH}")
fi

python -m ai.pack_generation.graph_builder "${graph_arguments[@]}"

generation_arguments=(
  --pack-input "${pack_bank_path}"
  --output-dir "${question_output_directory}"
  --variants-per-pack "${FACT_BATCH_VARIANTS_PER_PACK}"
  --usage-manifest "${usage_manifest_path}"
  --max-total-calls "${FACT_BATCH_MAX_TOTAL_CALLS}"
  --max-seconds "${FACT_BATCH_MAX_SECONDS}"
  --seed "${FACT_BATCH_SEED}"
)
if [[ "${FACT_BATCH_MOCK_EXAM}" == "true" ]]; then
  generation_arguments+=(--mock-exam)
elif [[ "${FACT_BATCH_MOCK_EXAM}" != "false" ]]; then
  echo "FACT_BATCH_MOCK_EXAM must be true or false." >&2
  exit 1
fi

python -m ai.question_generation.workflows.closed_pack_batch "${generation_arguments[@]}"

python - "${FACT_BATCH_OUTPUT_DIR}" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

output_directory = Path(sys.argv[1]).resolve()
files = []
for path in sorted(output_directory.rglob("*")):
    if not path.is_file() or path.name == "artifact-manifest.json":
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files.append(
        {
            "path": path.relative_to(output_directory).as_posix(),
            "sha256": digest,
            "size": path.stat().st_size,
        }
    )

manifest = {
    "status": "READY_FOR_REVIEW",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "files": files,
}
(output_directory / "artifact-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

echo "Fact batch completed. Review artifacts before importing them into the service database."
