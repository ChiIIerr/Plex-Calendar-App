"""Poll services independently and combine their status without guessing Plex availability."""
import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from .integrations import safe_error


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def future_release(event):
    value = event.get("date")
    if not value:
        return False
    try:
        if event.get("all_day") or len(value) == 10:
            return value[:10] > datetime.now(timezone.utc).date().isoformat()
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return when > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def release_date(movie):
    dates = [(movie.get(field), label) for field, label in
             [("digitalRelease", "Digital release"), ("physicalRelease", "Physical release")]
             if movie.get(field)]
    if dates:
        return min(dates, key=lambda x: x[0])
    if movie.get("inCinemas"):
        return movie["inCinemas"], "In theaters · home release TBD"
    return None, "Release date TBD"


def download_state(queue, torrent=None):
    size = queue.get("size", 0) or 0
    remaining = queue.get("sizeleft", size) or 0
    progress = max(0, min(1, 1 - remaining / size)) if size else 0
    state = str(queue.get("status", "queued")).lower()
    health = str(queue.get("trackedDownloadStatus", "")).lower()
    tracked = str(queue.get("trackedDownloadState", "")).lower()
    eta, speed = queue.get("timeleft"), None
    if torrent:
        progress = max(0, min(1, torrent.get("progress", 0)))
        state = torrent.get("state", "unknown").lower()
        eta = torrent.get("eta")
        speed = torrent.get("dlspeed", 0)
    if health in {"warning", "error"} or "failed" in tracked or state in {"error", "missingfiles"}:
        status, note = "issue", "Download or import needs attention in the source application."
    elif progress >= 1 or state in {"uploading", "stalledup", "queuedup", "pausedup", "stoppedup", "forcedup"} or state == "completed":
        status, note = "importing", "Download finished. Waiting for import and Plex library confirmation."
    elif state in {"queueddl", "queued", "pauseddl", "stoppeddl", "paused"}:
        status, note = "queued", "Waiting in the download client queue, or paused."
    elif state in {"stalleddl", "metadl", "forcedmetadl", "checkingdl", "checkingresumedata", "allocating"}:
        status, note = "queued", "Waiting for peers, metadata, or file checks."
    else:
        status, note = "downloading", "Downloading in the configured client."
    return {"status": status, "progress": round(progress, 4), "eta": eta, "speed": speed,
            "client_state": state, "note": note, "client": queue.get("downloadClient", "qBittorrent" if torrent else "Download client")}


def plex_match(event, index):
    if event["kind"] == "movie":
        for guid in [f"tmdb://{event.get('tmdb_id')}", f"imdb://{event.get('imdb_id')}"]:
            if guid in index.get("movies", {}):
                return index["movies"][guid]
    else:
        key = f"tvdb://{event.get('tvdb_id')}:{event.get('season')}:{event.get('episode')}"
        return index.get("episodes", {}).get(key)
    return None


def decorate(event, queues, torrents, plex_index, plex_status):
    # A file reported by Arr is never treated as confirmed Plex availability.
    match = plex_match(event, plex_index)
    queue = next((q for q in queues if
                  (event["kind"] == "movie" and q.get("movieId") == event["external_id"]) or
                  (event["kind"] == "episode" and (q.get("episodeId") or (q.get("episode") or {}).get("id")) == event["external_id"])), None)
    event.update({"progress": None, "eta": None, "speed": None, "plex_url": None,
                  "plex_checked_at": plex_status.get("last_success"), "stale": False})
    if match:
        event.update(status="available", note="Confirmed in your Plex library.")
        event["plex_url"] = "https://app.plex.tv/desktop/#!/server/" + quote(plex_index.get("machine_id", ""), safe="") + "/details?key=" + quote("/library/metadata/" + match["rating_key"], safe="")
        event["stale"] = plex_status.get("state") != "connected"
        if event["stale"]:
            event["note"] = "Last confirmed in Plex; its connection is currently unavailable."
    elif queue:
        download_id = str(queue.get("downloadId", "")).lower()
        event.update(download_state(queue, torrents.get(download_id)))
    elif event.get("has_file"):
        event.update(status="importing", note="The source app has a file. Awaiting confirmation in Plex.")
    elif not event.get("monitored"):
        event.update(status="unmonitored", note="This item is not currently monitored by the source app.")
    elif future_release(event):
        event.update(status="upcoming", note="Scheduled release. Plex availability depends on download and import.")
    else:
        event.update(status="waiting", note="Monitored and waiting for an available release.")
    return event


