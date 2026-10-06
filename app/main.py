"""Calendarr ASGI app. Run exactly one worker; the background poller is in-process."""
import asyncio
import contextlib
import copy
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .integrations import Clients, IntegrationError, plex_auth_url, safe_error, validate_url
from .security import RateLimiter, create_session, password_hash, password_valid, token_hash
from .store import DEFAULT_SETTINGS, Store
from .sync import SyncService

STATIC = Path(__file__).parent / "static"
USERNAME = r"^[A-Za-z0-9_.@-]{3,64}$"
COOKIE = "calendarr_session"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class Setup(Input):
    token: str = Field(min_length=1, max_length=512)
    username: str = Field(pattern=USERNAME)
    password: str = Field(min_length=12, max_length=512)


class NewUser(Input):
    username: str = Field(pattern=USERNAME)
    password: str = Field(min_length=12, max_length=512)
    role: Literal["viewer", "admin"] = "viewer"


class UserUpdate(Input):
    enabled: bool | None = None
    approved: bool | None = None
    role: Literal["viewer", "admin"] | None = None
    password: str | None = Field(default=None, min_length=12, max_length=512)


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class Connection(Input):
    enabled: bool = False
    url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=1024)
    token: str = Field(default="", max_length=2048)
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=1024)


class Preferences(Input):
    poll_seconds: int = Field(default=60, ge=30, le=3600)
    plex_poll_seconds: int = Field(default=300, ge=60, le=3600)
    plex_login: bool = False
    plex_approval: bool = False
    app_name: str = Field(default="Calendarr", min_length=1, max_length=48)


class SettingsUpdate(Input):
    sonarr: Connection
    radarr: Connection
    plex: Connection
    qbittorrent: Connection
    preferences: Preferences


def public_user(row):
    return {k: row[k] for k in ["id", "username", "role", "provider", "enabled", "approved"]}


def masked_settings(settings):
    result = copy.deepcopy(settings)
    for source in ["sonarr", "radarr", "plex", "qbittorrent"]:
        for secret in ["api_key", "token", "password"]:
            if secret in result[source]:
                result[source]["has_" + secret] = bool(result[source][secret])
                result[source][secret] = ""
    return result


