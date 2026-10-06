"""Read-only clients for Sonarr, Radarr, Plex and qBittorrent."""
import re
from urllib.parse import urlencode, urlsplit

import httpx


class IntegrationError(Exception):
    pass


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an http(s) base URL without credentials, a query, or a fragment.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid port.")
    return value.rstrip("/")


def safe_error(error):
    if isinstance(error, httpx.HTTPStatusError):
        return f"Service returned HTTP {error.response.status_code}. Check its URL and credentials."
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "Connection timed out. Check the service address and network."
    if isinstance(error, httpx.RequestError):
        return "Could not connect. Check the service address, TLS certificate and network."
    if isinstance(error, IntegrationError):
        return str(error)
    return "The service returned an unexpected response. Check the service version."


class Clients:
    def __init__(self, transport=None):
        self.transport = transport

    def client(self, config=None, headers=None):
        return httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8), headers=headers,
                                 follow_redirects=False, transport=self.transport, trust_env=False)

    async def arr(self, config, path, params=None):
        async with self.client(headers={"X-Api-Key": config["api_key"]}) as client:
            response = await client.get(config["url"] + "/api/v3/" + path, params=params)
            response.raise_for_status()
            return response.json()

    async def queue(self, config, source):
        records, page = [], 1
        while True:
            data = await self.arr(config, "queue", {
                "page": page, "pageSize": 250, "includeUnknownSeriesItems": "false",
                "includeSeries": "true", "includeEpisode": "true", "includeMovie": "true"})
            chunk = data.get("records", [])
            records.extend(chunk)
            if not chunk or len(records) >= data.get("totalRecords", len(records)):
                return records
            page += 1
            if page > 1000:
                raise IntegrationError("Queue pagination exceeded the supported limit.")

    async def qbittorrent(self, config):
        parsed = urlsplit(config["url"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with self.client(headers={"Referer": config["url"] + "/", "Origin": origin}) as client:
            result = await client.post(config["url"] + "/api/v2/auth/login",
                                       data={"username": config["username"], "password": config["password"]})
            result.raise_for_status()
            if result.text.strip() != "Ok.":
                raise IntegrationError("qBittorrent rejected the Web UI username or password.")
            response = await client.get(config["url"] + "/api/v2/torrents/info")
            response.raise_for_status()
            return response.json()

    async def plex_get(self, config, path, params=None):
        async with self.client(headers={"X-Plex-Token": config["token"], "Accept": "application/json"}) as client:
            response = await client.get(config["url"] + path, params=params)
            response.raise_for_status()
            return response.json().get("MediaContainer", {})

    async def plex_pages(self, config, path, params=None, key="Metadata"):
        result, offset = [], 0
        while True:
            data = await self.plex_get(config, path, {**(params or {}),
                "X-Plex-Container-Start": offset, "X-Plex-Container-Size": 500, "includeGuids": 1})
            chunk = data.get(key, [])
            result.extend(chunk)
            offset += len(chunk)
            if not chunk or offset >= data.get("totalSize", data.get("size", len(chunk))):
                return result
            if offset > 2000000:
                raise IntegrationError("Plex library exceeded the supported pagination limit.")

    async def plex_index(self, config):
        identity = await self.plex_get(config, "/")
        machine_id = identity.get("machineIdentifier")
        if not machine_id:
            raise IntegrationError("Plex did not return a server identifier. Use your Plex Media Server URL.")
        sections = (await self.plex_get(config, "/library/sections")).get("Directory", [])
        index = {"machine_id": machine_id, "name": identity.get("friendlyName", "Plex"),
                 "movies": {}, "episodes": {}}
        for section in sections:
            kind, section_id = section.get("type"), str(section.get("key", ""))
            if kind not in {"movie", "show"} or not section_id.isdigit():
                continue
            media = await self.plex_pages(config, f"/library/sections/{section_id}/all")
            if kind == "movie":
                for movie in media:
                    if not playable(movie):
                        continue
                    for guid in external_guids(movie):
                        index["movies"][guid] = {"rating_key": str(movie["ratingKey"]), "added_at": movie.get("addedAt")}
            else:
                shows = {str(s["ratingKey"]): external_guids(s) for s in media}
                episodes = await self.plex_pages(config, f"/library/sections/{section_id}/all", {"type": 4})
                for episode in episodes:
                    if not playable(episode):
                        continue
                    for guid in shows.get(str(episode.get("grandparentRatingKey")), []):
                        key = f"{guid}:{episode.get('parentIndex', 0)}:{episode.get('index', 0)}"
                        index["episodes"][key] = {"rating_key": str(episode["ratingKey"]), "added_at": episode.get("addedAt")}
        return index

    @staticmethod
    def plex_headers(client_id, token=None):
        result = {"Accept": "application/json", "X-Plex-Client-Identifier": client_id,
                  "X-Plex-Product": "Reelarr Calendar", "X-Plex-Version": "1.0.0",
                  "X-Plex-Platform": "Web", "X-Plex-Device": "Browser"}
        if token:
            result["X-Plex-Token"] = token
        return result

    async def plex_pin(self, client_id, pin_id=None, code=None):
        async with self.client(headers=self.plex_headers(client_id)) as client:
            if pin_id:
                result = await client.get(f"https://plex.tv/api/v2/pins/{pin_id}", params={"code": code})
            else:
                result = await client.post("https://plex.tv/api/v2/pins", data={"strong": "true"})
            result.raise_for_status()
            return result.json()

    async def plex_account(self, client_id, token, machine_id):
        async with self.client(headers=self.plex_headers(client_id, token)) as client:
            response = await client.get("https://plex.tv/api/v2/resources", params={"includeHttps": 1, "includeRelay": 1})
            response.raise_for_status()
            resources = response.json()
            if not any(r.get("clientIdentifier") == machine_id and "server" in r.get("provides", "") for r in resources):
                raise IntegrationError("Your Plex account does not have access to the configured Plex server.")
            response = await client.get("https://plex.tv/api/v2/user")
            response.raise_for_status()
            return response.json()


def playable(item):
    return any(part.get("exists", True) not in {False, 0, "0"} and not part.get("deletedAt")
               for media in item.get("Media", []) for part in media.get("Part", []))


def external_guids(item):
    result = []
    for guid in [item.get("guid", ""), *[g.get("id", "") for g in item.get("Guid", [])]]:
        match = re.search(r"(?:^|agents\.)(tvdb|thetvdb|tmdb|themoviedb|imdb)://([^/?]+)", guid)
        if match:
            provider = {"thetvdb": "tvdb", "themoviedb": "tmdb"}.get(match[1], match[1])
            result.append(f"{provider}://{match[2]}")
    return list(set(result))


def plex_auth_url(client_id, code):
    return "https://app.plex.tv/auth#?" + urlencode({"clientID": client_id, "code": code,
        "context[device][product]": "Reelarr Calendar"})
