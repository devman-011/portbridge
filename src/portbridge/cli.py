"""Argument parsing, the interactive menu, and every command implementation.

Both `portbridge <subcommand>` and the interactive menu call the same do_*()
functions below, so behavior never diverges between the two entry points.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
import time

from portbridge import __version__
from portbridge import config as config_mod
from portbridge import core
from portbridge import logging_setup
from portbridge import monitor
from portbridge import netcheck
from portbridge import paths
from portbridge import process
from portbridge import state as state_mod
from portbridge import tailscale as ts
from portbridge import testserver
from portbridge import ui
from portbridge import validation
from portbridge.errors import (
    ExternalPortInUseError,
    NetworkUnavailableError,
    NotAuthenticatedError,
    PortBridgeError,
    TailscaleDaemonUnreachableError,
    TailscaleNotInstalledError,
)


# ---------------------------------------------------------------------------
# setup / dependency checks
# ---------------------------------------------------------------------------

ADMIN_ACL_URL = "https://login.tailscale.com/admin/acls"


def _confirm_system_change(prompt: str, assume_yes: bool) -> bool:
    """Gate for prompts that trigger a system-modifying action (installing
    software, starting/enabling a privileged service). Unlike ui.ask_yes_no
    (which falls back to its `default` when stdin isn't a TTY -- fine for
    low-stakes prompts like "continue anyway?"), this NEVER proceeds on its
    own just because stdin is non-interactive: it requires either the
    explicit --yes flag or a real answer at a real terminal. That matters
    here specifically because `ensure_tailscale_ready` can be reached from
    a non-interactive `portbridge start` (e.g. from a script/cron), where
    silently running `curl | sh` would be a serious overreach.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return ui.ask_yes_no(prompt, default=True)


def _run_visible(argv: list[str]) -> int:
    """Runs a command with output/prompts inherited from the terminal (so
    the user sees installer progress and any sudo password prompt), and
    turns a missing executable into a clean message instead of a
    traceback."""
    try:
        return subprocess.run(argv).returncode
    except FileNotFoundError as exc:
        print(f"  Could not run '{argv[0]}': {exc}")
        return 127


def ensure_tailscale_ready(assume_yes: bool = False) -> core.Probe:
    """Checks tailscale is installed, tailscaled is reachable, this device
    is authenticated, and the network is up -- offering to fix each gap
    interactively (never silently) before raising if it still can't
    proceed. Shared by `portbridge start` and `portbridge setup`."""
    probe = core.probe_tailscale()

    if not probe.tailscale_installed:
        print("\nTailscale isn't installed yet. PortBridge needs it to create the public endpoint.")
        if _confirm_system_change(
            "Install it now via the official installer "
            "(curl -fsSL https://tailscale.com/install.sh | sh)?", assume_yes
        ):
            print("\nRunning the official Tailscale installer (you may be asked for your sudo password)...\n")
            code = _run_visible(["sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"])
            if code != 0:
                raise PortBridgeError(
                    "The Tailscale install script did not finish successfully.",
                    "Try it manually: curl -fsSL https://tailscale.com/install.sh | sh",
                )
            probe = core.probe_tailscale()
            if probe.tailscale_installed:
                print("\nTailscale installed.")
        if not probe.tailscale_installed:
            raise TailscaleNotInstalledError()

    if not probe.daemon_reachable:
        print("\nThe tailscaled background service isn't running.")
        if _confirm_system_change(
            "Start it now (and enable it at boot) with "
            "'sudo systemctl enable --now tailscaled'?", assume_yes
        ):
            code = _run_visible(["sudo", "systemctl", "enable", "--now", "tailscaled"])
            if code != 0:
                print("  systemctl reported a problem; check: sudo systemctl status tailscaled")
            time.sleep(1)
            probe = core.probe_tailscale()
        if not probe.daemon_reachable:
            raise TailscaleDaemonUnreachableError(probe.status_error or "")

    if not probe.version_ok:
        print(
            f"\nWarning: installed tailscale ({probe.tailscale_version}) is older "
            f"than the {ts.MIN_VERSION} minimum Funnel requires; continuing anyway.\n"
            "  Upgrade with: curl -fsSL https://tailscale.com/install.sh | sh"
        )

    if not probe.authenticated:
        print("\nThis machine is not logged in to a Tailscale account.")
        print("This step needs you: it opens a browser link tied to your identity")
        print("(Google/Microsoft/GitHub/Apple/passkey) -- PortBridge never handles that itself.")
        if _confirm_system_change("Run 'tailscale up' now and open the login link?", assume_yes):
            ts.login_interactive()
            probe = core.probe_tailscale()
        if not probe.authenticated:
            raise NotAuthenticatedError()

    if not probe.connected:
        raise NetworkUnavailableError("tailscale reports this device as offline")

    if probe.health_problems:
        print("\nTailscale reports health warnings:")
        for problem in probe.health_problems:
            print(f"  - {problem}")

    return probe


