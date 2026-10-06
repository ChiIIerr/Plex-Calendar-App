"""Real process or Windows scheduled-task checks using isolated, disposable state."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows-state", type=Path, help="CI-only: exercise an installed Windows startup task")
    args = parser.parse_args()
    if args.windows_state and (os.name != "nt" or os.environ.get("CI") != "true"):
        parser.error("Scheduled-task smoke checks require an isolated Windows CI installation.")
    with tempfile.TemporaryDirectory(prefix="calendarr-native-") as temporary:
        process = None
        output = None
        if args.windows_state:
            metadata = json.loads((args.windows_state / "installation.json").read_text(encoding="utf-8-sig"))
            config_path = Path(metadata["config_path"])
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = {"data_dir": str(Path(temporary) / "data"), "log_dir": str(Path(temporary) / "logs"), "port": port}
            config_path = Path(temporary) / "server.json"
            config_path.write_text(json.dumps(config))
            output = open(Path(temporary) / "process.log", "w+")

        def control(command):
            nonlocal process
            if args.windows_state:
                script = ROOT / "deploy" / (command + ".ps1")
                subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-StateDir", str(args.windows_state)], check=True)
            elif command == "start":
                # Deliberately inherit a demo flag: the production entry point must remove it.
                env = os.environ | {"DEMO_MODE": "1"}
                process = subprocess.Popen([sys.executable, "-m", "app.server", "--config", str(config_path)], cwd=ROOT, env=env, stdout=output, stderr=output)
            elif process:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process = None

        base = f'http://127.0.0.1:{config["port"]}'
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, payload=None, csrf=None, method=None):
            headers = {"X-Calendarr-Request": "1", "Content-Type": "application/json"}
            if csrf:
                headers["X-CSRF-Token"] = csrf
            data = json.dumps(payload).encode() if payload is not None else None
            with browser.open(urllib.request.Request(base + path, data=data, headers=headers, method=method), timeout=3) as response:
                return json.load(response)

        def ready():
            for _ in range(60):
                if process and process.poll() is not None:
                    output.flush()
                    output.seek(0)
                    raise AssertionError("Server exited: " + output.read())
                try:
                    if request("/healthz")["status"] == "ok":
                        return
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.25)
            raise AssertionError("Server readiness timed out.")

        try:
            if not args.windows_state:
                control("start")
            ready()
            assert request("/api/bootstrap")["setup_required"]
            try:
                request("/api/dashboard")
                raise AssertionError("Unauthenticated calendar access was allowed.")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            token = (Path(config["data_dir"]) / "setup-token").read_text().strip()
            password = secrets.token_urlsafe(32)
            auth = request("/api/auth/setup", {"token": token, "username": "ci-admin", "password": password})
            assert request("/api/session")["user"]["role"] == "admin"
            assert not (Path(config["data_dir"]) / "setup-token").exists()
            # Persist a preference across both a restart and an installer rerun.
            settings = request("/api/settings")["settings"]
            for source in ("sonarr", "radarr", "plex", "qbittorrent"):
                settings[source] = {k: v for k, v in settings[source].items() if not k.startswith("has_")}
            settings["preferences"]["app_name"] = "Native persistence check"
            request("/api/settings", settings, csrf=auth["csrf"], method="PUT")
            request("/api/auth/logout", {}, csrf=auth["csrf"])
            key_before = (Path(config["data_dir"]) / "encryption.key").read_bytes()
            control("stop")
            control("start")
            ready()
            auth = request("/api/auth/login", {"username": "ci-admin", "password": password})
            assert request("/api/session")["user"]["role"] == "admin"
            assert request("/api/settings")["settings"]["preferences"]["app_name"] == "Native persistence check"
            request("/api/auth/logout", {}, csrf=auth["csrf"])
            if args.windows_state:
                # Exercise the actual update path with the same source and existing config.
                subprocess.run([
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy" / "install.ps1"),
                    "-StateDir", str(args.windows_state), "-InstallDir", metadata["install_dir"], "-TaskName", metadata["task_name"], "-PythonExe", sys.executable,
                ], check=True)
                ready()
                auth = request("/api/auth/login", {"username": "ci-admin", "password": password})
                assert request("/api/settings")["settings"]["preferences"]["app_name"] == "Native persistence check"
                request("/api/auth/logout", {}, csrf=auth["csrf"])
            assert (Path(config["data_dir"]) / "encryption.key").read_bytes() == key_before
            assert (Path(config["log_dir"]) / "server.log").exists()
            print("Native setup, protected login, restart, data/key persistence, and logging checks passed.")
        finally:
            control("stop")
            if output:
                output.close()


if __name__ == "__main__":
    main()
