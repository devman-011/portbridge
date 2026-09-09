"""Optional disposable TCP listener for testing -- deliberately isolated
from forwarding management. This is a convenience stand-in for `nc -l -p
PORT`; it never reads state.json, never touches the relay, and PortBridge's
forwarding logic never imports or calls into it. It echoes back whatever it
receives, one connection at a time, and prints received data to stdout so
you can watch bytes arrive when testing forwarding end-to-end.
"""

from __future__ import annotations

import socket
import sys

from portbridge.errors import PermissionDeniedError, PortInUseError


def run_echo_listener(bind_address: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((bind_address, port))
        except PermissionError as exc:
            raise PermissionDeniedError(
                f"binding to {bind_address}:{port} ({exc})"
            ) from exc
        except OSError as exc:
            raise PortInUseError(bind_address, port) from exc
        server.listen(1)
        print(f"Listening on {bind_address}:{port} (Ctrl+C to stop). "
              f"This is a test-only echo server, separate from forwarding.")
        try:
            while True:
                conn, addr = server.accept()
                print(f"Connection from {addr[0]}:{addr[1]}")
                try:
                    with conn:
                        while True:
                            data = conn.recv(4096)
                            if not data:
                                print(f"Connection from {addr[0]}:{addr[1]} closed.")
                                break
                            sys.stdout.write(data.decode("utf-8", "replace"))
                            sys.stdout.flush()
                            conn.sendall(data)
                except OSError as exc:
                    # A single client resetting/aborting the connection
                    # (ConnectionResetError, BrokenPipeError, etc.) should
                    # not take down the whole listener -- go back to
                    # accepting the next connection instead.
                    print(f"Connection from {addr[0]}:{addr[1]} dropped: {exc}")
        except KeyboardInterrupt:
            print("\nStopped.")