def movie_event(movie):
    date, label = release_date(movie)
    return {"id": f"radarr-{movie['id']}", "source": "radarr", "external_id": movie["id"],
            "title": movie.get("title", "Untitled movie"), "subtitle": str(movie.get("year", "")),
            "kind": "movie", "date": date, "date_label": label, "all_day": True,
            "monitored": movie.get("monitored", False), "has_file": movie.get("hasFile", False),
            "tmdb_id": movie.get("tmdbId"), "imdb_id": movie.get("imdbId"),
            "overview": movie.get("overview", ""), "genres": movie.get("genres", []),
            "added": movie.get("added"), "runtime": movie.get("runtime"),
            "releases": {k: movie.get(k) for k in ["inCinemas", "digitalRelease", "physicalRelease"]},
            "poster": f"/api/poster/radarr/{movie['id']}"}


def episode_event(episode, series):
    season, number = episode.get("seasonNumber", 0), episode.get("episodeNumber", 0)
    air_date = episode.get("airDateUtc") or episode.get("airDate")
    return {"id": f"sonarr-{episode['id']}", "source": "sonarr", "external_id": episode["id"],
            "series_id": series.get("id"), "title": series.get("title", "Unknown series"),
            "subtitle": f"S{season:02d} E{number:02d} · {episode.get('title', 'TBA')}",
            "kind": "episode", "date": air_date, "date_label": "Episode airs",
            "all_day": not episode.get("airDateUtc"), "season": season, "episode": number,
            "monitored": bool(series.get("monitored", False) and episode.get("monitored", False)),
            "has_file": episode.get("hasFile", False), "tvdb_id": series.get("tvdbId"),
            "overview": episode.get("overview") or series.get("overview", ""),
            "genres": series.get("genres", []), "runtime": series.get("runtime"), "added": series.get("added"),
            "poster": f"/api/poster/sonarr/{series.get('id', 0)}"}


