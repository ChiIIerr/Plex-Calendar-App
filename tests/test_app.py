import asyncio
import copy
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations import Clients, IntegrationError, external_guids
from app.main import create_app
from app.security import password_hash, password_valid
from app.store import DEFAULT_SETTINGS, Store
from app.sync import SyncService, decorate, download_state, release_date

HEADERS = {"X-Reelarr-Request": "1"}
PASSWORD = "a-strong-test-password-42"


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, demo=False, background=False)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def setup(client, app):
    token = (app.state.store.directory / "setup-token").read_text()
    response = client.post("/api/auth/setup", headers=HEADERS,
                           json={"token": token, "username": "admin", "password": PASSWORD})
    assert response.status_code == 200
    return {**HEADERS, "X-CSRF-Token": response.json()["csrf"]}


def login(client, username="admin", password=PASSWORD):
    response = client.post("/api/auth/login", headers=HEADERS, json={"username": username, "password": password})
    assert response.status_code == 200
    return {**HEADERS, "X-CSRF-Token": response.json()["csrf"]}


def test_setup_requires_server_token_and_is_one_time(client, app):
    assert client.get("/api/bootstrap").json()["setup_required"]
    assert client.post("/api/auth/setup", headers=HEADERS, json={"token": "wrong", "username": "admin", "password": PASSWORD}).status_code == 403
    headers = setup(client, app)
    assert not (app.state.store.directory / "setup-token").exists()
    assert not client.get("/api/bootstrap").json()["setup_required"]
    assert client.get("/api/session").json()["user"]["role"] == "admin"
    assert client.post("/api/auth/setup", headers=headers, json={"token": "wrong", "username": "other", "password": PASSWORD}).status_code == 403


def test_every_private_read_requires_login(client):
    for path in ["/api/dashboard", "/api/calendar?start=2026-10-01&end=2026-10-31", "/api/library", "/api/activity", "/api/settings", "/api/users", "/api/poster/sonarr/1"]:
        assert client.get(path).status_code == 401


def test_csrf_and_cross_origin_requests_rejected(client, app):
    setup(client, app)
    assert client.post("/api/sync", headers=HEADERS).status_code == 403
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/login", headers={**HEADERS, "Origin": "https://attacker.example"}, json={"username": "admin", "password": PASSWORD}).status_code == 403


def test_viewer_cannot_change_config_users_or_refresh(client, app):
    headers = setup(client, app)
    assert client.post("/api/users", headers=headers, json={"username": "viewer", "password": PASSWORD}).status_code == 200
    client.post("/api/auth/logout", headers=headers)
    viewer = login(client, "viewer")
    assert client.get("/api/dashboard").status_code == 200
    for path in ["/api/settings", "/api/users"]:
        assert client.get(path).status_code == 403
    assert client.post("/api/sync", headers=viewer).status_code == 403
    assert client.post("/api/users", headers=viewer, json={"username": "other", "password": PASSWORD}).status_code == 403


def test_local_admin_cannot_disable_or_demote_self(client, app):
    headers = setup(client, app)
    user_id = client.get("/api/session").json()["user"]["id"]
    for payload in [{"enabled": False}, {"approved": False}, {"role": "viewer"}]:
        assert client.patch(f"/api/users/{user_id}", headers=headers, json=payload).status_code == 400


def test_disabling_viewer_invalidates_existing_session(client, app):
    headers = setup(client, app)
    client.post("/api/users", headers=headers, json={"username": "viewer", "password": PASSWORD})
    viewer_id = next(u["id"] for u in client.get("/api/users").json()["items"] if u["username"] == "viewer")
    viewer_client = TestClient(app)
    login(viewer_client, "viewer")
    assert viewer_client.get("/api/dashboard").status_code == 200
    assert client.patch(f"/api/users/{viewer_id}", headers=headers, json={"enabled": False}).status_code == 200
    assert viewer_client.get("/api/dashboard").status_code == 401


