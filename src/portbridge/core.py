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

from portbridge import config as config_mod
from portbridge import netcheck
from portbridge import relay as relay_mod
from portbridge.errors import PortBridgeError


@dataclass
class Probe:
    frpc_installed: bool = False
    frpc_version: str | None = None
    relay_configured: bool = False
    relay_reachable: bool | None = None
    status_error: str | None = None


def probe_relay(cfg: dict[str, Any], check_reachable: bool = False) -> Probe:
    p = Probe()
    p.frpc_installed = relay_mod.binary_path() is not None
    if p.frpc_installed:
        try:
            p.frpc_version = relay_mod.get_version()
        except PortBridgeError as exc:
            p.status_error = exc.message

    p.relay_configured = config_mod.relay_configured(cfg) and bool(config_mod.load_relay_token())

    if check_reachable and p.relay_configured:
        relay = cfg["relay"]
        timeout = cfg["behavior"]["connect_timeout_seconds"]
        try:
            ok, _msg = netcheck.tcp_connect_test(relay["server_addr"], relay["server_port"], timeout=timeout)
            p.relay_reachable = ok
        except PortBridgeError as exc:
            p.relay_reachable = False
            p.status_error = p.status_error or exc.message

    return p


def probe_local(bind_address: str, port: int | None) -> bool | None:
    if not port:
        return None
    return netcheck.is_port_listening(bind_address, port)


def relay_prereqs(probe: Probe) -> list[str]:
    missing: list[str] = []
    if not probe.frpc_installed:
        return ["frpc is not installed"]
    if not probe.relay_configured:
        missing.append("relay not configured (run: portbridge configure / portbridge setup)")
    if probe.relay_reachable is False:
        missing.append("relay server is not reachable")
    return missing


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    if attempt < 1:
        attempt = 1
    return min(cap, base * (2 ** (attempt - 1)))


def build_status_snapshot(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    net = cfg["network"]
    bind = state.get("bind_address") or net["bind_address"]
    local_port = state.get("local_port") or (net["local_port"] or None)

    probe = probe_relay(cfg, check_reachable=True)
    local_listening = probe_local(bind, local_port)

    return {
        "probe": probe,
        "bind": bind,
        "local_port": local_port,
        "local_listening": local_listening,
        "state": state,
    }
