"""Shared PostgreSQL connection factory."""

import os

import psycopg2
from psycopg2.extensions import connection


def connect_db() -> connection:
    settings = {
        "host": os.getenv("POSTGRES_HOST"),
        "dbname": os.getenv("POSTGRES_DB"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "port": os.getenv("POSTGRES_PORT"),
        "connect_timeout": os.getenv("POSTGRES_CONNECT_TIMEOUT_SECONDS"),
        "sslmode": os.getenv("POSTGRES_SSLMODE"),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise ValueError("Missing PostgreSQL connection settings: " + ", ".join(missing))

    ssl_root_certificate = os.getenv("POSTGRES_SSLROOTCERT")
    if settings["sslmode"] in {"verify-ca", "verify-full"} and not ssl_root_certificate:
        raise ValueError("POSTGRES_SSLROOTCERT is required for verified PostgreSQL TLS")

    connection_arguments = {
        **settings,
        "port": int(str(settings["port"])),
        "connect_timeout": int(str(settings["connect_timeout"])),
    }
    if ssl_root_certificate:
        connection_arguments["sslrootcert"] = ssl_root_certificate

    return psycopg2.connect(**connection_arguments)
