"""Config file (config.toml) loading, defaults, validation, and saving.

The relay auth token is the one real secret PortBridge needs, and it is
deliberately kept OUT of this file -- see paths.relay_token_file() and
relay.py. Everything in config.toml is safe to read, share, or commit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from portbridge import paths
from portbridge.errors import MalformedConfigError

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

DEFAULT_CONFIG: dict[str, Any] = {
    "network": {
        "bind_address": "127.0.0.1",
        "local_port": 0,
    },
    "relay": {
        # frps's control address/port -- where frpc connects to register
        # the tunnel. Left blank until 'portbridge setup' or 'configure'
        # fills these in from your own deployed relay (see README for how
        # to deploy one -- it's a small frps + socat pair on any host with
        # a public TCP port, e.g. Railway's TCP Proxy).
        "server_addr": "",
        "server_port": 0,
        # The port frps listens on internally for this tunnel's data --
        # matches allowPorts in frps.toml and the socat/relay bridge in
        # front of it.
        "remote_port": 0,
        # What remote clients actually connect to -- may differ from
        # server_addr/server_port if, like the reference deployment, a
        # separate bridge service fronts the actual data port.
        "public_addr": "",
        "public_port": 0,
    },
    "behavior": {
        "auto_start": False,
        "reconnect": True,
        "reconnect_max_attempts": 0,
        "reconnect_backoff_base_seconds": 2,
        "reconnect_backoff_max_seconds": 60,
        "health_check_interval_seconds": 15,
        "connect_timeout_seconds": 5,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 5_242_880,
        "backup_count": 3,
    },
}


def _deep_merge_defaults(cfg: dict, defaults: dict) -> dict:
    merged = dict(defaults)
    for key, value in defaults.items():
        if isinstance(value, dict):
            merged[key] = _deep_merge_defaults(cfg.get(key, {}), value)
        else:
            merged[key] = cfg.get(key, value)
    # Preserve unknown extra keys instead of silently dropping them.
    for key, value in cfg.items():
        if key not in merged:
            merged[key] = value
    return merged


def load_config() -> dict:
    path = paths.config_file()
    if not path.exists():
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
        return cfg
    try:
        raw = path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        raise MalformedConfigError(path, str(exc)) from exc
    return _deep_merge_defaults(parsed, DEFAULT_CONFIG)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def to_toml(cfg: dict) -> str:
    lines = []
    for section, values in cfg.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_scalar(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_config(cfg: dict) -> None:
    paths.ensure_dirs()
    path = paths.config_file()
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(to_toml(cfg), encoding="utf-8")
    os.replace(tmp, path)


def relay_configured(cfg: dict) -> bool:
    relay = cfg["relay"]
    return bool(
        relay["server_addr"] and relay["server_port"]
        and relay["remote_port"] and relay["public_addr"] and relay["public_port"]
    )


def load_relay_token() -> str | None:
    path = paths.relay_token_file()
    if not path.exists():
        return None
    token = path.read_text(encoding="utf-8").strip()
    return token or None


def save_relay_token(token: str) -> None:
    paths.ensure_dirs()
    path = paths.relay_token_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
