from __future__ import annotations

import os


def database_url() -> str | None:
    return os.environ.get("CAREOPS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def using_postgres() -> bool:
    url = database_url()
    return bool(url and url.startswith(("postgres://", "postgresql://")))
