"""SQLite persistence and encrypted integration credentials."""
import contextlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet

from .migrations import PREVIOUS_NAME, migrate_database


DEFAULT_SETTINGS = {
    "sonarr": {"url": "", "api_key": "", "enabled": False},
    "radarr": {"url": "", "api_key": "", "enabled": False},
    "plex": {"url": "", "token": "", "enabled": False},
    "qbittorrent": {"url": "", "username": "", "password": "", "enabled": False},
    "preferences": {"poll_seconds": 60, "plex_poll_seconds": 300, "plex_login": False,
                    "plex_approval": False, "app_name": "Calendarr"},
}


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.path = directory / "calendarr.sqlite3"
        migrate_database(directory, self.path)
        key_path = directory / "encryption.key"
        if not key_path.exists():
            self.private_file(key_path, Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
                    password TEXT, role TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'local',
                    plex_id TEXT UNIQUE, plex_token TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                    approved INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf TEXT NOT NULL, expires REAL NOT NULL, access_checked REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS monitored (
                    source TEXT NOT NULL, external_id INTEGER NOT NULL, title TEXT NOT NULL,
                    kind TEXT NOT NULL, monitored INTEGER NOT NULL, added TEXT,
                    PRIMARY KEY(source, external_id));
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
                    kind TEXT NOT NULL, action TEXT NOT NULL, happened TEXT NOT NULL,
                    external_id INTEGER NOT NULL);
            """)
        os.chmod(self.path, 0o600)
        if not self.get_setting_record():
            self.save_settings(DEFAULT_SETTINGS)
        else:
            settings = self.settings()
            if settings.get("preferences", {}).get("app_name") == PREVIOUS_NAME:
                settings["preferences"]["app_name"] = "Calendarr"
                self.save_settings(settings)
        setup = directory / "setup-token"
        if not self.has_admin() and not setup.exists():
            self.private_file(setup, secrets.token_urlsafe(32).encode())

    @staticmethod
    def private_file(path, content):
        with open(path, "xb") as handle:
            os.chmod(path, 0o600)
            handle.write(content)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def get_setting_record(self):
        with self.connection() as db:
            return db.execute("SELECT value FROM settings WHERE id=1").fetchone()

    def settings(self):
        return self.decrypt(self.get_setting_record()["value"])

    def save_settings(self, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (self.encrypt(value),))

    def has_admin(self):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM users WHERE role='admin' AND provider='local'").fetchone())

    def cache(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_cache(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), time.time()))

    def activity(self, limit=200):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM activity ORDER BY happened DESC,id DESC LIMIT ?", (limit,))]
