"""Thin wrapper around the `tailscale` CLI.

PortBridge never talks to the Tailscale network directly -- it only shells
out to the official `tailscale` binary and parses its output. All actual
tunneling, encryption, NAT traversal, and TLS termination is handled by
tailscaled itself.

Facts baked in here (external port list, --tcp/--tls-terminated-tcp flags,
--bg flag, funnel status/reset subcommands) were verified against Tailscale's
live documentation in September 2026:
  https://tailscale.com/kb/1223/funnel
  https://tailscale.com/docs/reference/tailscale-cli/funnel
  https://tailscale.com/docs/reference/tailscale-cli/up
Tailscale's own docs warn that `--json` output "format is subject to
change" -- every JSON parse here fails closed into ProviderAPIChangedError
rather than raising a raw KeyError/traceback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from portbridge.errors import (
    MissingExecutableError,
    PermissionDeniedError,
    ProviderAPIChangedError,
    ServiceUnavailableError,
    TailscaleDaemonUnreachableError,
    TailscaleNotInstalledError,
)

MIN_VERSION = "1.38.3"  # Funnel's documented minimum required version.

_PERMISSION_HINTS = (
    "permission denied",
    "must be root",
    "access denied",
    "operation not permitted",
    "access is not allowed",
)

_DAEMON_UNREACHABLE_HINTS = (
    "failed to connect to local tailscaled",
    "no such file or directory",
    "connection refused",
    "is tailscaled running",
)


def binary_path() -> str | None:
    return shutil.which("tailscale")


def require_binary() -> str:
    path = binary_path()
    if path is None:
        raise TailscaleNotInstalledError()
    return path


def _looks_like_permission_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(hint in lowered for hint in _PERMISSION_HINTS)


def _looks_like_daemon_unreachable(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(hint in lowered for hint in _DAEMON_UNREACHABLE_HINTS)


def run_cli(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    """Run a read-only tailscale command. Never escalates to sudo."""
    binary = require_binary()
    try:
        return subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise MissingExecutableError("tailscale") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(
            f"'tailscale {' '.join(args)}' did not respond within {timeout:.0f}s"
        ) from exc


def run_admin(
    args: list[str], timeout: float = 30, allow_sudo: bool = True
) -> subprocess.CompletedProcess:
    """Run a daemon-modifying tailscale command (up, login, funnel ...).

    If it fails with what looks like a permission error, and allow_sudo is
    true and we're not already root, retries once with sudo. The background
    monitor always passes allow_sudo=False so a non-interactive reconnect
    attempt never blocks forever on a password prompt it cannot answer --
    see README's "First-time setup" for configuring a passwordless
    Tailscale operator so unattended reconnects work at all.
    """
    binary = require_binary()
    argv = [binary, *args]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MissingExecutableError("tailscale") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(
            f"'tailscale {' '.join(args)}' did not respond within {timeout:.0f}s"
        ) from exc

    if (
        result.returncode != 0
        and allow_sudo
        and os.geteuid() != 0
        and _looks_like_permission_error(result.stderr)
    ):
        try:
            result = subprocess.run(
                ["sudo", *argv], capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise MissingExecutableError("sudo") from exc
        except subprocess.TimeoutExpired as exc:
            raise ServiceUnavailableError(
                "sudo prompted for a password but none was entered in time"
            ) from exc
    return result


def get_version() -> str:
    result = run_cli(["version"], timeout=10)
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else "unknown"


def _parse_version_tuple(text: str) -> tuple[int, ...]:
    core = text.split("-")[0].strip()
    parts = []
    for chunk in core.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def version_at_least(min_version: str = MIN_VERSION) -> bool:
    try:
        current = _parse_version_tuple(get_version())
        minimum = _parse_version_tuple(min_version)
    except Exception:
        return True  # Don't block on a version string we failed to parse.
    return current >= minimum


def get_status(timeout: float = 10) -> dict[str, Any]:
    result = run_cli(["status", "--json"], timeout=timeout)
    if result.returncode != 0 and not result.stdout.strip():
        stderr = result.stderr.strip()
        if _looks_like_daemon_unreachable(stderr) or not stderr:
            raise TailscaleDaemonUnreachableError(stderr)
        raise TailscaleDaemonUnreachableError(stderr)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderAPIChangedError(
            f"'tailscale status --json' did not return valid JSON: {exc}"
        ) from exc


def is_authenticated(status: dict[str, Any]) -> bool:
    return status.get("BackendState") == "Running"


def backend_state(status: dict[str, Any]) -> str:
    return status.get("BackendState", "Unknown")


def self_online(status: dict[str, Any]) -> bool:
    self_peer = status.get("Self") or {}
    return bool(self_peer.get("Online"))


def self_dns_name(status: dict[str, Any]) -> str | None:
    self_peer = status.get("Self") or {}
    name = self_peer.get("DNSName")
    return name.rstrip(".") if name else None


def health_problems(status: dict[str, Any]) -> list[str]:
    return list(status.get("Health") or [])


def login_interactive(timeout: float = 180) -> subprocess.CompletedProcess:
    """Runs `tailscale up` with inherited stdio so the user can see and
    follow the browser authentication URL Tailscale prints to the
    terminal."""
    binary = require_binary()
    argv = [binary, "up"] if os.geteuid() == 0 else ["sudo", binary, "up"]
    try:
        return subprocess.run(argv, timeout=timeout)
    except FileNotFoundError as exc:
        raise MissingExecutableError("sudo") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError(
            "'tailscale up' did not complete within the timeout -- did you "
            "finish the browser login?"
        ) from exc


def set_operator(username: str, timeout: float = 15) -> subprocess.CompletedProcess:
    binary = require_binary()
    argv = ["sudo", binary, "set", f"--operator={username}"]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _funnel_target(bind_address: str, local_port: int) -> str:
    return f"tcp://{bind_address}:{local_port}"


def _funnel_flag(mode: str, external_port: int) -> str:
    if mode == "tls-terminated-tcp":
        return f"--tls-terminated-tcp={external_port}"
    return f"--tcp={external_port}"


def funnel_apply(
    bind_address: str,
    local_port: int,
    external_port: int,
    mode: str = "tcp",
    allow_sudo: bool = True,
    timeout: float = 30,
) -> subprocess.CompletedProcess:
    args = [
        "funnel",
        "--bg",
        _funnel_flag(mode, external_port),
        _funnel_target(bind_address, local_port),
    ]
    return run_admin(args, timeout=timeout, allow_sudo=allow_sudo)


def funnel_remove(
    bind_address: str,
    local_port: int,
    external_port: int,
    mode: str = "tcp",
    allow_sudo: bool = True,
    timeout: float = 30,
) -> subprocess.CompletedProcess:
    args = [
        "funnel",
        _funnel_flag(mode, external_port),
        _funnel_target(bind_address, local_port),
        "off",
    ]
    return run_admin(args, timeout=timeout, allow_sudo=allow_sudo)


def funnel_reset(allow_sudo: bool = True, timeout: float = 30) -> subprocess.CompletedProcess:
    return run_admin(["funnel", "reset"], timeout=timeout, allow_sudo=allow_sudo)


def funnel_status_json(timeout: float = 10) -> dict[str, Any] | None:
    """Best-effort structured funnel status. Tailscale does not publish a
    stable documented schema for `funnel status --json`, so callers should
    treat a None return as "fall back to funnel_status_text()" rather than
    as an error.
    """
    result = run_cli(["funnel", "status", "--json"], timeout=timeout)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def funnel_status_text(timeout: float = 10) -> str:
    result = run_cli(["funnel", "status"], timeout=timeout)
    return (result.stdout or "") + (result.stderr or "")


def is_port_funneled(external_port: int) -> bool:
    """True if the given external port currently has an active Funnel
    mapping, checked via the structured status first and falling back to a
    plain substring match on the text output if the JSON shape doesn't
    match what we expect (see funnel_status_json's docstring)."""
    data = funnel_status_json()
    if data is not None:
        try:
            text = json.dumps(data)
        except (TypeError, ValueError):
            text = ""
        if text:
            return f":{external_port}" in text or f'"{external_port}"' in text
    text = funnel_status_text()
    return f":{external_port}" in text
