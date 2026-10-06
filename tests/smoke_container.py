"""Exercise an ephemeral CI Compose container, including persistent administrator login."""
import http.cookiejar
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request


def main():
    if os.environ.get("CI") != "true":
        raise SystemExit("This check only runs against the ephemeral CI test container.")
    base = "http://127.0.0.1:8282"
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, payload=None, csrf=None):
        headers = {"X-Reelarr-Request": "1", "Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(payload).encode() if payload is not None else None
        with browser.open(urllib.request.Request(base + path, data=data, headers=headers), timeout=5) as response:
            return json.load(response)

    assert request("/healthz")["status"] == "ok"
    assert request("/api/bootstrap")["setup_required"]
    try:
        request("/api/dashboard")
        raise AssertionError("An unauthenticated viewer could access the dashboard.")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    token = subprocess.check_output(["docker", "compose", "exec", "-T", "reelarr", "cat", "/data/setup-token"], text=True).strip()
    password = secrets.token_urlsafe(32)
    auth = request("/api/auth/setup", {"token": token, "username": "ci-admin", "password": password})
    assert request("/api/session")["user"]["role"] == "admin"
    assert not request("/api/bootstrap")["setup_required"]
    assert request("/api/settings")["settings"]["sonarr"]["api_key"] == ""
    request("/api/auth/logout", {}, csrf=auth["csrf"])
    subprocess.run(["docker", "compose", "restart", "-t", "2", "reelarr"], check=True)
    for attempt in range(45):
        try:
            request("/healthz")
            break
        except (urllib.error.URLError, OSError):
            if attempt == 44:
                raise
            time.sleep(1)
    auth = request("/api/auth/login", {"username": "ci-admin", "password": password})
    assert request("/api/session")["user"]["role"] == "admin"
    assert request("/api/dashboard")["stats"]["monitored"] == 0
    request("/api/auth/logout", {}, csrf=auth["csrf"])
    print("Container setup, protected login, storage persistence, and restart checks passed.")


if __name__ == "__main__":
    main()
