"""Thin wrapper around the `frpc` (Fast Reverse Proxy client) binary.

PortBridge does not implement tunneling itself -- frpc does. PortBridge's
job is generating frpc's config from config.toml + the relay token, and
running/supervising frpc as a subprocess (see monitor.py).

frpc connects OUT to a relay server (frps) you deploy yourself somewhere
with a public TCP port -- see README for how the reference deployment
(frps + a small socat bridge, on Railway's TCP Proxy) was built. Nothing
here is Railway-specific; frpc only needs a reachable host:port speaking
the frp protocol.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from portbridge.errors import (
    FrpcNotInstalledError,
    MissingExecutableError,
    PortBridgeError,
    ServiceUnavailableError,
)

FRP_VERSION = "0.71.0"

_ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
}


def binary_path() -> str | None:
    return shutil.which("frpc")


def require_binary() -> str:
    path = binary_path()
    if path is None:
        raise FrpcNotInstalledError()
    return path


def get_version() -> str:
    binary = require_binary()
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        raise MissingExecutableError("frpc") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceUnavailableError("'frpc --version' timed out") from exc
    return (result.stdout or result.stderr or "unknown").strip()


def frp_download_arch() -> str | None:
    return _ARCH_MAP.get(platform.machine())


def install_frpc(install_dir: Path, version: str = FRP_VERSION) -> Path:
    """Downloads and installs the frpc binary for this machine's
    architecture into install_dir. Returns the installed binary's path.
    Pure stdlib (urllib + tarfile) so it works even without curl/wget."""
    arch = frp_download_arch()
    if arch is None:
        raise PortBridgeError(
            f"No known frpc release for this CPU architecture ({platform.machine()}).",
            "Download the right build manually from "
            "https://github.com/fatedier/frp/releases and place the "
            f"'frpc' binary in {install_dir}",
        )

    url = (
        f"https://github.com/fatedier/frp/releases/download/v{version}/"
        f"frp_{version}_linux_{arch}.tar.gz"
    )
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / "frpc"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "frp.tar.gz"
        try:
            urllib.request.urlretrieve(url, archive)  # noqa: S310 -- fixed https github.com URL
        except OSError as exc:
            raise PortBridgeError(
                f"Failed to download frpc from {url}: {exc}",
                "Check your network connection, or download manually from "
                "https://github.com/fatedier/frp/releases",
            ) from exc

        with tarfile.open(archive) as tf:
            tf.extractall(tmp_path)  # noqa: S202 -- trusted, pinned GitHub release URL

        extracted_bin = tmp_path / f"frp_{version}_linux_{arch}" / "frpc"
        shutil.copy2(extracted_bin, target)

    target.chmod(0o755)
    return target


def write_frpc_config(
    path: Path,
    server_addr: str,
    server_port: int,
    remote_port: int,
    token: str,
    bind_address: str,
    local_port: int,
    proxy_name: str = "portbridge-tunnel",
) -> None:
    content = (
        f'serverAddr = "{server_addr}"\n'
        f"serverPort = {server_port}\n"
        f"\n"
        f'auth.method = "token"\n'
        f'auth.token = "{token}"\n'
        f"\n"
        f"[[proxies]]\n"
        f'name = "{proxy_name}"\n'
        f'type = "tcp"\n'
        f'localIP = "{bind_address}"\n'
        f"localPort = {local_port}\n"
        f"remotePort = {remote_port}\n"
    )
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)  # contains the auth token
    except OSError:
        pass


def spawn_frpc(config_path: Path, log_file) -> subprocess.Popen:
    binary = require_binary()
    return subprocess.Popen(
        [binary, "-c", str(config_path)],
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
