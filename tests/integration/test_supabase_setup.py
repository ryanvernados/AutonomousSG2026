from urllib.parse import unquote, urlsplit

import psycopg
import pytest

from scripts.configure_supabase import configure, insert_password


def test_private_setup_encodes_password_characters():
    password = "private @:/?#%$' value"
    url = insert_password("postgresql://postgres.ref:[YOUR-PASSWORD]@db.example:5432/postgres?sslmode=require", password)
    assert unquote(urlsplit(url).password) == password
    assert urlsplit(url).hostname == "db.example"
    assert password not in url


def test_failed_setup_keeps_existing_configuration(tmp_path, capsys):
    env_path = tmp_path / ".env"
    content = ("AUTH_MODE=demo\nSUPABASE_URL=https://ref.supabase.co\nSUPABASE_PUBLISHABLE_KEY=public-test\n"
               "DATABASE_URL=postgresql://postgres.ref:[YOUR-PASSWORD]@db.example:5432/postgres\n")
    env_path.write_text(content)

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("Connection unavailable")

    with pytest.raises(psycopg.OperationalError):
        configure(env_path, connector=unavailable, password_reader=lambda _: "private-test-password")
    assert env_path.read_text() == content
    assert "private-test-password" not in capsys.readouterr().out
