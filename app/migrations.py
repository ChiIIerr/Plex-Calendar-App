"""Compatibility identifiers and a lossless database upgrade for earlier installations."""
import json
from contextlib import closing
import os
import sqlite3
import tempfile
import time
from pathlib import Path

PREVIOUS_NAME = json.loads(Path(__file__).with_name("legacy.json").read_text())["previous_name"]


def migrate_database(directory, destination):
    previous = directory / f"{PREVIOUS_NAME.lower()}.sqlite3"
    if destination.exists() or not previous.exists():
        return
    if not (directory / "encryption.key").exists():
        raise RuntimeError("Existing data requires its encryption.key. Restore the original key before upgrading.")
    # SQLite's backup API includes committed WAL records. A simple file copy would lose them.
    handle, temporary = tempfile.mkstemp(prefix=".calendarr-upgrade-", suffix=".sqlite3", dir=directory)
    os.close(handle)
    temporary = Path(temporary)
    deadline = time.monotonic() + 30

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise RuntimeError("Database upgrade timed out. Stop the previous server and try again.")

    try:
        with closing(sqlite3.connect(previous.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target, pages=256, progress=progress)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    # Retain the old database as a pre-upgrade backup; subsequent starts use the new file.
