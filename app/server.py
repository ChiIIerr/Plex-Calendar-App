"""Native server entry point. Apply configuration before importing the ASGI app."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

DEFAULTS = {
    "bind_host": "127.0.0.1",
    "port": 8282,
    "public_url": "http://localhost:8282",
    "trusted_proxies": "127.0.0.1,::1",
}
KEYS = set(DEFAULTS) | {"data_dir", "log_dir"}


def load_config(path):
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read server configuration: {path}") from error
    if not isinstance(raw, dict) or set(raw) - KEYS:
        raise ValueError("Server configuration must be an object containing only documented settings.")
    config = DEFAULTS | raw
    if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("port must be an integer between 1 and 65535.")
    if "public_url" not in raw:
        config["public_url"] = f"http://localhost:{config['port']}"
    try:
        if not isinstance(config["bind_host"], str):
            raise ValueError
        ipaddress.ip_address(config["bind_host"])
    except (ValueError, TypeError):
        raise ValueError("bind_host must be a local IP address, such as 127.0.0.1 or 0.0.0.0.") from None
    public = config["public_url"]
    if not isinstance(public, str) or any(c.isspace() for c in public):
        raise ValueError("public_url must be an HTTP or HTTPS origin.")
    try:
        url = urlsplit(public)
        port = url.port
        valid = url.scheme in ("http", "https") and url.hostname and not url.username and not url.password
        valid = valid and url.path in ("", "/") and not url.query and not url.fragment
        valid = valid and (port is None or 1 <= port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("public_url must be an HTTP or HTTPS origin without credentials or a path prefix.")
    config["public_url"] = public.rstrip("/")
    proxies = config["trusted_proxies"]
    if not isinstance(proxies, str):
        raise ValueError("trusted_proxies must be a comma-separated list of proxy IPs or networks.")
    try:
        for entry in proxies.split(",") if proxies else []:
            if ipaddress.ip_network(entry.strip(), strict=False).prefixlen == 0:
                raise ValueError
    except ValueError:
        raise ValueError("trusted_proxies must contain specific IPs/networks; a wildcard is not allowed.") from None
    config["trusted_proxies"] = ",".join(entry.strip() for entry in proxies.split(",") if entry.strip())
    for key in ("data_dir", "log_dir"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a nonempty filesystem path.")
        directory = Path(value).expanduser()
        config[key] = str((path.parent / directory).resolve() if not directory.is_absolute() else directory.resolve())
    if config["data_dir"] == config["log_dir"]:
        raise ValueError("data_dir and log_dir must be different directories.")
    return config


def apply_environment(config):
    # An unattended installation must not inherit a development login bypass.
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("SETUP_TOKEN", None)
    os.environ["DATA_DIR"] = config["data_dir"]
    os.environ["PUBLIC_URL"] = config["public_url"]
    os.environ["SECURE_COOKIES"] = "true" if config["public_url"].startswith("https://") else "false"


def log_config(directory):
    Path(directory).mkdir(parents=True, exist_ok=True)
    handler = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(Path(directory) / "server.log"),
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "encoding": "utf-8",
        "formatter": "plain",
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {
            "file": handler,
            "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        },
        "root": {"handlers": ["file", "console"], "level": "WARNING"},
        "loggers": {
            "uvicorn": {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run the native Calendarr server")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Validate configuration without starting or modifying data")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except ValueError as error:
        parser.error(str(error))
    if args.check:
        print("Server configuration is valid.")
        return
    apply_environment(config)
    uvicorn.run(
        "app.main:app", host=config["bind_host"], port=config["port"], workers=1,
        access_log=False, proxy_headers=True, forwarded_allow_ips=config["trusted_proxies"],
        log_config=log_config(config["log_dir"]),
    )


if __name__ == "__main__":
    main()
