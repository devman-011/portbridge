"""XDG-compliant filesystem locations used by PortBridge.

Config lives under XDG_CONFIG_HOME (defaults to ~/.config); state and logs
live under XDG_STATE_HOME (defaults to ~/.local/state), matching the Linux
XDG Base Directory spec and the locations requested in the PortBridge spec.
"""

import os
from pathlib import Path

APP_NAME = "portbridge"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_NAME


def state_file() -> Path:
    return state_dir() / "state.json"


def log_file() -> Path:
    return state_dir() / "portbridge.log"


def relay_token_file() -> Path:
    """The relay's auth token, kept out of config.toml on purpose (config
    is meant to be readable/shareable; this file is chmod 600 and holds
    the one real secret PortBridge needs)."""
    return config_dir() / "relay_token"


def frpc_config_file() -> Path:
    """The frpc TOML config PortBridge generates from config.toml + the
    relay token each time forwarding starts -- not hand-edited."""
    return state_dir() / "frpc.generated.toml"


def ensure_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
