FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG DEBIAN_FRONTEND=noninteractive
ARG BATCH_UID=10001
ARG BATCH_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/code

WORKDIR /code

COPY requirements/fact-batch.txt ./requirements/fact-batch.txt

RUN apt-get update \
    && apt-get upgrade --yes \
    && apt-get install --yes --no-install-recommends \
      ca-certificates \
      python3 \
      python3-venv \
    && python3 -m venv /opt/venv \
    && python -m pip install --no-cache-dir --upgrade pip==26.1.2 \
    && python -m pip install --no-cache-dir -r requirements/fact-batch.txt \
    && apt-get purge --yes \
      python3-pip-whl \
      python3-setuptools-whl \
      python3-venv \
      python3.12-venv \
    && apt-get autoremove --purge --yes \
    && rm -rf /var/lib/apt/lists/*

COPY docker/certs/aws-rds-global-bundle.pem /etc/ssl/certs/aws-rds-global-bundle.pem
COPY ai/__init__.py ./ai/__init__.py
COPY ai/pack_generation ./ai/pack_generation
COPY ai/question_generation ./ai/question_generation
COPY storage/__init__.py ./storage/__init__.py
COPY storage/fact_neo4j/__init__.py ./storage/fact_neo4j/__init__.py
COPY storage/fact_neo4j/load_fact_graph.py ./storage/fact_neo4j/load_fact_graph.py
COPY storage/postgresql/__init__.py ./storage/postgresql/__init__.py
COPY storage/postgresql/connection.py ./storage/postgresql/connection.py
COPY docs/hanneung_sllm_eval_rubric_v1_8.md ./docs/hanneung_sllm_eval_rubric_v1_8.md
COPY deployment/fact-batch/entrypoint.sh ./deployment/fact-batch/entrypoint.sh
COPY deployment/fact-batch/wait_for_dependencies.py ./deployment/fact-batch/wait_for_dependencies.py

RUN groupadd --gid "${BATCH_GID}" batch \
    && useradd --uid "${BATCH_UID}" --gid "${BATCH_GID}" --no-create-home --shell /usr/sbin/nologin batch \
    && chmod 0444 /etc/ssl/certs/aws-rds-global-bundle.pem \
    && chmod 0555 /code/deployment/fact-batch/entrypoint.sh \
    && find /code -type d -exec chmod 0555 {} + \
    && find /code -type f ! -path '/code/deployment/fact-batch/entrypoint.sh' -exec chmod 0444 {} +

USER batch

ENTRYPOINT ["/code/deployment/fact-batch/entrypoint.sh"]
