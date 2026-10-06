"""CI-only persistent legacy installation fixture and post-upgrade login check."""
import argparse
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import http.cookiejar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.migrations import PREVIOUS_NAME
from app.security import password_hash
from app.store import DEFAULT_SETTINGS, Store

PASSWORD = 'disposable-ci-upgrade-password-42'


def main():
    if os.environ.get('CI') != 'true':
        raise SystemExit('This fixture requires disposable CI state.')
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'verify'])
    parser.add_argument('state', type=Path)
    args = parser.parse_args()
    data = args.state / 'data'
    if args.command == 'seed':
        store = Store(data)
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings['preferences']['app_name'] = PREVIOUS_NAME
        settings['sonarr']['api_key'] = 'disposable-ci-upgrade-api-key'
        store.save_settings(settings)
        with store.connection() as db:
            db.execute('INSERT INTO users(username,password,role,created) VALUES (?,?,?,?)', ('upgrade-admin', password_hash(PASSWORD), 'admin', time.time()))
            db.execute('INSERT INTO activity(source,title,kind,action,happened,external_id) VALUES (?,?,?,?,?,?)', ('sonarr', 'Upgrade history', 'show', 'added', '2026-10-01', 1))
        (data / 'setup-token').unlink()
        store.path.rename(data / (PREVIOUS_NAME.lower() + '.sqlite3'))
    else:
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        config = json.loads((args.state / 'server.json').read_text(encoding='utf-8-sig'))
        base = f'http://127.0.0.1:{config["port"]}'
        def request(path, payload=None):
            body = json.dumps(payload).encode() if payload else None
            headers = {'Content-Type': 'application/json', 'X-Calendarr-Request': '1'}
            with browser.open(urllib.request.Request(base + path, data=body, headers=headers), timeout=5) as response:
                return json.load(response)
        bootstrap = request('/api/bootstrap')
        assert not bootstrap['setup_required']
        assert bootstrap['app_name'] == 'Calendarr'
        request('/api/auth/login', {'username': 'upgrade-admin', 'password': PASSWORD})
        assert request('/api/session')['user']['role'] == 'admin'
        assert request('/api/activity')['items'][0]['title'] == 'Upgrade history'
        assert request('/api/settings')['settings']['sonarr']['has_api_key']
        assert Store(data).settings()['sonarr']['api_key'] == 'disposable-ci-upgrade-api-key'
        assert (data / 'calendarr.sqlite3').exists()
        assert (data / (PREVIOUS_NAME.lower() + '.sqlite3')).exists()
        print('Previous administrator login, encrypted credentials, history, and display name survived the upgrade.')


if __name__ == '__main__':
    main()
