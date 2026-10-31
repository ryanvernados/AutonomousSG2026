"""Privately collect an existing database password and verify Supabase before enabling it.

Run in your own terminal: uv run python -m scripts.configure_supabase
No passwords or connection strings are printed. No schema or user changes are made.
"""
import getpass
from pathlib import Path
from urllib.parse import quote

import psycopg
from dotenv import dotenv_values, set_key

PROJECT_ID = "00000000-0000-0000-0000-000000000010"
PASSWORD_PLACEHOLDER = "[YOUR-PASSWORD]"


def insert_password(template: str, password: str) -> str:
    if not password:
        raise ValueError("Database password must not be empty")
    if template.count(PASSWORD_PLACEHOLDER) != 1:
        raise ValueError("Expected one database password placeholder")
    return template.replace(PASSWORD_PLACEHOLDER, quote(password, safe=""))


def configure(env_path: Path = Path(".env"), *, connector=psycopg.connect, password_reader=None):
    values = dotenv_values(env_path)
    url = values.get("DATABASE_URL") or ""
    if not url or not values.get("SUPABASE_URL") or not values.get("SUPABASE_PUBLISHABLE_KEY"):
        raise ValueError("Set the project URL, publishable key and DATABASE_URL template in .env first")
    if PASSWORD_PLACEHOLDER in url:
        reader = password_reader or getpass.getpass
        url = insert_password(url, reader("Existing Supabase DATABASE password (hidden): "))
    with connector(url, connect_timeout=10) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        scenario = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM public.scenarios WHERE scenario_id=%s AND project_id=%s::uuid)",
            ("sfo-synthetic-v1", PROJECT_ID)).fetchone()[0]
        member = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM public.project_members WHERE project_id=%s::uuid "
            "AND role IN ('controller','admin'))", (PROJECT_ID,)).fetchone()[0]
        function = connection.execute(
            "SELECT to_regprocedure('public.commit_simulation_session(jsonb,bigint,jsonb,text,text,text,jsonb,boolean,boolean)') "
            "IS NOT NULL").fetchone()[0]
        if not all((scenario, member, function)):
            raise ValueError("The airport scenario, controller membership or commit function is missing")
    # Save only after a successful read-only connection and schema check.
    set_key(str(env_path), "DATABASE_URL", url)
    set_key(str(env_path), "AUTH_MODE", "supabase")
    print("Database connection, scenario, controller membership and commit function verified.")
    print("Saved the private connection in .env and enabled AUTH_MODE=supabase.")
    print("Restart the backend with .\\scripts\\start.ps1")


def main():
    try:
        configure()
    except (psycopg.Error, OSError):
        raise SystemExit("Connection failed. Check the existing database password and network. .env was not changed.") from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
