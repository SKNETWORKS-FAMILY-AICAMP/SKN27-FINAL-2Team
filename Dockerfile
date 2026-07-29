FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

COPY requirements/ ./requirements/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements/prod.txt

COPY . .

RUN chmod +x /code/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/code/docker/entrypoint.sh"]

CMD ["gunicorn", "--chdir", "app", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-"]
