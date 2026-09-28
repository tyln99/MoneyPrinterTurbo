"""
PostgreSQL connection pool and schema migrations.

This module knows nothing about tasks, episodes or scenes; it only hands out
connections and brings the schema up to date. Everything domain-specific lives
in the modules that import it.

Two deliberate differences from the way `state.py` wires itself up:

  * Nothing connects at import. `state.py` builds its singleton while the module
    is being imported, which is fine for an in-memory dict but would mean
    `python cli.py --help` opens a TCP connection, tests need a live database to
    import anything, and the WebUI container dies when it starts before
    Postgres is healthy.
  * The DSN is read when the pool is first used, not when this module is
    imported. Config section globals are bound once at import
    (`app/config/config.py:550`), so reading at call time is what lets tests
    patch `config.app` and what lets a WebUI settings edit take effect.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import config

# Matches the docker-compose service so a config.toml written before this
# feature existed still works without being edited.
DEFAULT_DSN = (
    "postgresql://moneyprinterturbo:moneyprinterturbo@localhost:5432/moneyprinterturbo"
)

# Any constant works; it only has to be the same in every process that runs
# migrations. Derived from nothing in particular, just fixed.
_MIGRATION_LOCK_ID = 8145023

MIGRATIONS_DIR = Path(__file__).parent / "db_migrations"

_pool: ConnectionPool | None = None
_migrated = False
_lock = threading.RLock()


def resolve_dsn() -> str:
    """Read the connection string, falling back to the compose default."""
    return str(config.app.get("database_url", "") or DEFAULT_DSN).strip()


def get_pool() -> ConnectionPool:
    """
    Return the process-wide pool, creating it on first use.

    Creating the pool deliberately does not run migrations: `run_migrations`
    needs a pool itself, and having each call the other means the schema gets
    applied by the inner call while the outer one reports that there was
    nothing to do.

    `min_size=0` keeps this cheap and non-blocking, so a misconfigured DSN
    surfaces at the first query rather than at import.
    """
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=resolve_dsn(),
                min_size=0,
                max_size=int(config.app.get("database_pool_size", 8) or 8),
                kwargs={"row_factory": dict_row},
                open=True,
            )
        return _pool


def ensure_migrated() -> None:
    """Bring the schema up to date once per process."""
    global _migrated
    with _lock:
        if not _migrated:
            run_migrations(get_pool())
            _migrated = True


@contextmanager
def connection():
    """Borrow a connection from the pool, migrating the schema on first use."""
    ensure_migrated()
    with get_pool().connection() as conn:
        yield conn


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)


def run_migrations(pool: ConnectionPool | None = None) -> list[str]:
    """
    Apply every migration that has not been applied yet, and return their names.

    Serialised with an advisory lock because `docker-compose` starts the webui
    and api services at the same moment and both reach this code within the same
    second. Each file runs inside its own transaction together with the insert
    that records it, so a file is either fully applied or not applied at all.
    """
    pool = pool or get_pool()
    applied: list[str] = []

    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            conn.commit()

            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            done = {row["version"] for row in rows}

            for path in _migration_files():
                if path.name in done:
                    continue
                logger.info(f"applying database migration: {path.name}")
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (path.name,),
                )
                conn.commit()
                applied.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
            conn.commit()

    return applied


def reset_pool() -> None:
    """Close the pool so the next call rebuilds it. For tests and DSN changes."""
    global _pool, _migrated
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _migrated = False
