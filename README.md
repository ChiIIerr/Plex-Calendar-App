# Reelarr · Plex Calendar

A self-hosted calendar for the shows and movies making their way to your Plex library. Reelarr reads Sonarr, Radarr, Plex Media Server, and qBittorrent, then gives your users one place to see release dates, download progress, monitoring additions, and confirmed Plex availability.

## What’s included

- Responsive month, week, and agenda calendars with search, media filters, and status filters.
- Download and queue views with progress, speed, ETA, paused/stalled states, and import warnings.
- A monitored catalog and persistent activity history for additions, removals, monitoring changes, and arrivals in Plex.
- A required local administrator, additional local viewer/admin accounts, password changes, and offline administrator recovery.
- Optional Plex sign-in. Users must have access to the configured Plex server and receive viewer access. You can require approval of each new Plex account.
- Admin-only integration settings and connection tests. Credentials are encrypted at rest and never sent back to the browser.
- SQLite persistence, background polling, independent service error handling, and clearly marked cached status during outages.
- Docker Compose with automatic restarts, a health check, and a non-root container. No separate database or frontend build is needed.

Reelarr reads the upstream services. Add titles, edit monitoring, manage downloads, and scan libraries in their respective applications.

## Start with Docker Compose

Install Docker Engine with the Compose plugin, or Docker Desktop. Then:

```sh
git clone https://github.com/ChiIIerr/Plex-Calendar-App.git
cd Plex-Calendar-App
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8282**. Before creating the administrator, read the one-time setup token:

```sh
docker compose exec reelarr cat /data/setup-token
```

Enter that token on the setup page, choose a username, and set a password of at least 12 characters. The app removes the setup-token file once the administrator is created. There is no default password and no public self-registration for local accounts.

Open **Settings**, enter each service’s server URL and credentials, test its connection, enable it, and save. Use **Sync now** to fetch the first snapshot. The default polling intervals are 60 seconds for Arr/download status and five minutes for the Plex library.

The URLs must be reachable **from the container**:

| Service | Typical address | Credential |
| --- | --- | --- |
| Sonarr | `http://sonarr:8989` | API key from Settings → General → Security |
| Radarr | `http://radarr:7878` | API key from Settings → General → Security |
| Plex Media Server | `http://plex:32400` | Server owner’s `X-Plex-Token` |
| qBittorrent | `http://qbittorrent:8080` | Web UI username/password |

Container names work when Reelarr shares a Docker network with those services. Join your existing external network by adding a `networks` entry to the Reelarr service and declaring that network with `external: true`. For services installed directly on the Docker host, use `host.docker.internal` with their published ports. A LAN IP is another option. Preserve any configured URL base, such as `http://host:8989/sonarr`.

Enable the qBittorrent Web UI. Its configured host/domain must accept the URL you use. Reelarr sends the Origin/Referer headers and maintains the required login cookie. It supports the common Web API v2 username/password authentication used in qBittorrent 4.1+ and 5.x; the optional newer API-key authentication is not implemented.

### Run on startup

The included `restart: unless-stopped` policy starts Reelarr when the Docker daemon starts, unless you explicitly stopped the container. On Linux, enable the Docker service at boot. On Windows/macOS, enable Docker Desktop’s startup option; it normally starts when you sign in. For unattended startup before a user signs in, use a server running Docker Engine or a native service.

Useful commands:

```sh
docker compose logs -f reelarr
docker compose restart reelarr
docker compose down
# After pulling an update:
git pull
docker compose up -d --build
```

The `reelarr-data` volume retains your administrator, integration configuration, cache, and activity history across container upgrades. `docker compose down` preserves it; `docker compose down -v` removes it. Failed service connections use increasing retry intervals, up to 30 minutes, to avoid repeatedly hammering an unavailable service or a rejected login. **Sync now** retries immediately, and changing its saved connection resets that delay.

## External access

Run Reelarr behind your existing HTTPS reverse proxy. The default port binding is loopback-only, so a reverse proxy running on the Docker host can connect to `127.0.0.1:8282`.