def do_setup(assume_yes: bool = False) -> int:
    print(ui.box(["PORTBRIDGE SETUP"]))
    print("\nChecking Tailscale...")

    probe = ensure_tailscale_ready(assume_yes=assume_yes)
    print("\n✓ Tailscale is installed, running, authenticated, and connected.")
    if probe.dns_name:
        print(f"  This device's tailnet hostname: {probe.dns_name}")

    if os.geteuid() != 0:
        user = getpass.getuser()
        print(
            "\nOptional: PortBridge's background auto-reconnect cannot type a sudo\n"
            "password, so if it's ever needed, it will fail silently unless this\n"
            f"user ('{user}') is set as the Tailscale operator."
        )
        if _confirm_system_change(f"Set '{user}' as the Tailscale operator now?", assume_yes):
            result = ts.set_operator(user)
            if result.returncode == 0:
                print("  Operator set.")
            else:
                print(f"  Could not set it automatically ({result.stderr.strip()}).")
                print(f"  Run manually: sudo tailscale set --operator={user}")

    print(
        "\nOne thing PortBridge cannot check or fix for you (it lives in your\n"
        "Tailscale account's policy, not on this device): Funnel must be allowed\n"
        "for this device in your tailnet's ACL policy file. New tailnets allow it\n"
        "by default -- if 'portbridge start' later reports a Funnel/ACL error,\n"
        "fix it here:\n"
        f"  1. Open: {ADMIN_ACL_URL}\n"
        "  2. Make sure the policy includes:\n"
        '       "nodeAttrs": [\n'
        '         { "target": ["autogroup:member"], "attr": ["funnel"] }\n'
        "       ]\n"
        "  3. Save."
    )

    print("\nSetup check complete. You're ready to run: portbridge start --port <PORT>")
    return 0


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------

