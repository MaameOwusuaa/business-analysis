"""Create the local MySQL account used by the web app and update ``.env``.

Run from the project root with:

    python scripts/setup_mysql_access.py

The MySQL administrator password is requested through a hidden terminal prompt
and is never written to disk. The application password is generated locally,
stored in ``.env``, and never printed.
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

import mysql.connector
from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
APP_HOST = "localhost"
DATABASE_URL_KEY = "DATABASE_URL"
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def replace_database_url(path: Path, database_url: str) -> None:
    """Replace the DATABASE_URL line while preserving the rest of ``.env``."""
    original = path.read_text() if path.exists() else ""
    lines = original.splitlines(keepends=True)
    updated = []
    replaced = False

    for line in lines:
        if line.lstrip().startswith(f"{DATABASE_URL_KEY}=") or line.lstrip().startswith(
            f"{DATABASE_URL_KEY} ="
        ):
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            updated.append(f"{DATABASE_URL_KEY}={database_url}{newline}")
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        if updated and not updated[-1].endswith(("\n", "\r")):
            updated[-1] += "\n"
        updated.append(f"{DATABASE_URL_KEY}={database_url}\n")

    # The generated DATABASE_URL contains a credential; keep it owner-readable.
    mode = 0o600
    fd, temporary_name = tempfile.mkstemp(prefix=".env-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as output:
            output.writelines(updated)
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    if not ENV_FILE.exists():
        print("No .env file found. Create it first, then run this helper again.")
        return 1

    values = dotenv_values(ENV_FILE)
    configured_url = values.get(DATABASE_URL_KEY)
    if not configured_url:
        print("DATABASE_URL is missing from .env.")
        return 1

    try:
        current_url = make_url(configured_url)
    except Exception:
        print("DATABASE_URL in .env is not a valid SQLAlchemy URL.")
        return 1

    if current_url.get_backend_name() not in {"mysql", "mariadb"}:
        print("DATABASE_URL must point to MySQL or MariaDB before using this helper.")
        return 1

    database = current_url.database
    if not database or not IDENTIFIER_RE.fullmatch(database):
        print("The configured database name must contain only letters, numbers, '_' or '$'.")
        return 1

    app_username = current_url.username
    if not app_username or not USERNAME_RE.fullmatch(app_username):
        print("The configured MySQL username must contain only letters, numbers or '_'.")
        return 1

    host = current_url.host or "localhost"
    port = current_url.port or 3306
    admin_username = input("MySQL administrator username [root]: ").strip() or "root"
    admin_password = getpass.getpass("MySQL administrator password: ")
    app_password = secrets.token_urlsafe(32)

    print(
        f"This will reset the password for the existing MySQL account '{app_username}' "
        f"and grant it access to the existing database '{database}'."
    )
    print("It will not create a new MySQL account or database.")
    if input("Continue? [y/N]: ").strip().lower() not in {"y", "yes"}:
        print("No changes made.")
        return 1

    connection = None
    try:
        connection = mysql.connector.connect(
            host=host,
            port=port,
            user=admin_username,
            password=admin_password,
            connection_timeout=8,
        )
        cursor = connection.cursor()
        cursor.execute(
            "SELECT Host FROM mysql.user WHERE User = %s AND Host IN "
            "('localhost', '127.0.0.1', '%')",
            (app_username,),
        )
        existing_hosts = {row[0] for row in cursor.fetchall()}
        account_host = next(
            (candidate for candidate in (APP_HOST, "127.0.0.1", "%") if candidate in existing_hosts),
            None,
        )
        if account_host is None:
            raise RuntimeError(
                f"The existing account '{app_username}' has no local MySQL host entry."
            )

        cursor.execute(
            "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
            (database,),
        )
        if cursor.fetchone() is None:
            raise RuntimeError(f"The existing database '{database}' was not found.")

        cursor.execute(
            f"ALTER USER '{app_username}'@'{account_host}' IDENTIFIED BY %s",
            (app_password,),
        )
        cursor.execute(
            f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{app_username}'@'{account_host}'"
        )
        cursor.close()
        connection.close()
        connection = None

        app_connection = mysql.connector.connect(
            host=host,
            port=port,
            user=app_username,
            password=app_password,
            database=database,
            connection_timeout=8,
        )
        app_connection.close()

        app_url = URL.create(
            drivername="mysql+mysqlconnector",
            username=app_username,
            password=app_password,
            host=host,
            port=port,
            database=database,
            query=current_url.query,
        )
        replace_database_url(ENV_FILE, str(app_url))
        print(f"Existing MySQL account '{app_username}' is ready; .env has been updated.")
        print("You can now start the backend with: uvicorn api.main:app --reload")
        return 0
    except Exception as error:
        message = str(error).replace(admin_password, "[redacted]").replace(app_password, "[redacted]")
        print(f"MySQL setup failed ({type(error).__name__}): {message}", file=sys.stderr)
        return 1
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
