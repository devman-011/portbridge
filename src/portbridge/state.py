"""Runtime state (state.json): is forwarding active, PID, ports, health.

State is the source of truth for "what is PortBridge currently doing" --
separate from config.toml, which is just user preferences/defaults. Every
command that reads state first checks whether the recorded PID is still
alive and actually looks like a PortBridge monitor process; if not, the
state is stale (crash, reboot, kill -9) and gets cleaned up automatically.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from portbridge import paths

STOPPED = "STOPPED"
STARTING = "STARTING"
ACTIVE = "ACTIVE"
RECONNECTING = "RECONNECTING"
FAILED = "FAILED"


def default_state() -> dict[str, Any]:
    return {
        "status": STOPPED,
        "pid": None,
        "bind_address": None,
        "local_port": None,
        "external_port": None,
        "mode": None,
        "public_hostname": None,
        "started_at": None,
        "last_health_check": None,
        "health_ok": None,
        "health_detail": [],
        "reconnect_attempts": 0,
        "next_retry_at": None,
        "last_error": None,
    }


def load_state() -> dict[str, Any]:
    path = paths.state_file()
    if not path.exists():
        return default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_state()
    merged = default_state()
    merged.update(raw)
    return merged


def save_state(state: dict[str, Any]) -> None:
    paths.ensure_dirs()
    path = paths.state_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def clear_state() -> None:
    save_state(default_state())


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else -- treat as alive,
        # we simply won't be able to signal it (surfaces as a clear error
        # later rather than a silent false negative here).
        return True
    return True


def is_portbridge_process(pid: int | None) -> bool:
    """Best-effort guard against PID reuse: confirm /proc/<pid>/cmdline
    actually looks like one of our own processes before we ever signal it.
    """
    if not pid:
        return False
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        # Non-Linux or /proc unavailable: fall back to the liveness check
        # alone rather than refusing to operate.
        return is_pid_alive(pid)
    text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    return "portbridge" in text


def detect_and_clean_stale(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Returns (state, was_stale). If state claims to be running but the
    recorded process is gone, resets to STOPPED and reports it as stale.
    """
    if state["status"] in (ACTIVE, RECONNECTING, STARTING):
        pid = state.get("pid")
        if not is_pid_alive(pid) or not is_portbridge_process(pid):
            cleaned = default_state()
            cleaned["last_error"] = "Monitor process was not found (crash or reboot); state cleared."
            save_state(cleaned)
            return cleaned, True
    return state, False


def uptime_seconds(state: dict[str, Any]) -> float | None:
    started = state.get("started_at")
    if not started:
        return None
    return max(0.0, time.time() - started)


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