def do_start(
    local_port: int | None = None,
    external_port: int | None = None,
    bind: str | None = None,
    mode: str | None = None,
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
            f"{state['public_hostname']}:{state['external_port']}"
            if state.get("public_hostname")
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
    mode = mode or cfg["provider"]["mode"]

    if not netcheck.is_port_listening(bind, local_port):
        print(f"\nWarning: nothing is currently listening on {bind}:{local_port}.")
        if not (assume_yes or ui.ask_yes_no(
            "Continue anyway and forward it once your service starts?", default=True
        )):
            print("Aborted.")
            return 1

    probe = ensure_tailscale_ready(assume_yes=assume_yes)

    requested_external = validation.parse_port(
        external_port
        if external_port is not None
        else (cfg["network"]["external_port"] or config_mod.SUGGESTION_ORDER[0])
    )

    if requested_external not in config_mod.ALLOWED_EXTERNAL_PORTS:
        suggestion = config_mod.suggest_external_port(set())
        print(f"\n  Local port: {local_port}")
        print(f"  Requested external port: {requested_external}\n")
        print("  ERROR:")
        print("  The selected public forwarding mechanism does not support")
        print(f"  external TCP port {requested_external}.\n")
        print("  Allowed external ports:")
        for port in config_mod.ALLOWED_EXTERNAL_PORTS:
            print(f"    {port}")
        print()
        if suggestion is not None and (
            assume_yes or ui.ask_yes_no(f"  Would you like to use external port {suggestion}?", default=False)
        ):
            external_port = suggestion
        else:
            print("\nAborted -- no external port selected.")
            return 1
    else:
        external_port = requested_external

    if core.probe_funnel_mapped(external_port):
        print(f"\nExternal port {external_port} already has an active Funnel mapping on this device.")
        if not (assume_yes or ui.ask_yes_no(
            "Reset ALL existing Funnel/Serve mappings on this device and take it over?", default=False
        )):
            raise ExternalPortInUseError(external_port, "an existing mapping")
        ts.funnel_reset(allow_sudo=True)

    print("\nApplying funnel configuration...")
    core.apply_funnel(bind, local_port, external_port, mode, allow_sudo=True)

    new_state = state_mod.default_state()
    new_state.update({
        "status": state_mod.STARTING,
        "bind_address": bind,
        "local_port": local_port,
        "external_port": external_port,
        "mode": mode,
        "public_hostname": probe.dns_name,
        "started_at": time.time(),
    })
    state_mod.save_state(new_state)

    cfg["network"]["bind_address"] = bind
    cfg["network"]["local_port"] = local_port
    cfg["network"]["external_port"] = external_port
    cfg["provider"]["mode"] = mode
    config_mod.save_config(cfg)

    if foreground:
        new_state["pid"] = os.getpid()
        new_state["status"] = state_mod.ACTIVE
        state_mod.save_state(new_state)
        print(
            f"\nForwarding active (foreground). Local {bind}:{local_port} -> "
            f"public {probe.dns_name}:{external_port}\nPress Ctrl+C to stop.\n"
        )
        logger = logging_setup.get_logger(
            cfg["logging"]["level"], cfg["logging"]["max_bytes"], cfg["logging"]["backup_count"]
        )
        monitor.run_loop(cfg, logger)
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
    # this stale in-memory copy would race the child's own write and could
    # clobber ACTIVE back to STARTING, which would make the monitor think
    # it was told to stop on its very next health-check loop iteration.
    print("\nForwarding started.\n")
    print(f"  Local:  {bind}:{local_port}")
    print(f"  Public: {probe.dns_name}:{external_port}\n")
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

    removal_confirmed = True
    try:
        core.remove_funnel(
            state["bind_address"], state["local_port"], state["external_port"],
            state["mode"], allow_sudo=True,
        )
    except PortBridgeError as exc:
        removal_confirmed = False
        if not quiet:
            print(f"Warning: {exc.message}")

    external_port = state.get("external_port")
    state_mod.clear_state()

    if not quiet:
        # Tailscale's own remove/off command is authoritative: if it
        # confirmed removal (or that there was nothing to remove), trust
        # that instead of also running the heuristic `funnel status`
        # text/JSON check below -- its schema isn't documented by Tailscale
        # and can disagree with the real state, which would otherwise print
        # a confusing, self-contradicting second warning right after the
        # first one already said the mapping was gone.
        if not removal_confirmed and core.probe_funnel_mapped(external_port):
            print(
                "Warning: Tailscale may still report a mapping on that port "
                "(best-effort check). Verify with: tailscale funnel status"
            )
        print("Forwarding stopped.")
    return 0


def do_restart(assume_yes: bool = False) -> int:
    state = state_mod.load_state()
    state, _ = state_mod.detect_and_clean_stale(state)
    was_active = state["status"] != state_mod.STOPPED

    local_port = state.get("local_port")
    external_port = state.get("external_port")
    bind = state.get("bind_address")
    mode = state.get("mode")

    if was_active:
        do_stop(quiet=True)
        print("Stopped existing forwarding.")

    if not local_port:
        cfg = config_mod.load_config()
        local_port = cfg["network"]["local_port"] or None
        external_port = cfg["network"]["external_port"]
        bind = cfg["network"]["bind_address"]
        mode = cfg["provider"]["mode"]

    return do_start(
        local_port=local_port, external_port=external_port, bind=bind, mode=mode,
        assume_yes=assume_yes,
    )


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
    print("VPN/Tunnel:")
    print(f"  Installed:     {ui.yesno(probe.tailscale_installed)}")
    print(f"  Authenticated: {ui.yesno(probe.authenticated)}")
    print(f"  Connected:     {ui.yesno(probe.connected)}")
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
    print(f"  External: {snap['external_port'] or '-'}")
    print()

    if probe.dns_name and snap["external_port"]:
        print("Public endpoint:")
        print(f"  {probe.dns_name}:{snap['external_port']}")
        print()

    print("Uptime:")
    print(f"  {state_mod.format_uptime(state_mod.uptime_seconds(state))}")
    print()

    print("Health:")
    print(ui.check(
        probe.connected and probe.authenticated, "Tunnel connected",
        "; ".join(probe.health_problems) if probe.health_problems
        else (probe.status_error or "not connected or not authenticated"),
    ))
    print(ui.check(
        snap["local_listening"], "Local port reachable",
        f"nothing is listening on {snap['bind']}:{snap['local_port']}"
        if snap["local_port"] else "no local port configured",
    ))
    print(ui.check(
        bool(probe.dns_name and snap["external_port"]), "Public endpoint configured",
        "run 'portbridge start' to configure forwarding",
    ))
    if state["status"] == state_mod.ACTIVE:
        print(ui.check(
            snap["funnel_mapped"], "External mapping active on Tailscale",
            "tailscale no longer reports this external port as funneled",
        ))
    print()
    if was_stale:
        print("Note: previous forwarding state was stale (crash or reboot) and has been cleared.")
    return 0


def do_endpoint() -> int:
    state = state_mod.load_state()
    state, _ = state_mod.detect_and_clean_stale(state)
    if state["status"] != state_mod.ACTIVE and state["status"] != state_mod.RECONNECTING:
        print("Forwarding is not currently active; no public endpoint configured.")
        return 1
    probe = core.probe_tailscale()
    host = probe.dns_name or state.get("public_hostname") or "<unknown>.ts.net"
    print(f"{host}:{state['external_port']}")
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
            probe = core.probe_tailscale()
            host = probe.dns_name or state.get("public_hostname")
            if not host or not state.get("external_port"):
                print("Public endpoint unknown; cannot test.")
                ok_all = False
            else:
                ok, msg = netcheck.tcp_connect_test(host, state["external_port"], timeout=timeout)
                print(("✓ " if ok else "✗ ") + msg)
                if ok:
                    print(
                        "  (This confirms the public port accepts a TCP connection only. "
                        "Tailscale Funnel requires TLS at its edge even in this mode -- a "
                        "plain, non-TLS client will connect but get no data through. Use "
                        "'openssl s_client -connect host:port' or 'ncat --ssl' to test data "
                        "flow end-to-end.)"
                    )
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

def do_configure(overrides: dict, interactive_wizard: bool) -> int:
    cfg = config_mod.load_config()
    if interactive_wizard:
        print(ui.box(["CONFIGURE PORTBRIDGE"]))
        print()
        cfg["network"]["bind_address"] = ui.ask("Local bind address", cfg["network"]["bind_address"])
        current_local = cfg["network"]["local_port"] or ""
        raw = ui.ask("Default local port (blank = ask each time)", str(current_local) if current_local else "")
        cfg["network"]["local_port"] = validation.parse_port(raw) if raw else 0
        raw = ui.ask("Default external port", str(cfg["network"]["external_port"]))
        cfg["network"]["external_port"] = validation.parse_port(raw)
        raw = ui.ask("Funnel mode (tcp / tls-terminated-tcp)", cfg["provider"]["mode"])
        if raw in config_mod.FUNNEL_MODES:
            cfg["provider"]["mode"] = raw
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
    else:
        for (section, field), value in overrides.items():
            cfg[section][field] = value

    config_mod.save_config(cfg)
    print(f"\nSaved to {paths.config_file()}")
    return 0


def _collect_configure_overrides(args: argparse.Namespace) -> dict:
    mapping = {
        "bind": ("network", "bind_address"),
        "local_port": ("network", "local_port"),
        "external_port": ("network", "external_port"),
        "mode": ("provider", "mode"),
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
        probe = core.probe_tailscale()

        print()
        print(ui.banner())
        print()
        print("Status:")
        if probe.tailscale_installed:
            print(f"  VPN: {'Connected' if probe.connected else 'Not connected'}")
        else:
            print("  VPN: Not installed")
        print(f"  Public endpoint: {'Available' if probe.dns_name else 'Unavailable'}")
        print(f"  Forwarding: {_FORWARDING_LABEL.get(state['status'], state['status'])}")
        if not (probe.tailscale_installed and probe.authenticated and probe.connected):
            print("\n  Tailscale isn't fully set up yet -- choose 10) Setup check to fix it.")
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
        print(" 10) Setup check (install/authenticate Tailscale)")
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
  portbridge setup                           Check/install/authenticate Tailscale
  portbridge start --port 9001               Forward local port 9001
  portbridge start --port 9001 --external-port 8443
  portbridge stop                            Stop forwarding
  portbridge status                          Detailed status report
  portbridge endpoint                        Print host:port of the public endpoint
  portbridge test                            Test local + external connectivity
  portbridge test --local --port 9001        Test only a local port
  portbridge logs --follow                   Tail the log file live
  portbridge restart                         Stop then start with stored config
  portbridge configure                       Interactive configuration wizard
  portbridge listen --port 9001              Disposable test TCP echo server

Config file: ~/.config/portbridge/config.toml
State/log:   ~/.local/state/portbridge/
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portbridge",
        description="Manage TCP port forwarding from this Linux machine to the "
                     "public internet via Tailscale Funnel, without requiring the "
                     "remote machine to install any VPN client.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"portbridge {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start TCP port forwarding")
    p_start.add_argument("--port", type=int, help="Local port to forward")
    p_start.add_argument("--external-port", type=int, help="Requested public/external port")
    p_start.add_argument("--bind", help="Local bind address (default: 127.0.0.1)")
    p_start.add_argument("--mode", choices=config_mod.FUNNEL_MODES, help="Funnel mode")
    p_start.add_argument(
        "--foreground", action="store_true",
        help="Run in the foreground instead of detaching (for systemd/Type=simple)",
    )
    p_start.add_argument("-y", "--yes", action="store_true", help="Assume yes on prompts")

    p_setup = sub.add_parser(
        "setup", help="Check/install Tailscale and fix common setup gaps interactively"
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
    p_configure.add_argument("--external-port", type=int)
    p_configure.add_argument("--mode", choices=config_mod.FUNNEL_MODES)
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
                local_port=args.port, external_port=args.external_port, bind=args.bind,
                mode=args.mode, foreground=args.foreground, assume_yes=args.yes,
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
            return do_configure(overrides, interactive_wizard=not overrides)
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
