"""Config file (config.toml) loading, defaults, validation, and saving.

No secrets are ever stored here. Tailscale authentication is delegated
entirely to `tailscale up` / `tailscale login`, which persist their own
credentials inside tailscaled's state directory -- PortBridge never sees or
stores an auth key beyond passing it straight through to that one command.
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

# The one fact that gates everything else in this tool: Tailscale Funnel can
# only publish these three fixed external TCP ports. Verified against
# https://tailscale.com/kb/1223/funnel and the CLI reference at
# https://tailscale.com/docs/reference/tailscale-cli/funnel (September 2026).
ALLOWED_EXTERNAL_PORTS = (443, 8443, 10000)
# Preference order when suggesting a substitute port: keep 443/8443 free for
# real HTTPS services where possible, offer the generic port first.
SUGGESTION_ORDER = (10000, 8443, 443)

FUNNEL_MODES = ("tcp", "tls-terminated-tcp")

DEFAULT_CONFIG: dict[str, Any] = {
    "network": {
        "bind_address": "127.0.0.1",
        "local_port": 0,
        "external_port": 10000,
    },
    "provider": {
        "name": "tailscale-funnel",
        # Tailscale's own docs state plainly that "Funnel only works over
        # TLS-encrypted connections" -- this applies to --tcp too, not just
        # HTTPS mode. --tcp ("raw") only means Tailscale passes the TLS
        # bytes through undecrypted; the connecting client still MUST
        # speak TLS to get past Tailscale's edge at all, so a genuinely
        # plain client (nc, a game client, ...) gets nothing through it.
        # tls-terminated-tcp is the mode that actually works with an
        # ordinary, non-TLS local service: Tailscale terminates the
        # mandatory TLS at its edge and hands your local service plain
        # bytes -- only the *remote* client needs a TLS-capable tool
        # (e.g. `ncat --ssl`, `openssl s_client`), your local side is
        # unaffected. See https://github.com/tailscale/tailscale/issues/14240
        # for another user hitting this with a plain TCP (Minecraft) client.
        "mode": "tls-terminated-tcp",
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


def suggest_external_port(in_use: set[int]) -> int | None:
    for port in SUGGESTION_ORDER:
        if port not in in_use:
            return port
    return None