Set these values in `.env` and recreate the container:

```dotenv
PUBLIC_URL=https://calendar.your-domain.example
SECURE_COOKIES=true
FORWARDED_ALLOW_IPS=127.0.0.1
```

```sh
docker compose up -d
```

Point your proxy at Reelarr’s HTTP port. [deploy/Caddyfile.example](deploy/Caddyfile.example) shows a host-installed Caddy configuration. If the proxy runs in another container, attach it to Reelarr’s network and proxy to `reelarr:8282`. Set `FORWARDED_ALLOW_IPS` to that proxy’s actual address or trusted network; do not trust arbitrary public clients. The app accepts the configured public origin for protected requests and enables secure cookies automatically for an HTTPS `PUBLIC_URL`.

To access the app directly from your LAN without a host-installed proxy, set `BIND_ADDRESS=0.0.0.0` and set `PUBLIC_URL` to the actual LAN address. Use HTTPS via a reverse proxy for internet-facing access. Publish the calendar app’s proxy endpoint; the upstream services can stay on your internal network. Serve Reelarr at the root of its hostname; hosting it under a path prefix is not currently supported.

### Plex sign-in

1. Connect Plex using your server owner’s token, save settings, and complete a successful sync.
2. Enable **Allow sign-in with Plex** in Settings. Optionally enable **Require admin approval**.
3. Users choose **Continue with Plex** on the login screen and approve the sign-in on Plex’s website.
4. Reelarr checks that Plex lists the configured server among that user’s accessible resources. A different Plex account without server access cannot enter.

Plex accounts are always viewers; local accounts provide administrator access. Server access is rechecked every five minutes during an active Plex session. Plex sessions last one hour and local sessions last seven days. Disabling an account or changing its permissions revokes its sessions. An approval requirement applies to **new** Plex accounts; existing approvals remain in place. Administrators can disable or approve accounts from Settings.

**Calendar scope:** every approved viewer can see the entire monitored catalog configured in this app. Reelarr does not apply Plex’s per-library sharing restrictions, parental controls, or title restrictions to calendar visibility. The **Open in Plex** link still uses the viewer’s normal Plex permissions for playback. Require local accounts or individual approval if that broader calendar visibility is not appropriate for your users.

## How statuses work

| Status | Evidence |
| --- | --- |
| Upcoming | A monitored episode/movie has a future air or home-release date. |
| Awaiting release | Monitored, with no current file or matching download. |
| Queued | Arr/qBittorrent reports queued, paused, stopped, stalled, metadata, or file-check state. |
| Downloading | A matching Arr queue entry is downloading; qBittorrent supplies progress/speed/ETA when its download hash matches. |
| Awaiting Plex | Arr reports a file or a completed download, but Plex has not confirmed the item yet. |
| Needs attention | Arr reports a download/import warning or the client reports an error. |
| In Plex | Plex has a matching movie file or episode file in its library. |
| Not monitored | The source item is not monitored; hidden by default in the calendar. |

Movies use the earlier of their digital/physical release dates. A theatrical-only date is explicitly labeled **In theaters · home release TBD**; it does not promise home availability. Titles without a known date remain visible in **Monitored**. Episode air times appear in the viewer’s local timezone; movie releases remain date-only.

Plex matching uses TMDB/IMDb IDs for movies and TVDB ID plus season/episode number for episodes. Modern Plex metadata and common legacy agent IDs are supported. If metadata IDs are missing or episode numbering differs between Plex and Sonarr, the app keeps the item in **Awaiting Plex** rather than guessing from a title. Fix matching/numbering in your source services. Playback confirmation means the item is indexed with an existing media part, not a real-time filesystem read or playback test.

qBittorrent torrents are shown only through matching Sonarr/Radarr queue entries, using download info hashes. Unrelated torrents are not exposed to viewers. Sonarr/Radarr queue progress remains available if qBittorrent is not configured. Once an imported download disappears from the Arr queue, its presence is determined through Arr file status and Plex confirmation.

