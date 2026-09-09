"""The background health-check / auto-reconnect loop.

Runs either as a detached child (spawned by `portbridge start`, entered via
the hidden `portbridge _monitor` subcommand) or in-process when the caller
used `portbridge start --foreground` (e.g. under systemd). Either way, by
the time this loop starts, the initial `tailscale funnel` mapping has
already been applied by the caller -- this module's only jobs are: watch
health, reconnect the *tunnel* with backoff if it drops, and clean up on
SIGTERM/SIGINT.

It deliberately treats "local service not listening" as informational only
-- PortBridge never restarts, pokes, or otherwise interferes with whatever
is listening on the local port (or isn't).
"""

from __future__ import annotations

import os
import signal
import time

from portbridge import config as config_mod
from portbridge import core
from portbridge import logging_setup
from portbridge import state as state_mod
from portbridge.errors import PortBridgeError

_stop_requested = False


def _handle_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def entrypoint() -> int:
    cfg = config_mod.load_config()
    logging_cfg = cfg["logging"]
    logger = logging_setup.get_logger(
        logging_cfg["level"], logging_cfg["max_bytes"], logging_cfg["backup_count"]
    )
    state = state_mod.load_state()
    if not state.get("local_port") or not state.get("external_port"):
        logger.error("Monitor started with no active configuration in state.json; exiting.")
        return 1
    state["pid"] = os.getpid()
    state["status"] = state_mod.ACTIVE
    state_mod.save_state(state)
    logger.info(
        "Monitor started (pid=%s) forwarding %s:%s -> external port %s",
        os.getpid(), state["bind_address"], state["local_port"], state["external_port"],
    )
    run_loop(cfg, logger)
    return 0


def _check_health(state: dict) -> tuple[list[str], list[str]]:
    """Returns (tunnel_problems, local_problems). Only tunnel_problems
    drive reconnect behavior."""
    tunnel_problems: list[str] = []
    probe = core.probe_tailscale()
    tunnel_problems.extend(core.funnel_prereqs(probe))
    mapped = core.probe_funnel_mapped(state["external_port"])
    if mapped is False:
        tunnel_problems.append(f"external port {state['external_port']} is no longer funneled")

    local_problems: list[str] = []
    local_ok = core.probe_local(state["bind_address"], state["local_port"])
    if local_ok is False:
        local_problems.append(
            f"local service not listening on {state['bind_address']}:{state['local_port']} "
            "(PortBridge will not touch it -- start your local service)"
        )
    return tunnel_problems, local_problems


def _sleep_interruptible(seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _stop_requested:
            return True
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))
    return _stop_requested


def run_loop(cfg: dict, logger) -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    behavior = cfg["behavior"]
    interval = behavior["health_check_interval_seconds"]
    base = behavior["reconnect_backoff_base_seconds"]
    cap = behavior["reconnect_backoff_max_seconds"]
    max_attempts = behavior["reconnect_max_attempts"]

    try:
        while not _stop_requested:
            state = state_mod.load_state()
            if state.get("status") not in (state_mod.ACTIVE, state_mod.RECONNECTING):
                logger.info("State changed externally (status=%s); monitor exiting.", state.get("status"))
                return

            tunnel_problems, local_problems = _check_health(state)
            now = time.time()
            state["last_health_check"] = now
            state["health_detail"] = tunnel_problems + local_problems

            if not tunnel_problems:
                state["status"] = state_mod.ACTIVE
                state["health_ok"] = not local_problems
                state["reconnect_attempts"] = 0
                state["next_retry_at"] = None
                state_mod.save_state(state)
                if local_problems:
                    logger.warning("Tunnel healthy but local service down: %s", "; ".join(local_problems))
                else:
                    logger.info("Health check OK.")
                if _sleep_interruptible(interval):
                    break
                continue

            logger.warning("Tunnel health check failed: %s", "; ".join(tunnel_problems))
            state["health_ok"] = False

            if not behavior["reconnect"]:
                state["status"] = state_mod.FAILED
                state["last_error"] = "; ".join(tunnel_problems)
                state_mod.save_state(state)
                logger.error("Reconnect disabled in config; giving up.")
                return

            attempt = state.get("reconnect_attempts", 0) + 1
            if max_attempts and attempt > max_attempts:
                state["status"] = state_mod.FAILED
                state["last_error"] = (
                    f"Gave up after {max_attempts} reconnect attempts: "
                    + "; ".join(tunnel_problems)
                )
                state_mod.save_state(state)
                logger.error("Max reconnect attempts (%s) reached; giving up.", max_attempts)
                return

            delay = core.backoff_delay(attempt, base, cap)
            state["status"] = state_mod.RECONNECTING
            state["reconnect_attempts"] = attempt
            state["next_retry_at"] = now + delay
            state_mod.save_state(state)
            logger.info("Reconnecting: attempt %s, next retry in %.0fs", attempt, delay)

            if _sleep_interruptible(delay):
                break

            try:
                core.apply_funnel(
                    state["bind_address"], state["local_port"], state["external_port"],
                    state["mode"], allow_sudo=False,
                )
                logger.info("Reconnect attempt %s succeeded.", attempt)
            except PortBridgeError as exc:
                logger.warning("Reconnect attempt %s failed: %s", attempt, exc.message)
    finally:
        _cleanup(logger)


def _cleanup(logger) -> None:
    state = state_mod.load_state()
    if state.get("pid") == os.getpid():
        try:
            core.remove_funnel(
                state["bind_address"], state["local_port"], state["external_port"],
                state["mode"], allow_sudo=False,
            )
            logger.info("Funnel mapping removed on shutdown.")
        except PortBridgeError as exc:
            logger.warning("Could not cleanly remove funnel mapping on shutdown: %s", exc.message)
        state_mod.clear_state()
    logger.info("Monitor stopped (pid=%s).", os.getpid())
