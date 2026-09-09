"""Argument parsing, the interactive menu, and every command implementation.

Both `portbridge <subcommand>` and the interactive menu call the same do_*()
functions below, so behavior never diverges between the two entry points.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from portbridge import __version__
from portbridge import config as config_mod
from portbridge import core
from portbridge import logging_setup
from portbridge import monitor
from portbridge import netcheck
from portbridge import paths
from portbridge import process
from portbridge import relay as relay_mod
from portbridge import state as state_mod
from portbridge import testserver
from portbridge import ui
from portbridge import validation
from portbridge.errors import (
    FrpcNotInstalledError,
    PortBridgeError,
    RelayConnectionError,
    RelayNotConfiguredError,
)


# ---------------------------------------------------------------------------
# setup / dependency checks
# ---------------------------------------------------------------------------

def _confirm_system_change(prompt: str, assume_yes: bool) -> bool:
    """Gate for prompts that trigger a system-modifying action (installing
    software). Unlike ui.ask_yes_no (which falls back to its `default`
    when stdin isn't a TTY -- fine for low-stakes prompts like "continue
    anyway?"), this NEVER proceeds on its own just because stdin is
    non-interactive: it requires either the explicit --yes flag or a real
    answer at a real terminal. That matters because `ensure_relay_ready`
    can be reached from a non-interactive `portbridge start` (e.g. from a
    script/cron), where silently downloading and installing a binary
    would be a real overreach.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return ui.ask_yes_no(prompt, default=True)


def _missing_relay_fields(cfg: dict) -> list[str]:
    relay = cfg["relay"]
    missing = []
    if not relay["server_addr"]:
        missing.append("server_addr")
    if not relay["server_port"]:
        missing.append("server_port")
    if not relay["remote_port"]:
        missing.append("remote_port")
    if not relay["public_addr"]:
        missing.append("public_addr")
    if not relay["public_port"]:
        missing.append("public_port")
    if not config_mod.load_relay_token():
        missing.append("relay_token")
    return missing


def ensure_relay_ready(cfg: dict, assume_yes: bool = False) -> core.Probe:
    """Checks frpc is installed and the relay is configured/reachable --
    offering to install frpc interactively (never silently) before
    raising if it still can't proceed. Shared by `portbridge start` and
    `portbridge setup`. Missing relay *configuration* (as opposed to the
    frpc binary) is not filled in here -- that's what `portbridge setup`
    walks you through; this just checks and points you there.
    """
    probe = core.probe_relay(cfg, check_reachable=False)

    if not probe.frpc_installed:
        print("\nfrpc isn't installed yet. PortBridge needs it to run the tunnel client.")
        if _confirm_system_change(
            "Install it now (downloads the frpc release for this machine's CPU architecture)?",
            assume_yes,
        ):
            install_dir = Path.home() / ".local" / "bin"
            print(f"\nDownloading frpc into {install_dir}...")
            relay_mod.install_frpc(install_dir)
            print("frpc installed.")
            probe = core.probe_relay(cfg, check_reachable=False)
        if not probe.frpc_installed:
            raise FrpcNotInstalledError()

    missing = _missing_relay_fields(cfg)
    if missing:
        raise RelayNotConfiguredError(missing)

    relay = cfg["relay"]
    timeout = cfg["behavior"]["connect_timeout_seconds"]
    ok, msg = netcheck.tcp_connect_test(relay["server_addr"], relay["server_port"], timeout=timeout)
    probe.relay_reachable = ok
    if not ok:
        raise RelayConnectionError(relay["server_addr"], relay["server_port"], msg)

    return probe


def do_setup(assume_yes: bool = False) -> int:
    print(ui.box(["PORTBRIDGE SETUP"]))
    cfg = config_mod.load_config()

    print("\nChecking frpc...")
    probe = core.probe_relay(cfg, check_reachable=False)
    if not probe.frpc_installed:
        print("frpc isn't installed yet. PortBridge needs it to run the tunnel client.")
        if _confirm_system_change(
            "Install it now (downloads the frpc release for this machine's CPU architecture)?",
            assume_yes,
        ):
            install_dir = Path.home() / ".local" / "bin"
            print(f"\nDownloading frpc into {install_dir}...")
            relay_mod.install_frpc(install_dir)
            print("frpc installed.")
            probe = core.probe_relay(cfg, check_reachable=False)
        if not probe.frpc_installed:
            raise FrpcNotInstalledError()
    print(f"✓ frpc installed ({probe.frpc_version}).")

    if not config_mod.relay_configured(cfg):
        if sys.stdin.isatty():
            print(
                "\nNo relay is configured yet. PortBridge needs a relay's address and\n"
                "ports to tunnel through -- see README for how to deploy one (a small\n"
                "frps server + bridge, e.g. on Railway's TCP Proxy, fronting your own\n"
                "machine). Enter its details:"
            )
            relay = cfg["relay"]
            relay["server_addr"] = ui.ask("Relay control address (frps host)", relay["server_addr"] or None)
            relay["server_port"] = ui.ask_port("Relay control port (frps bindPort)", relay["server_port"] or None)
            relay["remote_port"] = ui.ask_port(
                "Relay internal data port (frps allowPorts)", relay["remote_port"] or None
            )
            relay["public_addr"] = ui.ask(
                "Public address remote clients connect to", relay["public_addr"] or None
            )
            relay["public_port"] = ui.ask_port("Public port remote clients connect to", relay["public_port"] or None)
            config_mod.save_config(cfg)
            print("Relay settings saved.")
        if not config_mod.relay_configured(cfg):
            raise RelayNotConfiguredError(_missing_relay_fields(cfg))

    if not config_mod.load_relay_token():
        print("\nNo relay auth token is configured yet.")
        if sys.stdin.isatty():
            token = ui.ask("Relay auth token (from your frps deployment)")
            if token:
                config_mod.save_relay_token(token)
                print(f"Token saved to {paths.relay_token_file()} (kept out of config.toml).")
        if not config_mod.load_relay_token():
            raise RelayNotConfiguredError(["relay_token"])

    relay = cfg["relay"]
    timeout = cfg["behavior"]["connect_timeout_seconds"]
    print(f"\nChecking the relay is reachable at {relay['server_addr']}:{relay['server_port']}...")
    ok, msg = netcheck.tcp_connect_test(relay["server_addr"], relay["server_port"], timeout=timeout)
    if ok:
        print(f"✓ {msg}")
    else:
        raise RelayConnectionError(relay["server_addr"], relay["server_port"], msg)

    print(f"\nSetup check complete. Public endpoint: {relay['public_addr']}:{relay['public_port']}")
    print("You're ready to run: portbridge start --port <PORT>")
    return 0


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------

def do_start(
    local_port: int | None = None,
    bind: str | None = None,
    foreground: bool = False,
    assume_yes: bool = False,
) -> int:
    cfg = config_mod.load_config()
    state = state_mod.load_state()
    state, was_stale = state_mod.detect_and_clean_stale(state)
    if was_stale:
        print("Note: cleaned up leftover state from a previous crash or reboot.\n")

    if state["status"] in (state_mod.ACTIVE, state_mod.RECONNECTING, state_mod.STARTING):
        print("Forwarding is already active.\n")
        print(f"Local:  {state['bind_address']}:{state['local_port']}")
        public = (
            f"{state['public_addr']}:{state['public_port']}"
            if state.get("public_addr")
            else "(unknown)"
        )
        print(f"Public: {public}\n")
        if not (assume_yes or ui.ask_yes_no("Stop and reconfigure?", default=False)):
            print("Leaving existing forwarding active.")
            return 1
        do_stop(quiet=True)
        print("Stopped existing forwarding.\n")

    bind = bind or cfg["network"]["bind_address"]
    if local_port is None:
        default_port = cfg["network"]["local_port"] or None
        local_port = ui.ask_port("Enter local listening port", default=default_port)
    else:
        local_port = validation.parse_port(local_port)

    if not netcheck.is_port_listening(bind, local_port):
        print(f"\nWarning: nothing is currently listening on {bind}:{local_port}.")
        if not (assume_yes or ui.ask_yes_no(
            "Continue anyway and forward it once your service starts?", default=True
        )):
            print("Aborted.")
            return 1

    ensure_relay_ready(cfg, assume_yes=assume_yes)
    relay = cfg["relay"]

    new_state = state_mod.default_state()
    new_state.update({
        "status": state_mod.STARTING,
        "bind_address": bind,
        "local_port": local_port,
        "public_addr": relay["public_addr"],
        "public_port": relay["public_port"],
        "started_at": time.time(),
    })
    state_mod.save_state(new_state)

    cfg["network"]["bind_address"] = bind
    cfg["network"]["local_port"] = local_port
    config_mod.save_config(cfg)

    if foreground:
        new_state["pid"] = None  # monitor.run_loop sets this once frpc is up
        logger = logging_setup.get_logger(
            cfg["logging"]["level"], cfg["logging"]["max_bytes"], cfg["logging"]["backup_count"]
        )
        print(
            f"\nForwarding active (foreground). Local {bind}:{local_port} -> "
            f"public {relay['public_addr']}:{relay['public_port']}\nPress Ctrl+C to stop.\n"
        )
        monitor.start_and_run(cfg, logger)
        return 0

    try:
        process.spawn_monitor()
    except RuntimeError as exc:
        raise PortBridgeError(
            f"Failed to start the background monitor: {exc}",
            f"Check the log for details: portbridge logs (file: {paths.log_file()})",
        ) from exc
    # The detached child (monitor.entrypoint) takes ownership of state.json
    # from here: it records its own PID and flips status to ACTIVE itself.
    # We deliberately do not write state.json again here -- doing so with
    # this stale in-memory copy would race the child's own write.
    time.sleep(1.5)  # give frpc a moment to fail fast (bad token, unreachable relay)
    final_state = state_mod.load_state()
    if final_state.get("status") == state_mod.FAILED:
        raise PortBridgeError(
            f"frpc failed to start: {final_state.get('last_error') or 'unknown error'}",
            "Check: portbridge logs",
        )
    print("\nForwarding started.\n")
    print(f"  Local:  {bind}:{local_port}")
    print(f"  Public: {relay['public_addr']}:{relay['public_port']}\n")
    return 0


def do_stop(quiet: bool = False) -> int:
    state = state_mod.load_state()
    state, _ = state_mod.detect_and_clean_stale(state)
    if state["status"] == state_mod.STOPPED:
        if not quiet:
            print("Forwarding is not currently active.")
        return 0

    pid = state.get("pid")
    if pid and state_mod.is_pid_alive(pid) and state_mod.is_portbridge_process(pid):
        ok = process.terminate_monitor(pid)
        if not ok and not quiet:
            print("Warning: monitor process did not exit cleanly; forcing cleanup anyway.")

    # Killing the monitor kills frpc (its child) as part of its own SIGTERM
    # cleanup, which is what actually tears down the tunnel -- unlike the
    # old Tailscale design there's no separate "remove mapping" RPC call
    # needed. clear_state() here is just a safety net in case the monitor
    # didn't get to its own cleanup (e.g. the SIGKILL fallback above).
    state_mod.clear_state()

    if not quiet:
        print("Forwarding stopped.")
    return 0


def do_restart(assume_yes: bool = False) -> int:
    state = state_mod.load_state()
    state, _ = state_mod.detect_and_clean_stale(state)
    was_active = state["status"] != state_mod.STOPPED

    local_port = state.get("local_port")

    if was_active:
        do_stop(quiet=True)
        print("Stopped existing forwarding.")

    if not local_port:
        cfg = config_mod.load_config()
        local_port = cfg["network"]["local_port"] or None

    # bind is deliberately NOT carried over from the old state: do_start()
    # falls back to the CURRENT config.toml when not passed explicitly, so
    # a `portbridge configure --bind ...` change takes effect on restart
    # instead of being silently reverted to whatever was running before.
    return do_start(local_port=local_port, assume_yes=assume_yes)


# ---------------------------------------------------------------------------
# status / endpoint / test / logs
# ---------------------------------------------------------------------------

def do_status() -> int:
    cfg = config_mod.load_config()
    state = state_mod.load_state()
    state, was_stale = state_mod.detect_and_clean_stale(state)
    snap = core.build_status_snapshot(cfg, state)
    probe = snap["probe"]

    print(ui.status_header())
    print()
    print("Relay client:")
    print(f"  frpc installed: {ui.yesno(probe.frpc_installed)}")
    print(f"  Configured:     {ui.yesno(probe.relay_configured)}")
    print(f"  Reachable:      {ui.yesno(bool(probe.relay_reachable))}")
    print()
    print("Local service:")
    print(f"  Address:   {snap['bind']}")
    print(f"  Port:      {snap['local_port'] or '-'}")
    print(f"  Listening: {ui.yesno(bool(snap['local_listening']))}")
    print()
    print("Forwarding:")
    print(f"  Status:   {state['status']}")
    if state["status"] == state_mod.RECONNECTING:
        next_retry = state.get("next_retry_at")
        remaining = max(0, int(next_retry - time.time())) if next_retry else 0
        print(f"  Attempt:  {state.get('reconnect_attempts', 0)}")
        print(f"  Next retry: {remaining} seconds")
    print(f"  Local:    {snap['bind']}:{snap['local_port'] or '-'}")
    print()

    if state.get("public_addr") and state.get("public_port"):
        print("Public endpoint:")
        print(f"  {state['public_addr']}:{state['public_port']}")
        print()

    print("Uptime:")
    print(f"  {state_mod.format_uptime(state_mod.uptime_seconds(state))}")
    print()

    print("Health:")
    print(ui.check(
        probe.relay_configured and probe.relay_reachable is not False, "Relay reachable",
        probe.status_error or "relay not configured or unreachable",
    ))
    print(ui.check(
        snap["local_listening"], "Local port reachable",
        f"nothing is listening on {snap['bind']}:{snap['local_port']}"
        if snap["local_port"] else "no local port configured",
    ))
    print(ui.check(
        state["status"] == state_mod.ACTIVE, "Tunnel active",
        f"current status is {state['status']}",
    ))
    print()
    if was_stale:
        print("Note: previous forwarding state was stale (crash or reboot) and has been cleared.")
    return 0


def do_endpoint() -> int:
    state = state_mod.load_state()
    state, _ = state_mod.detect_and_clean_stale(state)
    if state["status"] not in (state_mod.ACTIVE, state_mod.RECONNECTING):
        print("Forwarding is not currently active; no public endpoint configured.")
        return 1
    print(f"{state['public_addr']}:{state['public_port']}")
    return 0


def do_test(check_local: bool = True, check_external: bool = True, port: int | None = None) -> int:
    cfg = config_mod.load_config()
    state = state_mod.load_state()
    timeout = cfg["behavior"]["connect_timeout_seconds"]
    bind = state.get("bind_address") or cfg["network"]["bind_address"]
    local_port = port or state.get("local_port") or (cfg["network"]["local_port"] or None)

    ok_all = True
    if check_local:
        if not local_port:
            print("No local port configured or known. Use --port, or run 'portbridge start' first.")
            ok_all = False
        else:
            ok, msg = netcheck.tcp_connect_test(bind, local_port, timeout=timeout)
            print(("✓ " if ok else "✗ ") + msg)
            ok_all = ok_all and ok

    if check_external:
        if state["status"] not in (state_mod.ACTIVE, state_mod.RECONNECTING):
            print("Forwarding is not active; skipping external endpoint test.")
        else:
            host = state.get("public_addr")
            ext_port = state.get("public_port")
            if not host or not ext_port:
                print("Public endpoint unknown; cannot test.")
                ok_all = False
            else:
                ok, msg = netcheck.tcp_connect_test(host, ext_port, timeout=timeout)
                print(("✓ " if ok else "✗ ") + msg)
                ok_all = ok_all and ok

    return 0 if ok_all else 1


def do_logs(follow: bool = False, n: int = 50) -> int:
    if follow:
        logging_setup.follow()
        return 0
    lines = logging_setup.tail_lines(n)
    if not lines:
        print("(no log entries yet)")
    for line in lines:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------

def do_configure(overrides: dict, interactive_wizard: bool, relay_token: str | None = None) -> int:
    cfg = config_mod.load_config()

    if relay_token is not None:
        config_mod.save_relay_token(relay_token)
        print(f"Relay token saved to {paths.relay_token_file()}.")

    if interactive_wizard:
        print(ui.box(["CONFIGURE PORTBRIDGE"]))
        print()
        cfg["network"]["bind_address"] = ui.ask("Local bind address", cfg["network"]["bind_address"])
        current_local = cfg["network"]["local_port"] or ""
        raw = ui.ask("Default local port (blank = ask each time)", str(current_local) if current_local else "")
        cfg["network"]["local_port"] = validation.parse_port(raw) if raw else 0

        relay = cfg["relay"]
        relay["server_addr"] = ui.ask("Relay control address (frps host)", relay["server_addr"] or "")
        raw = ui.ask("Relay control port", str(relay["server_port"]) if relay["server_port"] else "")
        if raw:
            relay["server_port"] = validation.parse_port(raw)
        raw = ui.ask("Relay internal data port", str(relay["remote_port"]) if relay["remote_port"] else "")
        if raw:
            relay["remote_port"] = validation.parse_port(raw)
        relay["public_addr"] = ui.ask("Public address (what remote clients connect to)", relay["public_addr"] or "")
        raw = ui.ask("Public port", str(relay["public_port"]) if relay["public_port"] else "")
        if raw:
            relay["public_port"] = validation.parse_port(raw)

        raw = ui.ask("Health check interval (seconds)", str(cfg["behavior"]["health_check_interval_seconds"]))
        cfg["behavior"]["health_check_interval_seconds"] = int(raw)
        raw = ui.ask("Connect timeout (seconds)", str(cfg["behavior"]["connect_timeout_seconds"]))
        cfg["behavior"]["connect_timeout_seconds"] = int(raw)
        cfg["behavior"]["reconnect"] = ui.ask_yes_no("Enable auto-reconnect?", cfg["behavior"]["reconnect"])
        raw = ui.ask("Max reconnect attempts (0 = unlimited)", str(cfg["behavior"]["reconnect_max_attempts"]))
        cfg["behavior"]["reconnect_max_attempts"] = int(raw)
        raw = ui.ask("Reconnect backoff base (seconds)", str(cfg["behavior"]["reconnect_backoff_base_seconds"]))
        cfg["behavior"]["reconnect_backoff_base_seconds"] = int(raw)
        raw = ui.ask("Reconnect backoff cap (seconds)", str(cfg["behavior"]["reconnect_backoff_max_seconds"]))
        cfg["behavior"]["reconnect_backoff_max_seconds"] = int(raw)
        raw = ui.ask("Log level (DEBUG/INFO/WARNING/ERROR)", cfg["logging"]["level"]).upper()
        cfg["logging"]["level"] = raw
    elif overrides:
        for (section, field), value in overrides.items():
            cfg[section][field] = value

    if interactive_wizard or overrides:
        config_mod.save_config(cfg)
        print(f"\nSaved to {paths.config_file()}")
    return 0


def _collect_configure_overrides(args: argparse.Namespace) -> dict:
    mapping = {
        "bind": ("network", "bind_address"),
        "local_port": ("network", "local_port"),
        "server_addr": ("relay", "server_addr"),
        "server_port": ("relay", "server_port"),
        "remote_port": ("relay", "remote_port"),
        "public_addr": ("relay", "public_addr"),
        "public_port": ("relay", "public_port"),
        "health_interval": ("behavior", "health_check_interval_seconds"),
        "connect_timeout": ("behavior", "connect_timeout_seconds"),
        "reconnect": ("behavior", "reconnect"),
        "reconnect_max_attempts": ("behavior", "reconnect_max_attempts"),
        "backoff_base": ("behavior", "reconnect_backoff_base_seconds"),
        "backoff_max": ("behavior", "reconnect_backoff_max_seconds"),
        "log_level": ("logging", "level"),
    }
    overrides = {}
    for attr, key in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            overrides[key] = value
    return overrides


# ---------------------------------------------------------------------------
# listen (disposable test listener -- separate from forwarding)
# ---------------------------------------------------------------------------

def do_listen(bind: str, port: int) -> int:
    testserver.run_echo_listener(bind, port)
    return 0


# ---------------------------------------------------------------------------
# interactive menu
# ---------------------------------------------------------------------------

_FORWARDING_LABEL = {
    state_mod.STOPPED: "OFF",
    state_mod.STARTING: "STARTING",
    state_mod.ACTIVE: "ON",
    state_mod.RECONNECTING: "RECONNECTING",
    state_mod.FAILED: "FAILED",
}


def interactive_menu() -> int:
    while True:
        cfg = config_mod.load_config()
        state = state_mod.load_state()
        state, _ = state_mod.detect_and_clean_stale(state)
        probe = core.probe_relay(cfg)

        print()
        print(ui.banner())
        print()
        print("Status:")
        if probe.frpc_installed:
            print(f"  Relay client: {'Configured' if probe.relay_configured else 'Not configured'}")
        else:
            print("  Relay client: Not installed")
        print(f"  Public endpoint: {'Available' if probe.relay_configured else 'Unavailable'}")
        print(f"  Forwarding: {_FORWARDING_LABEL.get(state['status'], state['status'])}")
        if not (probe.frpc_installed and probe.relay_configured):
            print("\n  Relay isn't fully set up yet -- choose 10) Setup check to fix it.")
        print()
        print("Choose an option:\n")
        print("  1) Start port forwarding")
        print("  2) Stop port forwarding")
        print("  3) Show status")
        print("  4) Show public endpoint")
        print("  5) Test local port")
        print("  6) Test external endpoint")
        print("  7) View logs")
        print("  8) Restart forwarding")
        print("  9) Configure")
        print(" 10) Setup check (install frpc / configure relay)")
        print("  0) Exit")
        print()
        try:
            choice = input("Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        print()

        try:
            if choice == "1":
                do_start()
            elif choice == "2":
                do_stop()
            elif choice == "3":
                do_status()
            elif choice == "4":
                do_endpoint()
            elif choice == "5":
                default_port = state.get("local_port") or (cfg["network"]["local_port"] or None)
                port = ui.ask_port("Port to test", default=default_port)
                do_test(check_local=True, check_external=False, port=port)
            elif choice == "6":
                do_test(check_local=False, check_external=True)
            elif choice == "7":
                do_logs()
                print("\n(Use 'portbridge logs --follow' in another terminal to tail live.)")
            elif choice == "8":
                do_restart()
            elif choice == "9":
                do_configure({}, interactive_wizard=True)
            elif choice == "10":
                do_setup()
            elif choice == "0":
                return 0
            else:
                print("Invalid choice.")
        except PortBridgeError as exc:
            ui.print_error(exc)
        except KeyboardInterrupt:
            print("\nCancelled.")

        if sys.stdin.isatty():
            try:
                input("\nPress Enter to continue...")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

_EPILOG = """\
Examples:
  portbridge                                 Launch the interactive menu
  portbridge setup                           Check/install frpc, configure the relay
  portbridge start --port 9001               Forward local port 9001
  portbridge stop                            Stop forwarding
  portbridge status                          Detailed status report
  portbridge endpoint                        Print host:port of the public endpoint
  portbridge test                            Test local + external connectivity
  portbridge test --local --port 9001        Test only a local port
  portbridge logs --follow                   Tail the log file live
  portbridge restart                         Stop then start with stored config
  portbridge configure                       Interactive configuration wizard
  portbridge configure --relay-token <token> Set the relay auth token
  portbridge listen --port 9001              Disposable test TCP echo server

Config file: ~/.config/portbridge/config.toml
State/log:   ~/.local/state/portbridge/
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portbridge",
        description="Manage TCP port forwarding from this Linux machine to the "
                     "public internet through your own relay (frpc/frps), without "
                     "requiring the remote machine to install any VPN client.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"portbridge {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start TCP port forwarding")
    p_start.add_argument("--port", type=int, help="Local port to forward")
    p_start.add_argument("--bind", help="Local bind address (default: 127.0.0.1)")
    p_start.add_argument(
        "--foreground", action="store_true",
        help="Run in the foreground instead of detaching (for systemd/Type=simple)",
    )
    p_start.add_argument("-y", "--yes", action="store_true", help="Assume yes on prompts")

    p_setup = sub.add_parser(
        "setup", help="Check/install frpc and configure the relay interactively"
    )
    p_setup.add_argument("-y", "--yes", action="store_true", help="Assume yes on prompts")

    sub.add_parser("stop", help="Stop TCP port forwarding")
    sub.add_parser("status", help="Show detailed status")
    sub.add_parser("endpoint", help="Print the public endpoint")

    p_test = sub.add_parser("test", help="Test local and/or external connectivity")
    p_test.add_argument("--local", action="store_true", help="Test only the local port")
    p_test.add_argument("--external", action="store_true", help="Test only the external endpoint")
    p_test.add_argument("--port", type=int, help="Override the local port to test")

    p_logs = sub.add_parser("logs", help="Show recent log entries")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output live")
    p_logs.add_argument("-n", type=int, default=50, help="Number of lines to show (default: 50)")

    p_restart = sub.add_parser("restart", help="Restart forwarding with the stored configuration")
    p_restart.add_argument("-y", "--yes", action="store_true", help="Assume yes on prompts")

    p_configure = sub.add_parser("configure", help="View or edit configuration")
    p_configure.add_argument("--bind")
    p_configure.add_argument("--local-port", type=int)
    p_configure.add_argument("--server-addr", help="Relay control address (frps host)")
    p_configure.add_argument("--server-port", type=int, help="Relay control port")
    p_configure.add_argument("--remote-port", type=int, help="Relay internal data port")
    p_configure.add_argument("--public-addr", help="Public address remote clients connect to")
    p_configure.add_argument("--public-port", type=int, help="Public port remote clients connect to")
    p_configure.add_argument("--relay-token", help="Relay auth token (stored outside config.toml)")
    p_configure.add_argument("--health-interval", type=int)
    p_configure.add_argument("--connect-timeout", type=int)
    p_configure.add_argument("--reconnect", dest="reconnect", action="store_true", default=None)
    p_configure.add_argument("--no-reconnect", dest="reconnect", action="store_false")
    p_configure.add_argument("--reconnect-max-attempts", type=int)
    p_configure.add_argument("--backoff-base", type=int)
    p_configure.add_argument("--backoff-max", type=int)
    p_configure.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    p_listen = sub.add_parser(
        "listen", help="Run a disposable TCP test listener (separate from forwarding)"
    )
    p_listen.add_argument("--port", type=int, required=True)
    p_listen.add_argument("--bind", default="127.0.0.1")

    sub.add_parser("_monitor", help=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Degrade box-drawing/check-mark characters gracefully instead of
    # crashing on terminals/locales that aren't UTF-8 (e.g. LANG=C
    # containers) -- the target platform is Linux with a UTF-8 locale, but
    # this costs nothing and avoids a hard crash where that isn't true.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if not args.command:
            return interactive_menu()

        if args.command == "start":
            return do_start(
                local_port=args.port, bind=args.bind,
                foreground=args.foreground, assume_yes=args.yes,
            )
        if args.command == "setup":
            return do_setup(assume_yes=args.yes)
        if args.command == "stop":
            return do_stop()
        if args.command == "status":
            return do_status()
        if args.command == "endpoint":
            return do_endpoint()
        if args.command == "test":
            local = args.local or not args.external
            external = args.external or not args.local
            return do_test(check_local=local, check_external=external, port=args.port)
        if args.command == "logs":
            return do_logs(follow=args.follow, n=args.n)
        if args.command == "restart":
            return do_restart(assume_yes=args.yes)
        if args.command == "configure":
            overrides = _collect_configure_overrides(args)
            interactive = not overrides and args.relay_token is None
            return do_configure(overrides, interactive_wizard=interactive, relay_token=args.relay_token)
        if args.command == "listen":
            return do_listen(args.bind, args.port)
        if args.command == "_monitor":
            return monitor.entrypoint()
    except PortBridgeError as exc:
        ui.print_error(exc)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception:
        logger = logging_setup.get_logger()
        logger.exception("Unexpected error in command %r", getattr(args, "command", None))
        print("\nAn unexpected error occurred. Details were written to the log.", file=sys.stderr)
        print(f"  See: portbridge logs   (file: {paths.log_file()})\n", file=sys.stderr)
        return 1

    parser.print_help()
    return 1
