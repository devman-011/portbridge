"""Spawning and terminating PortBridge's own background monitor process.

This never touches any process PortBridge didn't start itself: spawn_monitor
returns the exact PID of the child it just created, and terminate_monitor
only ever signals a PID that state.py has already confirmed (a) is alive
and (b) has "portbridge" in its /proc/<pid>/cmdline.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from portbridge import paths


def spawn_monitor() -> int:
    """Launches `portbridge _monitor` fully detached: new session (so it
    survives the parent shell exiting), stdin closed, stdout/stderr appended
    to the log file rather than inherited from the terminal."""
    paths.ensure_dirs()
    log_path = paths.log_file()
    log_fh = open(log_path, "a", encoding="utf-8")
    argv = [sys.executable, "-m", "portbridge", "_monitor"]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
        close_fds=True,
    )
    log_fh.close()  # The child holds its own duplicated fd; safe to close ours.
    time.sleep(0.3)
    if proc.poll() is not None:
        raise RuntimeError(
            f"Monitor process exited immediately (code {proc.returncode}); "
            f"check {log_path} for details."
        )
    return proc.pid


def terminate_monitor(pid: int, timeout: float = 5.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.2)
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
