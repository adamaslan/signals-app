#!/usr/bin/env python3
"""Database tool for the signals Supabase project: inspect, query, write, migrate.

Two routes, tried in this order unless ``--route`` says otherwise:
  api       Supabase Management API (``SUPABASE_ACCESS_TOKEN`` + project ref from
            ``SUPABASE_URL``). Can run DDL. Make a token at
            https://supabase.com/dashboard/account/tokens.
  postgres  Direct connection from ``DATABASE_URL`` (needs ``psycopg``, installed
            on demand with: mamba install -n signals-app -c conda-forge psycopg).

Credentials are read from the environment (source ``~/code/signals-app/.env``) and
never printed. Reads are the default; anything that writes needs ``--yes``.

Usage:
    python scripts/db_tool.py status
    python scripts/db_tool.py tables
    python scripts/db_tool.py query "select count(*) from detector_hits"
    python scripts/db_tool.py migrate --since 20261006 --dry-run
    python scripts/db_tool.py migrate --since 20261006 --yes
    python scripts/db_tool.py apply supabase/migrations/20261006000002_confluence_shadow.sql --yes
    python scripts/db_tool.py exec "delete from confluence_shadow" --yes
    python scripts/db_tool.py purge-shadow --yes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
READ_ONLY_START = re.compile(r"^\s*(select|with|explain|show|table|values)\b", re.IGNORECASE)
WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|call|do|into)\b",
    re.IGNORECASE,
)
LEDGER_DDL = (
    "create table if not exists applied_migrations ("
    "name text primary key, applied_at timestamptz not null default now())"
)
# Objects each recent migration creates, so `status` can say what is applied
# without a ledger (older migrations were applied by hand).
EXPECTED_OBJECTS = {
    "20261006000001_detector_hits_kind.sql": (
        "select count(*) = 3 as ok from information_schema.columns "
        "where table_name = 'detector_hits' and column_name in ('kind', 'concept', 'context')"
    ),
    "20261006000002_confluence_shadow.sql": (
        "select to_regclass('public.confluence_shadow') is not null as ok"
    ),
}


class DbError(RuntimeError):
    """A database call failed or was refused."""


class SqlRunner(Protocol):
    """Runs one SQL string and returns its rows as dicts."""

    name: str

    def run(self, sql: str) -> list[dict[str, Any]]: ...


class ApiRunner:
    """Supabase Management API: ``POST /v1/projects/{ref}/database/query``."""

    name = "api"

    def __init__(self, supabase_url: str, token: str, client: Any | None = None) -> None:
        host = urllib.parse.urlparse(supabase_url).hostname or ""
        self._ref = host.split(".")[0]
        if not self._ref or not token:
            raise DbError("api route needs SUPABASE_URL and SUPABASE_ACCESS_TOKEN")
        self._token = token
        self._client = client

    def run(self, sql: str) -> list[dict[str, Any]]:
        import httpx

        client = self._client or httpx.Client(timeout=60)
        resp = client.post(
            f"https://api.supabase.com/v1/projects/{self._ref}/database/query",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"query": sql},
        )
        if resp.status_code == 401:
            raise DbError("api route: 401 Unauthorized. SUPABASE_ACCESS_TOKEN is expired or revoked")
        if resp.status_code >= 400:
            raise DbError(f"api route: HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        return body if isinstance(body, list) else []


class PostgresRunner:
    """Direct Postgres via ``DATABASE_URL`` (psycopg)."""

    name = "postgres"

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise DbError("postgres route needs DATABASE_URL")
        self._dsn = dsn

    def run(self, sql: str) -> list[dict[str, Any]]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise DbError("postgres route needs psycopg: mamba install -n signals-app -c conda-forge psycopg") from exc
        with psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall()) if cur.description else []


def build_runner(route: str | None, env: dict[str, str] | None = None) -> SqlRunner:
    """Pick a route from the environment. Raises DbError when no credential is set."""
    env = dict(os.environ) if env is None else env
    errors: list[str] = []
    if route in (None, "api") and env.get("SUPABASE_ACCESS_TOKEN") and env.get("SUPABASE_URL"):
        return ApiRunner(env["SUPABASE_URL"], env["SUPABASE_ACCESS_TOKEN"])
    errors.append("api: SUPABASE_ACCESS_TOKEN / SUPABASE_URL not set")
    if route in (None, "postgres") and env.get("DATABASE_URL"):
        return PostgresRunner(env["DATABASE_URL"])
    errors.append("postgres: DATABASE_URL not set")
    raise DbError("no usable database route. " + "; ".join(errors))


# ------------------------------------------------------------------- operations


def is_read_only(sql: str) -> bool:
    """True when ``sql`` is a single read statement with no write keyword in it."""
    body = sql.strip().rstrip(";")
    if ";" in body or not READ_ONLY_START.match(body):
        return False
    return not WRITE_WORDS.search(re.sub(r"'[^']*'", "''", body))


def query(runner: SqlRunner, sql: str) -> list[dict[str, Any]]:
    """Run a read-only statement; refuses anything that could write."""
    if not is_read_only(sql):
        raise DbError("query is read-only; use `exec ... --yes` for statements that write")
    return runner.run(sql)


def execute(runner: SqlRunner, sql: str, yes: bool) -> list[dict[str, Any]]:
    """Run any statement. Requires ``yes`` so a write is always deliberate."""
    if not yes:
        raise DbError("refusing to write without --yes")
    return runner.run(sql)


def tables(runner: SqlRunner) -> list[dict[str, Any]]:
    """Public tables with approximate live row counts."""
    return runner.run(
        "select relname as table, n_live_tup as approx_rows from pg_stat_user_tables "
        "where schemaname = 'public' order by relname"
    )


def migration_files(since: str | None = None, only: list[str] | None = None) -> list[Path]:
    """Migration files to consider, oldest first. ``since`` is a filename prefix like 20261006."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if only:
        wanted = {Path(o).name for o in only}
        files = [f for f in files if f.name in wanted]
        missing = wanted - {f.name for f in files}
        if missing:
            raise DbError(f"not in supabase/migrations: {sorted(missing)}")
    if since:
        files = [f for f in files if f.name >= since]
    return files


