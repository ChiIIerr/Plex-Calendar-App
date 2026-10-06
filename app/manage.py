"""Offline administrator recovery. Requires filesystem access to this server's data."""
import argparse
import getpass
import os
from pathlib import Path

from .security import password_hash
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Recover a local Reelarr administrator account")
    parser.add_argument("command", choices=["reset-admin"])
    parser.add_argument("username")
    parser.add_argument("--config", type=Path, help="Use the installed server's data directory")
    args = parser.parse_args()
    if args.config:
        from .server import apply_environment, load_config
        try:
            apply_environment(load_config(args.config))
        except ValueError as error:
            parser.error(str(error))
    store = Store(Path(os.environ.get("DATA_DIR", "data")))
    with store.connection() as db:
        row = db.execute("SELECT * FROM users WHERE username=? AND role='admin' AND provider='local'", (args.username,)).fetchone()
    if not row:
        parser.error("No local administrator exists with that username.")
    password = getpass.getpass("New administrator password (at least 12 characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if len(password) < 12 or len(password) > 512 or password != confirm:
        parser.error("Passwords must match and contain between 12 and 512 characters.")
    with store.connection() as db:
        db.execute("UPDATE users SET password=?,enabled=1,approved=1 WHERE id=?", (password_hash(password), row["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    print("Administrator password reset. Existing sessions were signed out.")


if __name__ == "__main__":
    main()
