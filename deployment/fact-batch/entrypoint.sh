#!/usr/bin/env bash

set -euo pipefail

required_variables=(
  FACT_BATCH_OUTPUT_DIR
  FACT_BATCH_SPEC_PATH
  FACT_BATCH_EXISTING_BANK_PATH
  FACT_BATCH_PACKS_PER_RUN
  FACT_BATCH_VARIANTS_PER_PACK
  FACT_BATCH_MOCK_EXAM
  FACT_BATCH_MAX_TOTAL_CALLS
  FACT_BATCH_MAX_SECONDS
  FACT_BATCH_SEED
  FACT_BATCH_DEPENDENCY_TIMEOUT_SECONDS
  FACT_BATCH_DEPENDENCY_RETRY_INTERVAL_SECONDS
  OPENAI_API_KEY
  OPENAI_CHAT_MODEL
  POSTGRES_DB
  POSTGRES_HOST
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_PORT
  POSTGRES_CONNECT_TIMEOUT_SECONDS
  POSTGRES_SSLMODE
  POSTGRES_SSLROOTCERT
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
  FACT_BATCH_PACKS_PER_RUN \
  FACT_BATCH_VARIANTS_PER_PACK \
  FACT_BATCH_MAX_TOTAL_CALLS \
  FACT_BATCH_MAX_SECONDS; do
  if [[ ! "${!positive_integer_name}" =~ ^[1-9][0-9]*$ ]]; then
    echo "${positive_integer_name} must be a positive integer." >&2
    exit 1
  fi
done

if (( FACT_BATCH_PACKS_PER_RUN > 5 )); then
  echo "FACT_BATCH_PACKS_PER_RUN must not exceed 5." >&2
  exit 1
fi

if [[ ! "${FACT_BATCH_SEED}" =~ ^[0-9]+$ ]]; then
  echo "FACT_BATCH_SEED must be a non-negative integer." >&2
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

selected_spec_path="${FACT_BATCH_OUTPUT_DIR}/selected_specs.json"
selection_manifest_path="${FACT_BATCH_OUTPUT_DIR}/spec-selection-manifest.json"
pack_bank_path="${FACT_BATCH_OUTPUT_DIR}/new_pack_bank.json"
cumulative_pack_bank_path="${FACT_BATCH_OUTPUT_DIR}/cumulative_pack_bank.json"
question_output_directory="${FACT_BATCH_OUTPUT_DIR}/questions"
usage_manifest_path="${FACT_BATCH_USAGE_MANIFEST_PATH:-${FACT_BATCH_OUTPUT_DIR}/usage_manifest.json}"

if [[ ! -f "${FACT_BATCH_EXISTING_BANK_PATH}" ]]; then
  echo "Existing pack bank does not exist: ${FACT_BATCH_EXISTING_BANK_PATH}" >&2
  exit 1
fi

if [[ ! -f "${FACT_BATCH_SPEC_PATH}" ]]; then
  echo "Approved Fact batch spec does not exist: ${FACT_BATCH_SPEC_PATH}" >&2
  exit 1
fi

python -m ai.pack_generation.batch_constraints select \
  --approved-specs "${FACT_BATCH_SPEC_PATH}" \
  --existing-bank "${FACT_BATCH_EXISTING_BANK_PATH}" \
  --output "${selected_spec_path}" \
  --manifest "${selection_manifest_path}" \
  --packs-per-run "${FACT_BATCH_PACKS_PER_RUN}" \
  --maximum-packs-per-run 5

graph_arguments=(
  --spec "${selected_spec_path}"
  --output "${pack_bank_path}"
  --model "${pack_model}"
  --existing-bank "${FACT_BATCH_EXISTING_BANK_PATH}"
)

python -m ai.pack_generation.graph_builder "${graph_arguments[@]}"

generation_arguments=(
  --pack-input "${pack_bank_path}"
  --output-dir "${question_output_directory}"
  --variants-per-pack "${FACT_BATCH_VARIANTS_PER_PACK}"
  --usage-manifest "${usage_manifest_path}"
  --max-total-calls "${FACT_BATCH_MAX_TOTAL_CALLS}"
  --max-seconds "${FACT_BATCH_MAX_SECONDS}"
  --seed "${FACT_BATCH_SEED}"
  --evaluate
  --eval-model "${OPENAI_CHAT_MODEL}"
)
if [[ "${FACT_BATCH_MOCK_EXAM}" == "true" ]]; then
  generation_arguments+=(--mock-exam)
elif [[ "${FACT_BATCH_MOCK_EXAM}" != "false" ]]; then
  echo "FACT_BATCH_MOCK_EXAM must be true or false." >&2
  exit 1
fi

python -m ai.question_generation.workflows.closed_pack_batch "${generation_arguments[@]}"

questions_for_import_path="${FACT_BATCH_OUTPUT_DIR}/questions-for-import.json"
classifications_path="${FACT_BATCH_OUTPUT_DIR}/service-classifications.jsonl"
explanations_path="${FACT_BATCH_OUTPUT_DIR}/choice-explanations.jsonl"
db_validation_path="${FACT_BATCH_OUTPUT_DIR}/db-import-validation.json"
db_import_result_path="${FACT_BATCH_OUTPUT_DIR}/db-import-result.json"

python -m ai.question_generation.postprocess_questions prepare-batch-import \
  --summary "${question_output_directory}/summary.json" \
  --questions-output "${questions_for_import_path}" \
  --classifications-output "${classifications_path}"

python -m ai.question_generation.postprocess_questions explain \
  --input "${questions_for_import_path}" \
  --output "${explanations_path}" \
  --model "${OPENAI_CHAT_MODEL}"

python -m ai.question_generation.postprocess_questions import-db \
  --input "${questions_for_import_path}" \
  --explanations "${explanations_path}" \
  --classifications "${classifications_path}" \
  --dry-run \
  --result-output "${db_validation_path}"

python -m ai.question_generation.postprocess_questions import-db \
  --input "${questions_for_import_path}" \
  --explanations "${explanations_path}" \
  --classifications "${classifications_path}" \
  --result-output "${db_import_result_path}"

python -m ai.pack_generation.batch_constraints merge \
  --existing-bank "${FACT_BATCH_EXISTING_BANK_PATH}" \
  --new-bank "${pack_bank_path}" \
  --selection-manifest "${selection_manifest_path}" \
  --output "${cumulative_pack_bank_path}"

python - "${FACT_BATCH_OUTPUT_DIR}" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

output_directory = Path(sys.argv[1]).resolve()
selection_manifest = json.loads(
    (output_directory / "spec-selection-manifest.json").read_text(encoding="utf-8")
)
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
    "status": "IMPORTED",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "selected_spec_ids": selection_manifest["selected_spec_ids"],
    "remaining_unused_spec_count": selection_manifest["remaining_unused_spec_count"],
    "files": files,
}
(output_directory / "artifact-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY

echo "Fact batch completed with approved specs and imported validated questions into the service database."
