"""User-facing error types.

Every error PortBridge can anticipate is a PortBridgeError with a plain
English `message` and an optional `hint` (a suggested diagnostic command or
next step). cli.py catches PortBridgeError at the top level and prints
message+hint instead of a Python traceback. Unexpected exceptions are caught
separately, logged in full, and shown to the user as a short generic notice.
"""

from __future__ import annotations


class PortBridgeError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class InvalidPortError(PortBridgeError):
    def __init__(self, port):
        super().__init__(
            f"'{port}' is not a valid TCP port.",
            "Ports must be integers between 1 and 65535.",
        )


class PortInUseError(PortBridgeError):
    def __init__(self, bind, port):
        super().__init__(
            f"Something is already using {bind}:{port} for listening.",
            f"Check what's using it with: ss -ltnp 'sport = :{port}'",
        )


class FrpcNotInstalledError(PortBridgeError):
    def __init__(self):
        super().__init__(
            "The 'frpc' relay client is not installed (or not on PATH).",
            "Run 'portbridge setup' to install it, or download it manually "
            "from https://github.com/fatedier/frp/releases",
        )


class RelayNotConfiguredError(PortBridgeError):
    def __init__(self, missing: list[str] | None = None):
        detail = f" Missing: {', '.join(missing)}." if missing else ""
        super().__init__(
            f"No relay is configured yet.{detail}",
            "Run 'portbridge configure' to set your relay's address/port "
            "and 'portbridge configure --relay-token <token>' for its auth "
            "token. See README for how to deploy your own relay.",
        )


class RelayConnectionError(PortBridgeError):
    def __init__(self, server_addr: str, server_port: int, detail: str = ""):
        msg = f"Could not reach the relay at {server_addr}:{server_port}."
        if detail:
            msg += f" ({detail})"
        super().__init__(
            msg,
            "Check the relay deployment is running and the address/port in "
            "'portbridge configure' are correct.",
        )


class DNSFailureError(PortBridgeError):
    def __init__(self, hostname: str):
        super().__init__(
            f"DNS resolution failed for '{hostname}'.",
            f"Try: getent hosts {hostname}  -- or: dig {hostname}",
        )


class PermissionDeniedError(PortBridgeError):
    def __init__(self, detail: str = ""):
        msg = "Permission denied."
        if detail:
            msg += f" ({detail})"
        super().__init__(msg, "You may need to re-run with appropriate permissions.")


class MissingExecutableError(PortBridgeError):
    def __init__(self, name: str):
        super().__init__(
            f"Required program '{name}' was not found on PATH.",
            f"Install it and make sure '{name}' is on your PATH.",
        )


class ServiceUnavailableError(PortBridgeError):
    def __init__(self, detail: str = ""):
        msg = "The relay client did not respond in time."
        if detail:
            msg += f" ({detail})"
        super().__init__(msg, "Try again, or check: portbridge logs")


class MalformedConfigError(PortBridgeError):
    def __init__(self, path, detail: str):
        super().__init__(
            f"Config file at {path} is malformed: {detail}",
            f"Fix the syntax, or delete the file to regenerate defaults: rm {path}",
        )
