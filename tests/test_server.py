"""Configuration failures must not turn an unattended server into an unsafe demo."""
import json
import logging.config
from pathlib import Path

import pytest

from app.server import apply_environment, load_config, log_config


def write_config(tmp_path, **changes):
    config = {"data_dir": "data", "log_dir": "logs"} | changes
    path = tmp_path / "server.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_config_resolves_storage_from_file_not_working_directory(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    other = tmp_path / "unrelated"
    other.mkdir()
    monkeypatch.chdir(other)
    config = load_config(path)
    assert config["data_dir"] == str(tmp_path / "data")
    assert config["log_dir"] == str(tmp_path / "logs")
    assert not (tmp_path / "data").exists()  # Validation must not create/reset a database.


@pytest.mark.parametrize("changes", [
    {"port": True}, {"port": 0}, {"port": 65536}, {"port": "8282"},
    {"bind_host": "public.example.com"}, {"bind_host": None}, {"bind_host": True},
    {"public_url": "https://user:secret@example.com"},
    {"public_url": "https://example.com/calendar"},
    {"public_url": "https://example.com?token=secret"},
    {"public_url": "https://example.com#fragment"},
    {"public_url": "ftp://example.com"}, {"public_url": "https://example.com:99999"},
    {"public_url": "https://example.com\n"}, {"public_url": None},
    {"trusted_proxies": "*"}, {"trusted_proxies": "127.0.0.1,"},
    {"trusted_proxies": "0.0.0.0/0"}, {"trusted_proxies": "::/0"},
    {"trusted_proxies": ["127.0.0.1"]}, {"unknown_key": True},
    {"data_dir": ""}, {"data_dir": []}, {"data_dir": "logs"},
])
def test_invalid_config_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, **changes))


def test_config_handles_windows_utf8_bom_and_proxy_networks(tmp_path):
    path = write_config(tmp_path, public_url="https://calendar.example.com/", trusted_proxies="127.0.0.1, 10.0.0.0/24, ::1")
    path.write_text(path.read_text(), encoding="utf-8-sig")
    config = load_config(path)
    assert config["public_url"] == "https://calendar.example.com"
    assert config["trusted_proxies"] == "127.0.0.1,10.0.0.0/24,::1"


def test_default_public_origin_uses_custom_port(tmp_path):
    assert load_config(write_config(tmp_path, port=18282))["public_url"] == "http://localhost:18282"


def test_production_config_overrides_inherited_demo_and_storage(tmp_path, monkeypatch):
    for key, value in {"DEMO_MODE": "1", "SETUP_TOKEN": "inherited-token", "DATA_DIR": "wrong", "SECURE_COOKIES": "false", "PUBLIC_URL": "http://wrong"}.items():
        monkeypatch.setenv(key, value)
    config = load_config(write_config(tmp_path, public_url="https://calendar.example.com"))
    apply_environment(config)
    import os
    assert "DEMO_MODE" not in os.environ
    assert "SETUP_TOKEN" not in os.environ
    assert os.environ["DATA_DIR"] == str(tmp_path / "data")
    assert os.environ["SECURE_COOKIES"] == "true"


def test_logs_are_written_and_rotated(tmp_path):
    config = log_config(tmp_path / "logs")
    # Isolate the logger so a test doesn't alter pytest's/root's handlers.
    file_config = config["handlers"]["file"] | {"maxBytes": 128}
    logging.config.dictConfig({
        "version": 1, "disable_existing_loggers": False,
        "formatters": config["formatters"], "handlers": {"testfile": file_config},
        "loggers": {"calendarr-test": {"handlers": ["testfile"], "level": "INFO", "propagate": False}},
    })
    logger = logging.getLogger("calendarr-test")
    try:
        for index in range(8):
            logger.info("Server startup record %s with enough text to force rotation", index)
        assert "startup record" in (tmp_path / "logs" / "server.log").read_text()
        assert (tmp_path / "logs" / "server.log.1").exists()
        assert len(list((tmp_path / "logs").glob("server.log*"))) <= 6
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
