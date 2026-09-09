"""Plain TCP connectivity checks -- no assumptions about the payload
protocol. `tcp_connect_test` is a real, user-requested client connection
(see its docstring for why that's sometimes not harmless). Passive checks
like `is_port_listening` deliberately do NOT connect -- see its docstring.
"""

from __future__ import annotations

import socket
import struct
from pathlib import Path

from portbridge.errors import DNSFailureError

_TCP_LISTEN_STATE = "0A"
_WILDCARD_ADDRESSES = {"0.0.0.0", "::", "::ffff:0.0.0.0"}


def _decode_local_address(hex_addr: str, hex_port: str, is_ipv6: bool) -> tuple[str, int]:
    port = int(hex_port, 16)
    raw = bytes.fromhex(hex_addr)
    if is_ipv6:
        words = struct.unpack("<4I", raw)
        packed = struct.pack(">4I", *words)
        ip = socket.inet_ntop(socket.AF_INET6, packed)
    else:
        word = struct.unpack("<I", raw)[0]
        packed = struct.pack(">I", word)
        ip = socket.inet_ntop(socket.AF_INET, packed)
    return ip, port


def _listening_sockets(path: str, is_ipv6: bool) -> list[tuple[str, int]]:
    """Parses /proc/net/tcp[6] for sockets in LISTEN state. Kernel-exposed
    accounting, not a network call -- reading it can't affect anyone."""
    found: list[tuple[str, int]] = []
    try:
        with Path(path).open("r", encoding="ascii", errors="replace") as fh:
            next(fh, None)  # header line
            for line in fh:
                fields = line.split()
                if len(fields) < 4 or fields[3].upper() != _TCP_LISTEN_STATE:
                    continue
                try:
                    hex_addr, hex_port = fields[1].split(":")
                    found.append(_decode_local_address(hex_addr, hex_port, is_ipv6))
                except (ValueError, struct.error):
                    continue
    except OSError:
        pass
    return found


def is_port_listening(bind_address: str, port: int) -> bool:
    """True if a socket is in LISTEN state on this port and address.

    This intentionally reads the kernel's own socket table
    (/proc/net/tcp, /proc/net/tcp6) instead of opening a real TCP
    connection to check. A real probe connection would be *accepted* by
    the target service -- and for a single-shot listener like plain
    `nc -l` (without `-k`/`-keep-open`), being accepted-then-closed even
    once is enough to make it exit, since nc only ever serves one client
    before quitting. This function is called on every `portbridge status`
    and every background health-check tick, so it must never touch the
    local service at all -- only inspect whether *something* is bound and
    listening.
    """
    for path, is_ipv6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        for ip, listen_port in _listening_sockets(path, is_ipv6):
            if listen_port == port and (ip == bind_address or ip in _WILDCARD_ADDRESSES):
                return True
    return False


def tcp_connect_test(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Attempt a real bare TCP connect; return (success, human message).

    Unlike is_port_listening, this genuinely connects -- it's what backs
    `portbridge test`, an explicit, on-demand, one-shot check the user
    asked for. Note this means running it against a single-shot listener
    (plain `nc -l` without `-k`) will consume that listener's one
    connection and end it, same as any other real client connecting.
    """
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DNSFailureError(host) from exc

    start = None
    try:
        import time

        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = (time.monotonic() - start) * 1000
            return True, f"Connected to {host}:{port} in {elapsed_ms:.0f} ms."
    except socket.timeout:
        return False, f"Timed out connecting to {host}:{port} after {timeout:.0f}s."
    except ConnectionRefusedError:
        return False, f"Connection to {host}:{port} was refused (nothing listening)."
    except OSError as exc:
        return False, f"Could not connect to {host}:{port}: {exc}"


def resolve_host(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except socket.gaierror:
        return False
