FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WEB_CONTAINER_PORT=8000

WORKDIR /code

COPY requirements/ ./requirements/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements/prod.txt

COPY . .

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && chmod +x /code/docker/entrypoint.sh \
    && chown -R app:app /code

EXPOSE 8000

USER app

ENTRYPOINT ["/code/docker/entrypoint.sh"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD python -c "import os, urllib.request; request = urllib.request.Request(f\"http://127.0.0.1:{os.environ['WEB_CONTAINER_PORT']}/health/\", headers={\"Host\": os.environ['DJANGO_HEALTHCHECK_HOST']}); urllib.request.urlopen(request, timeout=4)"

CMD ["gunicorn", "--chdir", "app", "config.wsgi:application", "--access-logfile", "-", "--error-logfile", "-"]
