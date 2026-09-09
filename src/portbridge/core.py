"""Shared, side-effecting-but-silent logic used by both the CLI subcommands
and the interactive menu (cli.py) and the background monitor (monitor.py).

Nothing in this module prints to the terminal or prompts for input -- it
either returns data or raises a PortBridgeError. That keeps the same code
path exercised whether it's driven by a human at a TTY or by the detached
monitor process writing only to the log file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from portbridge import netcheck
from portbridge import tailscale as ts
from portbridge.errors import (
    ExternalPortInUseError,
    PermissionDeniedError,
    PortBridgeError,
)


@dataclass
class Probe:
    tailscale_installed: bool = False
    tailscale_version: str | None = None
    version_ok: bool = True
    daemon_reachable: bool = False
    authenticated: bool = False
    backend_state: str = "Unknown"
    connected: bool = False
    dns_name: str | None = None
    health_problems: list[str] = field(default_factory=list)
    status_error: str | None = None


def probe_tailscale() -> Probe:
    p = Probe()
    p.tailscale_installed = ts.binary_path() is not None
    if not p.tailscale_installed:
        return p
    try:
        p.tailscale_version = ts.get_version()
        p.version_ok = ts.version_at_least()
    except PortBridgeError as exc:
        p.status_error = exc.message
    try:
        status = ts.get_status()
        p.daemon_reachable = True
        p.backend_state = ts.backend_state(status)
        p.authenticated = ts.is_authenticated(status)
        p.connected = ts.self_online(status)
        p.dns_name = ts.self_dns_name(status)
        p.health_problems = ts.health_problems(status)
    except PortBridgeError as exc:
        p.status_error = p.status_error or exc.message
    return p


def probe_local(bind_address: str, port: int | None) -> bool | None:
    if not port:
        return None
    return netcheck.is_port_listening(bind_address, port, timeout=1.0)


def probe_funnel_mapped(external_port: int | None) -> bool | None:
    if not external_port:
        return None
    try:
        return ts.is_port_funneled(external_port)
    except PortBridgeError:
        return None


def funnel_prereqs(probe: Probe) -> list[str]:
    missing: list[str] = []
    if not probe.tailscale_installed:
        return ["tailscale is not installed"]
    if not probe.daemon_reachable:
        return [probe.status_error or "tailscaled is not reachable"]
    if not probe.authenticated:
        missing.append(f"not logged in (state: {probe.backend_state})")
    if not probe.version_ok:
        missing.append(
            f"tailscale version is older than the minimum required {ts.MIN_VERSION}"
        )
    missing.extend(probe.health_problems)
    return missing


def _classify_funnel_error(stderr: str, external_port: int) -> PortBridgeError:
    text = (stderr or "").strip()
    lowered = text.lower()
    if "node attribute" in lowered or "not permitted" in lowered or "acl" in lowered:
        return PortBridgeError(
            f"Tailscale refused to enable Funnel: {text}",
            "Grant the 'funnel' node attribute in your tailnet policy file. "
            "See https://tailscale.com/kb/1223/funnel",
        )
    if "already" in lowered and ("use" in lowered or "serve" in lowered):
        return ExternalPortInUseError(external_port, "another local mapping")
    if "permission" in lowered or "must be root" in lowered or "access is not allowed" in lowered:
        return PermissionDeniedError(text)
    return PortBridgeError(
        f"'tailscale funnel' failed: {text or 'unknown error'}",
        "Run 'tailscale funnel status' for details, or see "
        "https://tailscale.com/kb/1223/funnel",
    )


def apply_funnel(
    bind_address: str,
    local_port: int,
    external_port: int,
    mode: str,
    allow_sudo: bool = True,
    timeout: float = 30,
) -> None:
    result = ts.funnel_apply(
        bind_address, local_port, external_port, mode, allow_sudo=allow_sudo, timeout=timeout
    )
    if result.returncode != 0:
        raise _classify_funnel_error(result.stderr, external_port)


def remove_funnel(
    bind_address: str,
    local_port: int,
    external_port: int,
    mode: str,
    allow_sudo: bool = True,
    timeout: float = 30,
) -> None:
    result = ts.funnel_remove(
        bind_address, local_port, external_port, mode, allow_sudo=allow_sudo, timeout=timeout
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "not found" in stderr or "no such" in stderr or "not configured" in stderr or "not running" in stderr:
            return  # Already off -- treat as success, matches idempotent "stop".
        raise _classify_funnel_error(result.stderr, external_port)


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    if attempt < 1:
        attempt = 1
    return min(cap, base * (2 ** (attempt - 1)))


def build_status_snapshot(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    net = cfg["network"]
    bind = state.get("bind_address") or net["bind_address"]
    local_port = state.get("local_port") or (net["local_port"] or None)
    external_port = state.get("external_port")

    probe = probe_tailscale()
    local_listening = probe_local(bind, local_port)
    funnel_mapped = probe_funnel_mapped(external_port) if state.get("status") == "ACTIVE" else None

    return {
        "probe": probe,
        "bind": bind,
        "local_port": local_port,
        "external_port": external_port,
        "local_listening": local_listening,
        "funnel_mapped": funnel_mapped,
        "state": state,
    }
