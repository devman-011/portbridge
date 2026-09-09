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


class TailscaleNotInstalledError(PortBridgeError):
    def __init__(self):
        super().__init__(
            "The 'tailscale' CLI is not installed (or not on PATH).",
            "Install it with: curl -fsSL https://tailscale.com/install.sh | sh",
        )


class TailscaleDaemonUnreachableError(PortBridgeError):
    def __init__(self, detail: str = ""):
        msg = "Could not reach the tailscaled daemon."
        if detail:
            msg += f" ({detail})"
        super().__init__(
            msg,
            "Check it's running with: sudo systemctl status tailscaled "
            "-- or start it with: sudo systemctl start tailscaled",
        )


class NotAuthenticatedError(PortBridgeError):
    def __init__(self):
        super().__init__(
            "This machine is not logged in to a Tailscale account.",
            "Run: sudo tailscale up  (or 'portbridge start' will offer to "
            "do this for you interactively)",
        )


class NetworkUnavailableError(PortBridgeError):
    def __init__(self, detail: str = ""):
        msg = "This machine appears to be offline."
        if detail:
            msg += f" ({detail})"
        super().__init__(msg, "Check your network connection: ip addr; ping -c1 1.1.1.1")


class DNSFailureError(PortBridgeError):
    def __init__(self, hostname: str):
        super().__init__(
            f"DNS resolution failed for '{hostname}'.",
            f"Try: getent hosts {hostname}  -- or: dig {hostname}",
        )


class ExternalPortInUseError(PortBridgeError):
    def __init__(self, port, other_local_port):
        super().__init__(
            f"External port {port} is already funneled to local port "
            f"{other_local_port} by an existing PortBridge/Tailscale config.",
            "Stop it first with 'portbridge stop', or choose a different "
            "external port.",
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
        msg = "The Tailscale CLI did not respond in time."
        if detail:
            msg += f" ({detail})"
        super().__init__(msg, "Try again, or check: sudo systemctl status tailscaled")


class MalformedConfigError(PortBridgeError):
    def __init__(self, path, detail: str):
        super().__init__(
            f"Config file at {path} is malformed: {detail}",
            f"Fix the syntax, or delete the file to regenerate defaults: rm {path}",
        )


class ProviderAPIChangedError(PortBridgeError):
    def __init__(self, detail: str = ""):
        msg = "Could not parse output from the 'tailscale' CLI."
        if detail:
            msg += f" ({detail})"
        super().__init__(
            msg,
            "The installed Tailscale version may be newer/older than this "
            "tool expects. Check: tailscale version",
        )