class SyncService:
    def __init__(self, store, clients):
        self.store, self.clients = store, clients
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.calendar_lock = asyncio.Lock()

    def status(self):
        return {name: self.store.cache(f"status:{name}", {"state": "not_configured", "last_success": None,
                 "error": None}) for name in ["sonarr", "radarr", "plex", "qbittorrent"]}

    async def run(self):
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep the poller alive if one cycle fails; details stay out of logs that may contain credentials.
                self.store.set_cache("sync_error", "An unexpected sync error occurred. Try a manual refresh.")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.store.settings()["preferences"]["poll_seconds"])
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def refresh(self, force_plex=False):
        if self.lock.locked():
            return False
        async with self.lock:
            settings = self.store.settings()
            await asyncio.gather(*[self.refresh_source(name, settings, force_plex) for name in
                                   ["sonarr", "radarr", "plex", "qbittorrent"]])
            self.record_availability()
            self.store.set_cache("last_sync", now_iso())
            self.store.set_cache("sync_error", None)
            return True

    async def refresh_source(self, name, settings, force_plex):
        config = settings[name]
        if not config["enabled"]:
            self.store.set_cache(f"status:{name}", {"state": "not_configured", "last_success": None, "error": None})
            return
        old = self.store.cache(f"status:{name}", {})
        if not force_plex and old.get("retry_at", 0) > time.time():
            return
        if name == "plex" and not force_plex and old.get("last_success"):
            age = time.time() - datetime.fromisoformat(old["last_success"].replace("Z", "+00:00")).timestamp()
            if age < settings["preferences"]["plex_poll_seconds"] and old.get("state") == "connected":
                return
        try:
            if name == "sonarr":
                start = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
                end = (datetime.now(timezone.utc) + timedelta(days=366)).date().isoformat()
                catalog, calendar, queue = await asyncio.gather(self.clients.arr(config, "series"),
                    self.clients.arr(config, "calendar", {"start": start,
                        "end": (date.fromisoformat(end) + timedelta(days=1)).isoformat(),
                        "unmonitored": "true", "includeSeries": "true"}),
                    self.clients.queue(config, name))
                data = {"catalog": catalog, "calendar": calendar, "queue": queue, "coverage": [start, end]}
            elif name == "radarr":
                catalog, queue = await asyncio.gather(self.clients.arr(config, "movie"), self.clients.queue(config, name))
                data = {"catalog": catalog, "queue": queue}
            elif name == "plex":
                data = await self.clients.plex_index(config)
            else:
                data = await self.clients.qbittorrent(config)
            self.store.set_cache(name, data)
            if name in {"sonarr", "radarr"}:
                self.record_monitoring(name, data["catalog"])
            self.store.set_cache(f"status:{name}", {"state": "connected", "last_success": now_iso(), "error": None})
        except Exception as error:
            failures = old.get("failures", 0) + 1
            delay = min(1800, settings["preferences"]["poll_seconds"] * 2 ** min(failures, 6))
            self.store.set_cache(f"status:{name}", {"state": "error", "last_success": old.get("last_success"),
                                 "error": safe_error(error), "failures": failures, "retry_at": time.time() + delay})

    def record_monitoring(self, source, catalog):
        with self.store.connection() as db:
            old = {r["external_id"]: dict(r) for r in db.execute("SELECT * FROM monitored WHERE source=?", (source,))}
            for item in catalog:
                monitored = int(bool(item.get("monitored")))
                previous = old.pop(item["id"], None)
                kind = "movie" if source == "radarr" else "series"
                action, happened = None, now_iso()
                if previous is None:
                    action, happened = "added", item.get("added") or happened
                elif previous["monitored"] != monitored:
                    action = "monitoring_started" if monitored else "monitoring_paused"
                if action:
                    db.execute("INSERT INTO activity(source,title,kind,action,happened,external_id) VALUES (?,?,?,?,?,?)",
                               (source, item.get("title", "Untitled"), kind, action, happened, item["id"]))
                db.execute("INSERT OR REPLACE INTO monitored VALUES (?,?,?,?,?,?)",
                    (source, item["id"], item.get("title", "Untitled"), kind, monitored, item.get("added")))
            for item in old.values():
                db.execute("INSERT INTO activity(source,title,kind,action,happened,external_id) VALUES (?,?,?,?,?,?)",
                    (source, item["title"], item["kind"], "removed", now_iso(), item["external_id"]))
                db.execute("DELETE FROM monitored WHERE source=? AND external_id=?", (source, item["external_id"]))
            db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY happened DESC,id DESC LIMIT 5000)")

    def snapshots(self):
        settings = self.store.settings()
        return {name: self.store.cache(name, {} if name != "qbittorrent" else []) if settings[name]["enabled"]
                else ({} if name != "qbittorrent" else []) for name in ["sonarr", "radarr", "plex", "qbittorrent"]}

    def events(self, extra_episodes=None):
        data = self.snapshots()
        status = self.status()
        qbt_enabled = self.store.settings()["qbittorrent"]["enabled"]
        torrents = {}
        for torrent in data["qbittorrent"]:
            for key in ["hash", "infohash_v1", "infohash_v2"]:
                if torrent.get(key):
                    torrents[torrent[key].lower()] = torrent
        sonarr, radarr, index = data["sonarr"], data["radarr"], data["plex"]
        series = {s["id"]: s for s in sonarr.get("catalog", [])}
        episodes = {e["id"]: e for e in (extra_episodes if extra_episodes is not None else sonarr.get("calendar", []))}
        for queue in sonarr.get("queue", []):
            if queue.get("episode"):
                episodes.setdefault(queue["episode"]["id"], queue["episode"])
        events = [movie_event(m) for m in radarr.get("catalog", [])]
        for episode in episodes.values():
            show = series.get(episode.get("seriesId")) or episode.get("series", {})
            events.append(episode_event(episode, show))
        queues = sonarr.get("queue", []) + radarr.get("queue", [])
        for event in events:
            decorate(event, queues, torrents, index, status["plex"])
            event["stale"] |= status[event["source"]]["state"] != "connected"
            if event["status"] in {"downloading", "queued"} and qbt_enabled:
                event["stale"] |= status["qbittorrent"]["state"] != "connected"
        return events

    async def calendar(self, start, end):
        data = self.snapshots()["sonarr"]
        coverage = data.get("coverage")
        extra = None
        extra_stale = False
        if coverage and (start < coverage[0] or end > coverage[1]):
            async with self.calendar_lock:
                key = f"calendar:{start}:{end}"
                cached = self.store.cache(key, {})
                if time.time() - cached.get("time", 0) > 60:
                    try:
                        episodes = await self.clients.arr(self.store.settings()["sonarr"], "calendar", {
                            "start": start, "end": (date.fromisoformat(end) + timedelta(days=1)).isoformat(),
                            "unmonitored": "true", "includeSeries": "true"})
                        cached = {"episodes": episodes, "time": time.time()}
                        self.store.set_cache(key, cached)
                        with self.store.connection() as db:
                            db.execute("DELETE FROM cache WHERE key LIKE 'calendar:%' AND key NOT IN (SELECT key FROM cache WHERE key LIKE 'calendar:%' ORDER BY updated DESC LIMIT 120)")
                    except Exception as error:
                        if not cached:
                            raise error
                        extra_stale = True
                extra = cached["episodes"]
        # The caller uses an inclusive date range and filters timed episodes in browser local time.
        result = [event for event in self.events(extra) if event["date"] and start <= event["date"][:10] <= end]
        if extra_stale:
            for event in result:
                if event["source"] == "sonarr":
                    event["stale"] = True
        return result

    def library(self):
        data = self.snapshots()
        movies = {e["external_id"]: e for e in self.events() if e["kind"] == "movie"}
        result = list(movies.values())
        episode_counts = {}
        for key in data["plex"].get("episodes", {}):
            guid = key.rsplit(":", 2)[0]
            episode_counts[guid] = episode_counts.get(guid, 0) + 1
        for show in data["sonarr"].get("catalog", []):
            count = episode_counts.get(f"tvdb://{show.get('tvdbId')}", 0)
            result.append({"id": f"series-{show['id']}", "external_id": show["id"], "source": "sonarr",
                "title": show.get("title", "Untitled"), "subtitle": f"{count} episodes in Plex",
                "kind": "series", "monitored": show.get("monitored", False), "added": show.get("added"),
                "overview": show.get("overview", ""), "genres": show.get("genres", []),
                "status": "monitored" if show.get("monitored") else "unmonitored",
                "poster": f"/api/poster/sonarr/{show['id']}", "plex_episode_count": count})
        return sorted(result, key=lambda item: item.get("added") or "", reverse=True)

    def record_availability(self):
        previous = self.store.cache("event_states", {})
        events = self.events()
        with self.store.connection() as db:
            for event in events:
                if event["status"] == "available" and previous.get(event["id"]) not in {None, "available"}:
                    db.execute("INSERT INTO activity(source,title,kind,action,happened,external_id) VALUES (?,?,?,?,?,?)",
                        (event["source"], event["title"], event["kind"], "available", now_iso(), event["external_id"]))
        self.store.set_cache("event_states", {event["id"]: event["status"] for event in events})
