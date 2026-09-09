"""The background health-check / auto-reconnect loop.

Unlike the old Tailscale-based design (where tailscaled ran independently
and PortBridge just sent it config), frpc IS the tunnel client and must
stay running continuously -- if the process exits, the tunnel is gone.
This loop's main job is supervising frpc as a child process: start it,
watch that it's still alive, restart it with backoff if it dies. frpc has
its own internal reconnect logic for transient network drops, so this
loop mainly reacts to the frpc *process* disappearing, not brief blips.

Runs either as a detached child (spawned by `portbridge start`, entered
via the hidden `portbridge _monitor` subcommand) or in-process when the
caller used `portbridge start --foreground` (e.g. under systemd).

Local-service-down is deliberately informational only -- PortBridge never
restarts, pokes, or otherwise interferes with whatever is listening on the
local port (or isn't).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

from portbridge import config as config_mod
from portbridge import core
from portbridge import logging_setup
from portbridge import paths
from portbridge import relay as relay_mod
from portbridge import state as state_mod
from portbridge.errors import PortBridgeError

_stop_requested = False
_frpc_proc: subprocess.Popen | None = None


def _handle_signal(signum, frame):
    global _stop_requested
    _stop_requested = True


def start_and_run(cfg: dict, logger) -> None:
    """Shared by the detached-child entrypoint() and `portbridge start
    --foreground`: loads state (already written by do_start() with
    local_port/public_addr/etc.), claims it with our own PID, and runs the
    supervision loop."""
    state = state_mod.load_state()
    if not state.get("local_port") or not state.get("public_port"):
        logger.error("Monitor started with no active configuration in state.json; exiting.")
        return
    state["pid"] = os.getpid()
    state["status"] = state_mod.ACTIVE
    state_mod.save_state(state)
    logger.info(
        "Monitor started (pid=%s) forwarding %s:%s -> %s:%s",
        os.getpid(), state["bind_address"], state["local_port"],
        state["public_addr"], state["public_port"],
    )
    run_loop(cfg, logger, state)


def entrypoint() -> int:
    cfg = config_mod.load_config()
    logging_cfg = cfg["logging"]
    logger = logging_setup.get_logger(
        logging_cfg["level"], logging_cfg["max_bytes"], logging_cfg["backup_count"]
    )
    start_and_run(cfg, logger)
    return 0


def _start_frpc(cfg: dict, state: dict, logger) -> subprocess.Popen | None:
    token = config_mod.load_relay_token()
    if not token:
        logger.error("No relay token configured (portbridge configure --relay-token ...); cannot start frpc.")
        return None
    config_path = paths.frpc_config_file()
    relay_mod.write_frpc_config(
        config_path,
        server_addr=cfg["relay"]["server_addr"],
        server_port=cfg["relay"]["server_port"],
        remote_port=cfg["relay"]["remote_port"],
        token=token,
        bind_address=state["bind_address"],
        local_port=state["local_port"],
    )
    paths.ensure_dirs()
    log_fh = open(paths.log_file(), "a", encoding="utf-8")
    try:
        proc = relay_mod.spawn_frpc(config_path, log_fh)
    except PortBridgeError as exc:
        logger.error("Could not start frpc: %s", exc.message)
        return None
    finally:
        log_fh.close()  # the child holds its own duplicated fd
    return proc


def _check_local(state: dict) -> list[str]:
    local_ok = core.probe_local(state["bind_address"], state["local_port"])
    if local_ok is False:
        return [
            f"local service not listening on {state['bind_address']}:{state['local_port']} "
            "(PortBridge will not touch it -- start your local service)"
        ]
    return []


def run_loop(cfg: dict, logger, initial_state: dict) -> None:
    global _frpc_proc
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    behavior = cfg["behavior"]
    interval = behavior["health_check_interval_seconds"]
    base = behavior["reconnect_backoff_base_seconds"]
    cap = behavior["reconnect_backoff_max_seconds"]
    max_attempts = behavior["reconnect_max_attempts"]

    _frpc_proc = _start_frpc(cfg, initial_state, logger)
    if _frpc_proc is None:
        state = state_mod.load_state()
        state["status"] = state_mod.FAILED
        state["last_error"] = "frpc failed to start"
        state_mod.save_state(state)
        logger.error("frpc failed to start on first attempt; monitor exiting.")
        return

    try:
        while not _stop_requested:
            state = state_mod.load_state()
            if state.get("status") not in (state_mod.ACTIVE, state_mod.RECONNECTING):
                logger.info("State changed externally (status=%s); monitor exiting.", state.get("status"))
                return

            frpc_alive = _frpc_proc is not None and _frpc_proc.poll() is None
            local_problems = _check_local(state)
            now = time.time()
            state["last_health_check"] = now

            if frpc_alive:
                state["status"] = state_mod.ACTIVE
                state["health_ok"] = not local_problems
                state["health_detail"] = local_problems
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

            logger.warning("frpc is not running (process exited).")
            state["health_ok"] = False
            state["health_detail"] = ["frpc process exited"] + local_problems

            if not behavior["reconnect"]:
                state["status"] = state_mod.FAILED
                state["last_error"] = "frpc exited and reconnect is disabled"
                state_mod.save_state(state)
                logger.error("Reconnect disabled in config; giving up.")
                return

            attempt = state.get("reconnect_attempts", 0) + 1
            if max_attempts and attempt > max_attempts:
                state["status"] = state_mod.FAILED
                state["last_error"] = f"Gave up after {max_attempts} reconnect attempts (frpc kept exiting)"
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

            _frpc_proc = _start_frpc(cfg, state, logger)
            if _frpc_proc is not None:
                logger.info("Reconnect attempt %s: frpc restarted.", attempt)
            else:
                logger.warning("Reconnect attempt %s: frpc failed to start.", attempt)
    finally:
        _cleanup(logger)


def _sleep_interruptible(seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _stop_requested:
            return True
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))
    return _stop_requested


def _cleanup(logger) -> None:
    global _frpc_proc
    if _frpc_proc is not None and _frpc_proc.poll() is None:
        try:
            _frpc_proc.terminate()
            _frpc_proc.wait(timeout=5)
        except Exception:
            try:
                _frpc_proc.kill()
                _frpc_proc.wait(timeout=5)
            except Exception:
                pass
        logger.info("frpc stopped.")

    state = state_mod.load_state()
    if state.get("pid") == os.getpid():
        state_mod.clear_state()
    logger.info("Monitor stopped (pid=%s).", os.getpid())
