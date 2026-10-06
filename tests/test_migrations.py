"""Renaming persistent files must retain credentials, accounts, and committed WAL records."""
import copy
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.migrations import PREVIOUS_NAME
from app.security import password_hash
from app.store import DEFAULT_SETTINGS, Store

PASSWORD = 'migration-test-password-42'


def previous_installation(directory, app_name=PREVIOUS_NAME):
    store = Store(directory)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings['preferences']['app_name'] = app_name
    settings['sonarr']['api_key'] = 'preserved-test-api-key'
    store.save_settings(settings)
    with store.connection() as db:
        db.execute('INSERT INTO users(username,password,role,created) VALUES (?,?,?,?)', ('admin', password_hash(PASSWORD), 'admin', time.time()))
        db.execute('INSERT INTO activity(source,title,kind,action,happened,external_id) VALUES (?,?,?,?,?,?)', ('sonarr', 'Preserved title', 'show', 'added', '2026-10-01', 1))
    (directory / 'setup-token').unlink()
    old = directory / f'{PREVIOUS_NAME.lower()}.sqlite3'
    store.path.rename(old)
    return old, (directory / 'encryption.key').read_bytes()


def test_upgrade_retains_login_secret_history_and_original_backup(tmp_path):
    old, key = previous_installation(tmp_path)
    app = create_app(tmp_path, background=False)
    with TestClient(app) as browser:
        bootstrap = browser.get('/api/bootstrap').json()
        assert not bootstrap['setup_required']
        assert bootstrap['app_name'] == 'Calendarr'
        login = browser.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD}, headers={'X-Calendarr-Request': '1'})
        assert login.status_code == 200
        assert 'calendarr_session' in login.headers['set-cookie']
        assert browser.get('/api/session').json()['user']['role'] == 'admin'
        assert browser.get('/api/activity').json()['items'][0]['title'] == 'Preserved title'
    assert app.state.store.settings()['sonarr']['api_key'] == 'preserved-test-api-key'
    assert app.state.store.path.name == 'calendarr.sqlite3'
    assert old.exists()
    assert (tmp_path / 'encryption.key').read_bytes() == key
    assert not (tmp_path / 'setup-token').exists()


def test_upgrade_includes_committed_wal_and_is_idempotent(tmp_path):
    old, _ = previous_installation(tmp_path)
    connection = sqlite3.connect(old)
    try:
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("INSERT INTO cache VALUES ('wal-record','42',123)")
        connection.commit()
        assert old.with_name(old.name + '-wal').exists()
        upgraded = Store(tmp_path)
        assert upgraded.cache('wal-record') == 42
        upgraded.set_cache('after-upgrade', 7)
        assert Store(tmp_path).cache('after-upgrade') == 7
    finally:
        connection.close()


def test_upgrade_preserves_custom_application_name(tmp_path):
    previous_installation(tmp_path, 'Our Family Calendar')
    assert Store(tmp_path).settings()['preferences']['app_name'] == 'Our Family Calendar'


def test_missing_key_never_creates_empty_replacement_database(tmp_path):
    previous_installation(tmp_path)
    (tmp_path / 'encryption.key').unlink()
    with pytest.raises(RuntimeError, match='encryption.key'):
        Store(tmp_path)
    assert not (tmp_path / 'calendarr.sqlite3').exists()
    assert not (tmp_path / 'encryption.key').exists()
