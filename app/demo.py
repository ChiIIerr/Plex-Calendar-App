"""Explicitly labelled, isolated sample data for the local design preview."""
from datetime import datetime, timedelta, timezone


def events_data():
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1)
    seeds = [
        (2, "The Last Horizon", "episode", "S02 E04 · A quiet signal", "available", "blue"),
        (4, "Echoes of Earth", "movie", "2026 · Adventure, sci-fi", "available", "sage"),
        (5, "Severance", "episode", "S03 E01 · The other side", "downloading", "red"),
        (5, "The Bear", "episode", "S05 E03 · Family meal", "downloading", "amber"),
        (6, "Project Hail Mary", "movie", "2026 · Science fiction", "queued", "blue"),
        (6, "Silo", "episode", "S03 E02 · Beneath the surface", "queued", "sage"),
        (8, "Slow Horses", "episode", "S06 E01 · New orders", "upcoming", "amber"),
        (9, "The Odyssey", "movie", "2026 · Adventure", "upcoming", "blue"),
        (9, "Foundation", "episode", "S04 E05 · The long night", "upcoming", "purple"),
        (11, "The White Lotus", "episode", "S04 E02 · Reservations", "upcoming", "sage"),
        (12, "Severance", "episode", "S03 E02 · Out of office", "upcoming", "red"),
        (13, "Silo", "episode", "S03 E03 · After the fall", "upcoming", "sage"),
        (15, "Slow Horses", "episode", "S06 E02 · Loose ends", "upcoming", "amber"),
        (16, "Dune: Part Three", "movie", "2026 · Science fiction", "upcoming", "sand"),
        (16, "Foundation", "episode", "S04 E06 · Far from home", "upcoming", "purple"),
        (18, "The White Lotus", "episode", "S04 E03 · High season", "upcoming", "sage"),
        (19, "Severance", "episode", "S03 E03 · Exit interview", "upcoming", "red"),
        (20, "Silo", "episode", "S03 E04 · Open doors", "upcoming", "sage"),
        (22, "Slow Horses", "episode", "S06 E03 · The handoff", "upcoming", "amber"),
        (23, "Wildwood", "movie", "2026 · Fantasy, animation", "upcoming", "sage"),
        (25, "The White Lotus", "episode", "S04 E04 · Check out", "upcoming", "sage"),
        (26, "Severance", "episode", "S03 E04 · A familiar face", "upcoming", "red"),
        (29, "The Night Shift", "movie", "2026 · Thriller", "unmonitored", "purple"),
    ]
    # Move the sample download cards to today; the labels are fictional preview data.
    events = []
    for number, (day, title, kind, subtitle, status, color) in enumerate(seeds):
        when = first + timedelta(days=day - 1)
        if status in {"downloading", "queued"}:
            when = today + timedelta(days=0 if status == "downloading" else 1)
        source = "radarr" if kind == "movie" else "sonarr"
        events.append({"id": f"demo-{number}", "external_id": number, "title": title, "subtitle": subtitle,
            "kind": kind, "source": source, "status": status, "date": when.isoformat() + ("T20:00:00Z" if kind == "episode" else ""),
            "date_label": "Digital release" if kind == "movie" else "Episode airs", "all_day": kind == "movie",
            "monitored": status != "unmonitored", "has_file": status == "available", "added": (today - timedelta(days=number + 1)).isoformat() + "T15:00:00Z",
            "overview": "Sample content for the preview. Connect your services to see real release dates, download progress, and availability from your own library.",
            "genres": ["Drama", "Science fiction"] if kind == "episode" else ["Adventure"], "runtime": 48 if kind == "episode" else 126,
            "season": 3, "episode": number, "poster": None, "color": color, "stale": False,
            "progress": (0.68 if title == "Severance" else 0.34) if status == "downloading" else (0 if status == "queued" else None),
            "eta": 740 if title == "Severance" else 1320, "speed": 12400000 if status == "downloading" else 0,
            "client": "qBittorrent", "note": "Sample data · release dates and availability are illustrative.", "plex_url": None})
    return events


def activity_data():
    now = datetime.now(timezone.utc)
    return [{"id": i, "title": title, "source": source, "kind": kind, "action": action,
             "external_id": i, "happened": (now - timedelta(hours=hours)).isoformat()}
            for i, (title, source, kind, action, hours) in enumerate([
                ("Project Hail Mary", "radarr", "movie", "added", 0.2),
                ("Severance", "sonarr", "series", "monitoring_started", 1),
                ("Echoes of Earth", "radarr", "movie", "available", 3),
                ("Slow Horses", "sonarr", "series", "added", 4),
                ("Silo", "sonarr", "series", "monitoring_started", 7),
                ("The Last Horizon", "sonarr", "episode", "available", 12),
                ("The Night Shift", "radarr", "movie", "monitoring_paused", 24)])]


def library_data():
    seen, result = set(), []
    for item in events_data():
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        row = dict(item)
        if row["kind"] == "episode":
            row.update(kind="series", status="monitored", subtitle="Drama · TV series", plex_episode_count=18)
        result.append(row)
    return result


def dashboard_data():
    events = events_data()
    now = datetime.now(timezone.utc).isoformat()
    return {"services": {name: {"state": "connected", "last_success": now, "error": None}
            for name in ["sonarr", "radarr", "plex", "qbittorrent"]}, "last_sync": now, "syncing": False,
            "sync_error": None, "stats": {"this_week": 7, "downloading": 2, "queued": 2, "monitored": 12},
            "downloads": [e for e in events if e["status"] in {"downloading", "queued"}], "activity": activity_data()[:6]}