On the first sync, catalog additions use the original `added` timestamp when the source provides it. Monitoring toggles and removals are recorded when Reelarr observes them; it cannot reconstruct monitoring changes made before it was installed or changes toggled back between polls. The activity log retains the latest 5,000 entries; the Activity page shows the most recent 200. Availability activity is recorded when an observed item transitions into Plex. Existing Plex media does not create a flood of arrival events on initial setup.

## Direct Python installation

Python 3.12 or newer is supported. On macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8282 --workers 1 --no-access-log
```

The default data directory is `./data`. Read `data/setup-token`, then complete setup in your browser. Set `DATA_DIR` to an absolute persistent path for a managed service. `PUBLIC_URL`, `SECURE_COOKIES`, and `FORWARDED_ALLOW_IPS` work the same way as in Docker.

**Use one worker.** Background polling, login throttling, and pending Plex sign-in challenges run inside the application process. Multiple workers would duplicate polling and split those in-memory records.

- Linux: adapt [deploy/reelarr.service.example](deploy/reelarr.service.example), create its service user and writable data directory, install the unit, and enable it with your service manager.
- Windows: run `powershell -ExecutionPolicy Bypass -File deploy/start.ps1` from the checkout. To start it automatically, create a Task Scheduler task triggered at startup or sign-in, using that script’s absolute path and a user that can read the checkout/write the data directory. Configure restart-on-failure in the task. Install dependencies once before registering an unattended task.
- macOS: Docker Desktop is the default option. A direct Python installation can also be registered with `launchd`, using the absolute Python executable, checkout working directory, and persistent `DATA_DIR`.

### Recover an administrator password

Recovery requires shell access to the server’s persistent data. It never sends a reset link externally.

```sh
docker compose exec -it reelarr python -m app.manage reset-admin YOUR_USERNAME
# Or, for a native installation:
.venv/bin/python -m app.manage reset-admin YOUR_USERNAME
```

Enter the new password at the hidden prompts. The command re-enables that local administrator and signs out existing sessions. Keep the same `DATA_DIR` as the running app.

### Backup

Stop the app briefly and back up the entire persistent data directory/volume, including `reelarr.sqlite3`, its journal files if present, and **encryption.key**. Keep them together: losing the encryption key makes stored service credentials unreadable. Restore the files with ownership readable/writable by the app’s user (`10001:10001` in the default container). Do not commit data, setup tokens, or keys to source control.

## Development and demo

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
# Isolated read-only sample preview, no integration credentials required:
DEMO_MODE=1 DATA_DIR=./data/demo .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8282
```

The demo bypasses sign-in for its **sample data only**, rejects account/configuration writes, and displays a visible demo label. Keep `DEMO_MODE` unset for a real installation. Its titles, episode numbering, release dates, and download values are illustrative rather than a factual release schedule.

Tests exercise authentication/CSRF, viewer permissions, admin preservation, credential encryption/redaction, session revocation, date ranges, download states, monitoring diffs, pagination, Plex ID matching/access checks, and partial-service outages through mock HTTP responses. GitHub Actions also builds and starts the Compose container, then verifies administrator setup, protected login, persistent storage, and login after a restart. Live connectivity still needs your own service URLs/credentials. Docker validation requires a Docker installation.

Runtime dependencies are pinned in `requirements.lock`; the UI uses local system fonts and no third-party CDN assets. The container health check tests the app itself, so one unavailable upstream does not restart an otherwise healthy calendar.

## API references

The adapters use the [Sonarr v3 API](https://sonarr.tv/docs/api/), [Radarr v3 API](https://radarr.video/docs/api/), and [qBittorrent Web API v2](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29). Plex library/PIN behavior follows the endpoints documented by the maintained [Python PlexAPI project](https://python-plexapi.readthedocs.io/en/latest/modules/myplex.html).

Reelarr is an independent project and is not affiliated with Plex, Sonarr, Radarr, or qBittorrent.