def status(runner: SqlRunner) -> dict[str, Any]:
    """Which recent migrations' objects exist, and the route used."""
    applied = {}
    for name, check in EXPECTED_OBJECTS.items():
        try:
            rows = runner.run(check)
            applied[name] = bool(rows and rows[0].get("ok"))
        except DbError as exc:
            applied[name] = f"check failed: {exc}"
    return {"route": runner.name, "migrations": applied}


def apply_files(runner: SqlRunner, files: list[Path], yes: bool, dry_run: bool) -> list[str]:
    """Apply migration files in order and record them in ``applied_migrations``.

    Migrations in this repo are idempotent (``if not exists``), so re-applying one is
    safe. ``dry_run`` only lists what would run.
    """
    lines: list[str] = []
    if dry_run:
        return [f"would apply {f.name}" for f in files]
    if not yes:
        raise DbError("refusing to apply migrations without --yes (or use --dry-run)")
    runner.run(LEDGER_DDL)
    for path in files:
        runner.run(path.read_text())
        runner.run(
            "insert into applied_migrations (name) values ('" + path.name.replace("'", "''") + "') "
            "on conflict (name) do update set applied_at = now()"
        )
        lines.append(f"applied {path.name}")
    return lines


# --------------------------------------------------------------------------- CLI


def _print(rows: Any) -> None:
    print(json.dumps(rows, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--route", choices=("api", "postgres"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("tables")
    q = sub.add_parser("query")
    q.add_argument("sql")
    e = sub.add_parser("exec")
    e.add_argument("sql", nargs="?")
    e.add_argument("-f", "--file")
    e.add_argument("--yes", action="store_true")
    a = sub.add_parser("apply")
    a.add_argument("files", nargs="+")
    a.add_argument("--yes", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("migrate")
    m.add_argument("--since", required=True, help="filename prefix, e.g. 20261006")
    m.add_argument("--yes", action="store_true")
    m.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("purge-shadow")
    p.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    try:
        runner = build_runner(args.route)
        if args.cmd == "status":
            _print(status(runner))
        elif args.cmd == "tables":
            _print(tables(runner))
        elif args.cmd == "query":
            _print(query(runner, args.sql))
        elif args.cmd == "exec":
            sql = Path(args.file).read_text() if args.file else args.sql
            if not sql:
                raise DbError("give SQL or -f FILE")
            _print(execute(runner, sql, args.yes))
        elif args.cmd == "apply":
            print("\n".join(apply_files(runner, migration_files(only=args.files), args.yes, args.dry_run)))
        elif args.cmd == "migrate":
            print("\n".join(apply_files(runner, migration_files(since=args.since), args.yes, args.dry_run)))
        elif args.cmd == "purge-shadow":
            _print(execute(runner, "delete from confluence_shadow", args.yes))
    except DbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
