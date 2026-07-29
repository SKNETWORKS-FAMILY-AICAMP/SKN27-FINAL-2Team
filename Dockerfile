FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV WEB_CONTAINER_PORT=8000
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /code

COPY requirements/base.txt requirements/prod.txt ./requirements/

RUN apt-get update \
    && apt-get upgrade --yes \
    && apt-get install --yes --no-install-recommends \
      adduser \
      ca-certificates \
      python3 \
      python3-venv \
    && python3 -m venv /opt/venv \
    && python -m pip install --no-cache-dir --upgrade pip==26.1.2 \
    && python -m pip install --no-cache-dir -r requirements/prod.txt \
    && apt-get purge --yes \
      python3-pip-whl \
      python3-setuptools-whl \
      python3-venv \
      python3.12-venv \
    && apt-get autoremove --purge --yes \
    && rm -rf /var/lib/apt/lists/*

COPY docker/certs/aws-rds-global-bundle.pem /etc/ssl/certs/aws-rds-global-bundle.pem

RUN chmod 0444 /etc/ssl/certs/aws-rds-global-bundle.pem

COPY . .

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && chmod 0555 /code/docker/entrypoint.sh \
    && DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
      POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
      python app/manage.py collectstatic --noinput

EXPOSE 8000

USER app

ENTRYPOINT ["/code/docker/entrypoint.sh"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD python -c "import os, urllib.request; request = urllib.request.Request(f\"http://127.0.0.1:{os.environ['WEB_CONTAINER_PORT']}/health/live/\", headers={\"Host\": os.environ['DJANGO_HEALTHCHECK_HOST']}); urllib.request.urlopen(request, timeout=4)"

CMD ["gunicorn", "--chdir", "app", "config.wsgi:application", "--access-logfile", "-", "--error-logfile", "-"]