def create_app(data_dir=None, demo=None, transport=None, background=True):
    directory = Path(data_dir or os.environ.get("DATA_DIR", "data"))
    store = Store(directory)
    demo_mode = demo if demo is not None else os.environ.get("DEMO_MODE", "0") == "1"
    clients = Clients(transport)
    sync = SyncService(store, clients)
    limiter = RateLimiter()
    pins = {}
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    secure_cookie = os.environ.get("SECURE_COOKIES", "").lower() in {"true", "1"} or public_url.startswith("https://")
    client_id = store.cache("client_id")
    if not client_id:
        client_id = "calendarr-" + secrets.token_hex(16)
        store.set_cache("client_id", client_id)
    dummy_hash = password_hash(secrets.token_urlsafe(32))

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(sync.run()) if background and not demo_mode else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Calendarr Calendar", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.sync, app.state.clients = store, sync, clients

    @app.middleware("http")
    async def request_security(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("x-calendarr-request") != "1":
                return JSONResponse({"detail": "Missing request protection header."}, status_code=403)
            origin = request.headers.get("origin")
            allowed = {str(request.base_url).rstrip("/")}
            if public_url:
                parsed = urlsplit(public_url)
                allowed.add(f"{parsed.scheme}://{parsed.netloc}")
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Request origin is not allowed."}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 65536:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid request length."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def throttle(request, action, limit=10):
        key = f"{request.client.host if request.client else 'unknown'}:{action}"
        if not limiter.allow(key, limit):
            raise HTTPException(429, "Too many attempts. Please wait five minutes.", headers={"Retry-After": "300"})

    def session_cookie(response, user_id, plex=False):
        token, csrf, duration = create_session(store, user_id, plex)
        response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie, samesite="lax", max_age=duration, path="/")
        return csrf

    async def user(request: Request):
        if demo_mode:
            return {"id": 0, "username": "Alex Morgan", "role": "admin", "provider": "demo", "enabled": 1, "approved": 1, "csrf": "demo"}
        token = request.cookies.get(COOKIE, "")
        with store.connection() as db:
            row = db.execute("SELECT u.*,s.csrf,s.access_checked FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
                             (token_hash(token), time.time())).fetchone()
        if not row or not row["enabled"] or not row["approved"]:
            raise HTTPException(401, "Please sign in.")
        row = dict(row)
        if row["provider"] == "plex" and time.time() - row["access_checked"] > 300:
            settings = store.settings()
            index = store.cache("plex", {})
            if not settings["preferences"]["plex_login"] or not settings["plex"]["enabled"]:
                raise HTTPException(401, "Plex sign-in is disabled.")
            try:
                await clients.plex_account(client_id, store.decrypt(row["plex_token"]), index.get("machine_id"))
            except IntegrationError:
                with store.connection() as db:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
                raise HTTPException(401, "Plex server access has been revoked.")
            except Exception:
                raise HTTPException(503, "Plex could not verify your access. Try again shortly.")
            with store.connection() as db:
                db.execute("UPDATE sessions SET access_checked=? WHERE token=?", (time.time(), token_hash(token)))
        if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
            raise HTTPException(403, "Your session expired. Refresh the page and try again.")
        return row

    async def admin(account=Depends(user)):
        if account["role"] != "admin":
            raise HTTPException(403, "Administrator access is required.")
        return account

    def editable():
        if demo_mode:
            raise HTTPException(409, "The demo is read-only. Run your own instance to save changes.")

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/bootstrap")
    async def bootstrap():
        settings = store.settings()
        return {"setup_required": not store.has_admin() and not demo_mode, "demo": demo_mode,
                "plex_login": not demo_mode and settings["preferences"]["plex_login"] and settings["plex"]["enabled"] and bool(store.cache("plex", {}).get("machine_id")),
                "app_name": settings["preferences"]["app_name"]}

    @app.post("/api/auth/setup")
    async def setup(payload: Setup, request: Request, response: Response):
        editable()
        throttle(request, "setup")
        token_path = directory / "setup-token"
        expected = os.environ.get("SETUP_TOKEN") or (token_path.read_text().strip() if token_path.exists() else "")
        if not expected or not hmac.compare_digest(payload.token, expected):
            raise HTTPException(403, "The setup token is incorrect.")
        try:
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM users WHERE role='admin' AND provider='local'").fetchone():
                    raise HTTPException(409, "An administrator has already been created.")
                cursor = db.execute("INSERT INTO users(username,password,role,created) VALUES (?,?,'admin',?)",
                                    (payload.username, password_hash(payload.password), time.time()))
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is already in use.")
        token_path.unlink(missing_ok=True)
        return {"csrf": session_cookie(response, user_id)}

    @app.post("/api/auth/login")
    async def login(payload: Login, request: Request, response: Response):
        editable()
        throttle(request, "login")
        with store.connection() as db:
            row = db.execute("SELECT * FROM users WHERE username=? AND provider='local'", (payload.username,)).fetchone()
        valid = password_valid(payload.password, row["password"] if row else dummy_hash)
        if not valid or not row or not row["enabled"] or not row["approved"]:
            raise HTTPException(401, "Username or password is incorrect.")
        return {"csrf": session_cookie(response, row["id"])}

    @app.get("/api/session")
    async def session(account=Depends(user)):
        return {"user": public_user(account), "csrf": account["csrf"], "demo": demo_mode}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, account=Depends(user)):
        with store.connection() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (token_hash(request.cookies.get(COOKIE, "")),))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    async def change_password(payload: PasswordChange, account=Depends(user)):
        editable()
        if account["provider"] != "local" or not password_valid(payload.current_password, account["password"]):
            raise HTTPException(403, "The current password is incorrect.")
        with store.connection() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (password_hash(payload.new_password), account["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (account["id"],))
        return {"ok": True, "sign_in_required": True}

    @app.post("/api/auth/plex/start")
    async def plex_start(request: Request, response: Response):
        editable()
        throttle(request, "plex-start", 5)
        settings = store.settings()
        if not settings["preferences"]["plex_login"] or not settings["plex"]["enabled"] or not store.cache("plex", {}).get("machine_id"):
            raise HTTPException(409, "Connect Plex and enable Plex sign-in in Settings first.")
        for key in list(pins):
            if pins[key]["expires"] < time.time():
                del pins[key]
        try:
            pin = await clients.plex_pin(client_id)
        except Exception as error:
            raise HTTPException(502, safe_error(error))
        challenge = secrets.token_urlsafe(32)
        pins[token_hash(challenge)] = {"pin_id": pin["id"], "code": pin["code"],
                                      "expires": time.time() + 600, "last_poll": 0}
        response.set_cookie("calendarr_plex_challenge", challenge, httponly=True, secure=secure_cookie, samesite="lax", max_age=600, path="/api/auth/plex")
        return {"auth_url": plex_auth_url(client_id, pin["code"])}

    @app.post("/api/auth/plex/poll")
    async def plex_poll(request: Request, response: Response):
        editable()
        key = token_hash(request.cookies.get("calendarr_plex_challenge", ""))
        pin = pins.get(key)
        if not pin or pin["expires"] < time.time():
            raise HTTPException(410, "Plex sign-in expired. Start again.")
        if time.time() - pin["last_poll"] < 2:
            return {"pending": True}
        pin["last_poll"] = time.time()
        settings = store.settings()
        if not settings["preferences"]["plex_login"] or not settings["plex"]["enabled"]:
            raise HTTPException(409, "Plex sign-in has been disabled.")
        try:
            result = await clients.plex_pin(client_id, pin["pin_id"], pin["code"])
            token = result.get("authToken")
            if not token:
                return {"pending": True}
            index = store.cache("plex", {})
            plex_user = await clients.plex_account(client_id, token, index.get("machine_id"))
        except IntegrationError as error:
            pins.pop(key, None)
            raise HTTPException(403, str(error))
        except Exception as error:
            raise HTTPException(502, safe_error(error))
        plex_id = str(plex_user["id"])
        with store.connection() as db:
            row = db.execute("SELECT * FROM users WHERE plex_id=?", (plex_id,)).fetchone()
            if row is None:
                # Plex users stay viewers and cannot collide with a local administrator's username.
                name = (plex_user.get("username") or plex_user.get("title") or "Plex user")[:64]
                if db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                    name = name[:48] + "-plex-" + plex_id
                db.execute("INSERT INTO users(username,role,provider,plex_id,plex_token,approved,created) VALUES (?,'viewer','plex',?,?,?,?)",
                    (name, plex_id, store.encrypt(token), int(not settings["preferences"]["plex_approval"]), time.time()))
                row = db.execute("SELECT * FROM users WHERE plex_id=?", (plex_id,)).fetchone()
            else:
                db.execute("UPDATE users SET plex_token=? WHERE id=?", (store.encrypt(token), row["id"]))
        pins.pop(key, None)
        response.delete_cookie("calendarr_plex_challenge", path="/api/auth/plex")
        if not row["enabled"] or not row["approved"]:
            raise HTTPException(403, "Your account is awaiting administrator approval or has been disabled.")
        return {"pending": False, "csrf": session_cookie(response, row["id"], plex=True)}

    @app.get("/api/dashboard")
    async def dashboard(account=Depends(user)):
        if demo_mode:
            from .demo import dashboard_data
            return dashboard_data()
        events = sync.events()
        today = date.today().isoformat()
        next_week = (date.today() + timedelta(days=6)).isoformat()
        return {"services": sync.status(), "last_sync": store.cache("last_sync"), "syncing": sync.lock.locked(),
                "sync_error": store.cache("sync_error"), "stats": {
                    "this_week": sum(1 for e in events if e.get("monitored") and e.get("date") and today <= e["date"][:10] <= next_week),
                    "downloading": sum(e["status"] == "downloading" for e in events),
                    "queued": sum(e["status"] in {"queued", "importing"} for e in events),
                    "monitored": sum(bool(e.get("monitored")) for e in sync.library())},
                "downloads": [e for e in events if e["status"] in {"downloading", "queued", "importing", "issue"}],
                "activity": store.activity(6)}

    @app.get("/api/calendar")
    async def calendar(start: date = Query(), end: date = Query(), account=Depends(user)):
        if end < start or (end - start).days > 63:
            raise HTTPException(400, "Choose a date range of up to 64 days.")
        if demo_mode:
            from .demo import events_data
            events = events_data()
            return {"events": [e for e in events if start.isoformat() <= e["date"][:10] <= end.isoformat()]}
        try:
            return {"events": await sync.calendar(start.isoformat(), end.isoformat())}
        except Exception as error:
            raise HTTPException(502, safe_error(error))

    @app.get("/api/library")
    async def library(account=Depends(user)):
        if demo_mode:
            from .demo import library_data
            return {"items": library_data()}
        return {"items": sync.library()}

    @app.get("/api/activity")
    async def activity(account=Depends(user)):
        if demo_mode:
            from .demo import activity_data
            return {"items": activity_data()}
        return {"items": store.activity()}

    @app.post("/api/sync")
    async def refresh(request: Request, account=Depends(admin)):
        if demo_mode:
            return {"ok": True, "demo": True}
        throttle(request, "sync", 15)
        return {"ok": await sync.refresh(force_plex=True)}

    @app.get("/api/settings")
    async def settings(account=Depends(admin)):
        return {"settings": masked_settings(store.settings()), "services": sync.status()}

    @app.put("/api/settings")
    async def save_settings(payload: SettingsUpdate, account=Depends(admin)):
        editable()
        original = store.settings()
        updated = copy.deepcopy(DEFAULT_SETTINGS)
        for source in ["sonarr", "radarr", "plex", "qbittorrent"]:
            incoming = getattr(payload, source).model_dump()
            config = updated[source]
            for field in config:
                config[field] = incoming[field]
            for secret in ["api_key", "token", "password"]:
                if secret in config and not config[secret]:
                    config[secret] = original[source].get(secret, "")
            if config["url"]:
                try:
                    config["url"] = validate_url(config["url"])
                except ValueError as error:
                    raise HTTPException(422, str(error))
            required = {"sonarr": ["url", "api_key"], "radarr": ["url", "api_key"],
                        "plex": ["url", "token"], "qbittorrent": ["url", "username", "password"]}[source]
            if config["enabled"] and any(not config.get(field) for field in required):
                raise HTTPException(422, f"Complete the {source} connection fields before enabling it.")
        updated["preferences"] = payload.preferences.model_dump()
        if updated["preferences"]["plex_login"] and not updated["plex"]["enabled"]:
            raise HTTPException(422, "Enable a Plex connection before enabling Plex sign-in.")
        # Wait for an active sync before changing credentials or invalidating its caches.
        async with sync.lock:
            store.save_settings(updated)
            with store.connection() as db:
                for source in ["sonarr", "radarr", "plex", "qbittorrent"]:
                    if original[source] != updated[source]:
                        db.execute("DELETE FROM cache WHERE key IN (?,?)", (source, f"status:{source}"))
                        db.execute("DELETE FROM cache WHERE key LIKE 'calendar:%'")
                    if original[source]["url"] != updated[source]["url"]:
                        db.execute("DELETE FROM monitored WHERE source=?", (source,))
                if original["plex"] != updated["plex"] or not updated["preferences"]["plex_login"]:
                    db.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE provider='plex')")
        sync.wake.set()
        return {"ok": True, "settings": masked_settings(updated)}

    @app.post("/api/settings/test/{source}")
    async def test_connection(source: Literal["sonarr", "radarr", "plex", "qbittorrent"], payload: Connection, account=Depends(admin)):
        editable()
        original = store.settings()[source]
        config = {k: payload.model_dump()[k] for k in original}
        for key in ["api_key", "token", "password"]:
            if key in config and not config[key]:
                config[key] = original.get(key, "")
        try:
            config["url"] = validate_url(config["url"])
            if source in {"sonarr", "radarr"}:
                data = await clients.arr(config, "system/status")
                message = f"Connected to {source.title()} {data.get('version', '')}."
            elif source == "plex":
                data = await clients.plex_get(config, "/")
                if not data.get("machineIdentifier"):
                    raise IntegrationError("This address did not return a Plex Media Server identity.")
                message = f"Connected to {data.get('friendlyName', 'Plex Media Server')}."
            else:
                data = await clients.qbittorrent(config)
                message = f"Connected. {len(data)} torrents visible to the service."
            return {"ok": True, "message": message}
        except ValueError:
            return {"ok": False, "message": "Check the base URL and the service response format."}
        except Exception as error:
            return {"ok": False, "message": safe_error(error)}

    @app.get("/api/users")
    async def users(account=Depends(admin)):
        if demo_mode:
            return {"items": [{"id": 0, "username": "Alex Morgan", "role": "admin", "provider": "demo", "enabled": True, "approved": True}]}
        with store.connection() as db:
            return {"items": [public_user(r) for r in db.execute("SELECT * FROM users ORDER BY created")]}

    @app.post("/api/users")
    async def create_user(payload: NewUser, account=Depends(admin)):
        editable()
        try:
            with store.connection() as db:
                db.execute("INSERT INTO users(username,password,role,created) VALUES (?,?,?,?)",
                           (payload.username, password_hash(payload.password), payload.role, time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is already in use.")
        return {"ok": True}

    @app.patch("/api/users/{user_id}")
    async def update_user(user_id: int, payload: UserUpdate, account=Depends(admin)):
        editable()
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Account not found.")
            updated = dict(row)
            for field, value in payload.model_dump(exclude_none=True).items():
                updated[field] = password_hash(value) if field == "password" else value
            if row["provider"] == "plex" and (updated["role"] != "viewer" or payload.password):
                raise HTTPException(400, "Plex accounts are viewers. Manage their password through Plex.")
            if user_id == account["id"] and (not updated["enabled"] or not updated["approved"] or updated["role"] != "admin" or payload.password):
                raise HTTPException(400, "Keep your own administrator account active. Use Change password for your password.")
            if row["provider"] == "local" and row["role"] == "admin" and row["enabled"] and row["approved"]:
                other = db.execute("SELECT 1 FROM users WHERE id!=? AND provider='local' AND role='admin' AND enabled=1 AND approved=1", (user_id,)).fetchone()
                if not other and (not updated["enabled"] or not updated["approved"] or updated["role"] != "admin"):
                    raise HTTPException(400, "At least one active local administrator is required.")
            db.execute("UPDATE users SET enabled=?,approved=?,role=?,password=? WHERE id=?",
                       (updated["enabled"], updated["approved"], updated["role"], updated["password"], user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return {"ok": True}

    @app.get("/api/poster/{source}/{item_id}")
    async def poster(source: Literal["sonarr", "radarr"], item_id: int, account=Depends(user)):
        if demo_mode:
            raise HTTPException(404, "No poster in demo mode.")
        config = store.settings()[source]
        data = store.cache(source, {})
        if not config["enabled"] or not any(item["id"] == item_id for item in data.get("catalog", [])):
            raise HTTPException(404, "Poster not found.")
        try:
            async with clients.client(headers={"X-Api-Key": config["api_key"]}) as client:
                async with client.stream("GET", config["url"] + f"/MediaCover/{item_id}/poster.jpg") as upstream:
                    upstream.raise_for_status()
                    content_type = upstream.headers.get("content-type", "").split(";")[0]
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise HTTPException(404, "Poster not found.")
                    chunks, size = [], 0
                    async for chunk in upstream.aiter_bytes():
                        size += len(chunk)
                        if size > 5 * 1024 * 1024:
                            raise HTTPException(413, "Poster is too large.")
                        chunks.append(chunk)
                    return Response(b"".join(chunks), media_type=content_type)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(404, "Poster not found.")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
