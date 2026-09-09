"""Plain TCP connectivity checks -- no assumptions about the payload
protocol. PortBridge only ever opens a short-lived client connection to
test reachability; it never sends or interprets application data.
"""

from __future__ import annotations

import socket

from portbridge.errors import DNSFailureError


def is_port_listening(bind_address: str, port: int, timeout: float = 1.0) -> bool:
    """True if something accepts a TCP connection at bind_address:port."""
    try:
        with socket.create_connection((bind_address, port), timeout=timeout):
            return True
    except OSError:
        return False


def tcp_connect_test(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """Attempt a bare TCP connect; return (success, human message)."""
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