def test_password_change_revokes_sessions(client, app):
    headers = setup(client, app)
    assert client.post("/api/auth/password", headers=headers, json={"current_password": "wrong", "new_password": "another-long-password"}).status_code == 403
    assert client.post("/api/auth/password", headers=headers, json={"current_password": PASSWORD, "new_password": "another-long-password"}).status_code == 200
    assert client.get("/api/session").status_code == 401
    login(client, password="another-long-password")


def test_login_throttling_and_generic_errors(client, app):
    setup(client, app)
    for _ in range(10):
        response = client.post("/api/auth/login", headers=HEADERS, json={"username": "does-not-exist", "password": "wrong"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Username or password is incorrect."
    assert client.post("/api/auth/login", headers=HEADERS, json={"username": "admin", "password": PASSWORD}).status_code == 429


def test_settings_encrypted_redacted_and_blank_preserves_secret(client, app):
    headers = setup(client, app)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["sonarr"] = {"enabled": True, "url": "http://sonarr:8989", "api_key": "sensitive-test-key"}
    assert client.put("/api/settings", headers=headers, json=settings).status_code == 200
    response = client.get("/api/settings")
    assert "sensitive-test-key" not in response.text
    assert response.json()["settings"]["sonarr"]["has_api_key"]
    assert b"sensitive-test-key" not in app.state.store.path.read_bytes()
    settings["sonarr"]["api_key"] = ""
    client.put("/api/settings", headers=headers, json=settings)
    assert app.state.store.settings()["sonarr"]["api_key"] == "sensitive-test-key"


def test_invalid_connections_and_missing_credentials_rejected(client, app):
    headers = setup(client, app)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["sonarr"]["enabled"] = True
    assert client.put("/api/settings", headers=headers, json=settings).status_code == 422
    for url in ["file:///etc/passwd", "http://user:password@sonarr:8989", "http://sonarr/?apikey=secret"]:
        settings["sonarr"].update(url=url, api_key="key")
        assert client.put("/api/settings", headers=headers, json=settings).status_code == 422


def test_calendar_range_validation(client, app):
    setup(client, app)
    assert client.get("/api/calendar?start=2026-10-01&end=2026-10-31").status_code == 200
    assert client.get("/api/calendar?start=2026-11-01&end=2026-10-31").status_code == 400
    assert client.get("/api/calendar?start=2026-01-01&end=2026-12-31").status_code == 400


def test_password_hashes_are_salted():
    first, second = password_hash(PASSWORD), password_hash(PASSWORD)
    assert first != second
    assert password_valid(PASSWORD, first)
    assert not password_valid("wrong", first)


def test_plex_availability_requires_matching_file():
    movie = {"kind": "movie", "external_id": 1, "tmdb_id": 123, "monitored": True,
             "has_file": True, "date": "2026-01-01"}
    assert decorate(dict(movie), [], {}, {}, {"state": "connected"})["status"] == "importing"
    index = {"machine_id": "server", "movies": {"tmdb://123": {"rating_key": "42"}}}
    result = decorate(dict(movie), [], {}, index, {"state": "connected"})
    assert result["status"] == "available"
    assert "42" in result["plex_url"]
    assert decorate(dict(movie), [], {}, index, {"state": "error"})["stale"]


@pytest.mark.parametrize("state,progress,expected", [
    ("downloading", .5, "downloading"), ("queuedDL", 0, "queued"),
    ("pausedDL", .4, "queued"), ("stoppedDL", .4, "queued"),
    ("stalledDL", .2, "queued"), ("metaDL", 0, "queued"),
    ("uploading", 1, "importing"), ("stoppedUP", 1, "importing"), ("error", .4, "issue"),
])
def test_qbittorrent_states(state, progress, expected):
    result = download_state({"size": 100, "sizeleft": 50}, {"state": state, "progress": progress, "eta": 300, "dlspeed": 123})
    assert result["status"] == expected
    assert result["progress"] == progress


def test_import_warning_overrides_completed_download():
    assert download_state({"trackedDownloadStatus": "warning"}, {"state": "uploading", "progress": 1})["status"] == "issue"


def test_earliest_home_release_preferred_to_theatrical():
    assert release_date({"inCinemas": "2026-01-01", "digitalRelease": "2026-04-01", "physicalRelease": "2026-03-20"}) == ("2026-03-20", "Physical release")
    assert "TBD" in release_date({"inCinemas": "2026-01-01"})[1]
    assert release_date({})[0] is None


def test_monitoring_diffs_and_removals_survive_restart(tmp_path):
    store = Store(tmp_path)
    sync = SyncService(store, Clients())
    sync.record_monitoring("sonarr", [{"id": 1, "title": "Show", "monitored": True, "added": "2026-01-01T12:00:00Z"}])
    sync.record_monitoring("sonarr", [{"id": 1, "title": "Show", "monitored": True}])
    assert len(store.activity()) == 1
    sync.record_monitoring("sonarr", [{"id": 1, "title": "Show", "monitored": False}])
    sync.record_monitoring("sonarr", [])
    assert {r["action"] for r in Store(tmp_path).activity()} == {"added", "monitoring_paused", "removed"}


def test_queue_pagination_and_api_header():
    seen = []
    def handler(request):
        assert request.headers["x-api-key"] == "secret"
        page = int(request.url.params["page"])
        seen.append(page)
        return httpx.Response(200, json={"records": [{"id": page}], "totalRecords": 3})
    result = asyncio.run(Clients(httpx.MockTransport(handler)).queue({"url": "http://sonarr", "api_key": "secret"}, "sonarr"))
    assert seen == [1, 2, 3]
    assert len(result) == 3


def test_qbittorrent_cookie_auth_and_referer():
    def handler(request):
        assert request.headers["referer"] == "http://qbt/"
        if request.url.path.endswith("login"):
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=test-session; Path=/"})
        assert "SID=test-session" in request.headers["cookie"]
        return httpx.Response(200, json=[{"hash": "abc", "progress": .5}])
    result = asyncio.run(Clients(httpx.MockTransport(handler)).qbittorrent({"url": "http://qbt", "username": "user", "password": "secret"}))
    assert result[0]["progress"] == .5


def test_plex_index_matches_provider_ids_season_and_episode():
    def handler(request):
        assert request.headers["x-plex-token"] == "secret"
        if request.url.path == "/":
            data = {"machineIdentifier": "server"}
        elif request.url.path == "/library/sections":
            data = {"Directory": [{"type": "show", "key": "1"}, {"type": "movie", "key": "2"}]}
        elif request.url.path == "/library/sections/2/all":
            data = {"Metadata": [{"ratingKey": "movie", "Guid": [{"id": "tmdb://555"}], "Media": [{"Part": [{"exists": True}]}]}]}
        elif request.url.params.get("type") == "4":
            data = {"Metadata": [{"ratingKey": "episode", "grandparentRatingKey": "show", "parentIndex": 2, "index": 3, "Media": [{"Part": [{"exists": True}]}]}]}
        else:
            data = {"Metadata": [{"ratingKey": "show", "Guid": [{"id": "tvdb://444"}]}]}
        return httpx.Response(200, json={"MediaContainer": data})
    index = asyncio.run(Clients(httpx.MockTransport(handler)).plex_index({"url": "http://plex", "token": "secret"}))
    assert index["episodes"]["tvdb://444:2:3"]["rating_key"] == "episode"
    assert index["movies"]["tmdb://555"]["rating_key"] == "movie"


def test_legacy_plex_provider_ids():
    assert external_guids({"guid": "com.plexapp.agents.thetvdb://444?lang=en"}) == ["tvdb://444"]


def test_partial_service_failure_preserves_last_good_snapshot(tmp_path):
    store = Store(tmp_path)
    settings = store.settings()
    settings["radarr"] = {"enabled": True, "url": "http://radarr", "api_key": "secret"}
    store.save_settings(settings)
    store.set_cache("radarr", {"catalog": [{"id": 1, "title": "Movie", "monitored": True}], "queue": []})
    store.set_cache("status:radarr", {"state": "connected", "last_success": "2026-01-01T00:00:00Z"})
    clients = Clients(httpx.MockTransport(lambda request: httpx.Response(500)))
    asyncio.run(SyncService(store, clients).refresh())
    assert store.cache("radarr")["catalog"][0]["title"] == "Movie"
    assert store.cache("status:radarr")["state"] == "error"
    assert store.cache("status:radarr")["last_success"] == "2026-01-01T00:00:00Z"
    assert "secret" not in store.cache("status:radarr")["error"]


def test_plex_account_must_have_configured_server_access():
    def handler(request):
        return httpx.Response(200, json=[{"clientIdentifier": "other-server", "provides": "server"}])
    with pytest.raises(IntegrationError, match="does not have access"):
        asyncio.run(Clients(httpx.MockTransport(handler)).plex_account("client", "token", "my-server"))


def test_demo_is_read_only_and_does_not_expose_real_configuration(tmp_path):
    app = create_app(tmp_path, demo=True, background=False)
    with TestClient(app) as client:
        assert client.get("/api/session").json()["demo"]
        assert client.get("/api/dashboard").json()["stats"]["downloading"] == 2
        assert client.post("/api/users", headers=HEADERS, json={"username": "viewer", "password": PASSWORD}).status_code == 409


def test_security_headers_and_no_api_cache(client, app):
    setup(client, app)
    response = client.get("/api/dashboard")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_today_air_time_is_upcoming_until_it_airs():
    episode = {"kind": "episode", "external_id": 1, "monitored": True, "has_file": False,
               "date": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(), "all_day": False}
    assert decorate(episode, [], {}, {}, {})["status"] == "upcoming"


def test_four_services_sync_then_plex_arrival_is_recorded(tmp_path):
    store = Store(tmp_path)
    settings = store.settings()
    for source in ["sonarr", "radarr"]:
        settings[source] = {"enabled": True, "url": f"http://{source}", "api_key": "secret"}
    settings["plex"] = {"enabled": True, "url": "http://plex", "token": "secret"}
    settings["qbittorrent"] = {"enabled": True, "url": "http://qbt", "username": "admin", "password": "secret"}
    store.save_settings(settings)
    imported = False
    def handler(request):
        host, path = request.url.host, request.url.path
        if host == "sonarr":
            show = {"id": 10, "title": "Test Show", "monitored": True, "tvdbId": 444, "added": "2026-01-01"}
            episode = {"id": 100, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 2,
                       "title": "Episode Two", "monitored": True, "airDateUtc": "2026-10-01T20:00:00Z", "hasFile": imported}
            if path.endswith("series"):
                data = [show]
            elif path.endswith("calendar"):
                data = [episode]
            else:
                data = {"records": [] if imported else [{"episodeId": 100, "seriesId": 10,
                    "episode": episode, "downloadId": "ABCDEF", "status": "downloading", "size": 100, "sizeleft": 50}], "totalRecords": 0 if imported else 1}
        elif host == "radarr":
            data = [{"id": 20, "title": "Movie", "monitored": True, "tmdbId": 555, "digitalRelease": "2026-10-01"}] if path.endswith("movie") else {"records": [], "totalRecords": 0}
        elif host == "qbt":
            if path.endswith("login"):
                return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=sample; Path=/"})
            data = [{"hash": "abcdef", "progress": .65, "state": "downloading", "dlspeed": 1000, "eta": 300}]
        elif path == "/":
            data = {"MediaContainer": {"machineIdentifier": "my-server"}}
        elif path == "/library/sections":
            data = {"MediaContainer": {"Directory": [{"type": "show", "key": "1"}]}}
        elif request.url.params.get("type") == "4":
            data = {"MediaContainer": {"Metadata": [{"ratingKey": "321", "grandparentRatingKey": "123", "parentIndex": 1,
                "index": 2, "Media": [{"Part": [{"exists": True}]}]}] if imported else []}}
        else:
            data = {"MediaContainer": {"Metadata": [{"ratingKey": "123", "Guid": [{"id": "tvdb://444"}]}]}}
        return httpx.Response(200, json=data)
    sync = SyncService(store, Clients(httpx.MockTransport(handler)))
    asyncio.run(sync.refresh())
    assert all(s["state"] == "connected" for s in sync.status().values())
    episode = next(e for e in sync.events() if e["kind"] == "episode")
    assert episode["status"] == "downloading"
    assert episode["progress"] == .65
    assert len(store.activity()) == 2
    imported = True
    asyncio.run(sync.refresh(force_plex=True))
    assert next(e for e in sync.events() if e["kind"] == "episode")["status"] == "available"
    assert sum(a["action"] == "available" for a in store.activity()) == 1
    asyncio.run(sync.refresh(force_plex=True))
    assert sum(a["action"] == "available" for a in store.activity()) == 1


def test_plex_pin_login_approval_and_browser_binding(tmp_path):
    def handler(request):
        if request.url.path == "/api/v2/pins":
            return httpx.Response(200, json={"id": 1, "code": "strong-random-code"})
        if request.url.path == "/api/v2/pins/1":
            return httpx.Response(200, json={"authToken": "plex-user-secret"})
        if request.url.path == "/api/v2/resources":
            return httpx.Response(200, json=[{"clientIdentifier": "my-server", "provides": "server"}])
        return httpx.Response(200, json={"id": 456, "username": "plex-viewer"})
    app = create_app(tmp_path, demo=False, transport=httpx.MockTransport(handler), background=False)
    with TestClient(app) as admin_client, TestClient(app) as plex_client, TestClient(app) as unrelated:
        admin_headers = setup(admin_client, app)
        settings = app.state.store.settings()
        settings["plex"] = {"enabled": True, "url": "http://plex", "token": "owner-secret"}
        settings["preferences"].update(plex_login=True, plex_approval=True)
        app.state.store.save_settings(settings)
        app.state.store.set_cache("plex", {"machine_id": "my-server"})
        start = plex_client.post("/api/auth/plex/start", headers=HEADERS)
        assert start.status_code == 200
        assert start.json()["auth_url"].startswith("https://app.plex.tv/auth#?")
        assert "plex-user-secret" not in start.text
        assert unrelated.post("/api/auth/plex/poll", headers=HEADERS).status_code == 410
        assert plex_client.post("/api/auth/plex/poll", headers=HEADERS).status_code == 403
        row = next(u for u in admin_client.get("/api/users").json()["items"] if u["provider"] == "plex")
        assert row["role"] == "viewer" and not row["approved"]
        assert admin_client.patch(f"/api/users/{row['id']}", headers=admin_headers, json={"approved": True}).status_code == 200
        assert admin_client.patch(f"/api/users/{row['id']}", headers=admin_headers, json={"role": "admin"}).status_code == 400
        plex_client.post("/api/auth/plex/start", headers=HEADERS)
        assert plex_client.post("/api/auth/plex/poll", headers=HEADERS).status_code == 200
        assert plex_client.get("/api/session").json()["user"]["role"] == "viewer"
        assert b"plex-user-secret" not in app.state.store.path.read_bytes()
        # Revoke a grant and force the periodic verification to run.
        with app.state.store.connection() as db:
            db.execute("UPDATE sessions SET access_checked=0 WHERE user_id=?", (row["id"],))
        app.state.store.set_cache("plex", {"machine_id": "different-server"})
        assert plex_client.get("/api/dashboard").status_code == 401


def test_https_public_origin_enables_secure_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://calendar.example.com")
    app = create_app(tmp_path, demo=False, background=False)
    with TestClient(app, base_url="https://calendar.example.com") as client:
        token = (tmp_path / "setup-token").read_text()
        response = client.post("/api/auth/setup", headers={**HEADERS, "Origin": "https://calendar.example.com"},
            json={"token": token, "username": "admin", "password": PASSWORD})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/session").status_code == 200


def test_error_backoff_and_manual_retry(tmp_path):
    store = Store(tmp_path)
    settings = store.settings()
    settings["qbittorrent"] = {"enabled": True, "url": "http://qbt", "username": "admin", "password": "wrong"}
    store.save_settings(settings)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="Fails.")
    sync = SyncService(store, Clients(httpx.MockTransport(handler)))
    asyncio.run(sync.refresh())
    asyncio.run(sync.refresh())
    assert len(seen) == 1
    asyncio.run(sync.refresh(force_plex=True))
    assert len(seen) == 2
