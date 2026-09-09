# Testing PortBridge

These tests use two machines you control: **Machine A** (runs PortBridge)
and **Machine B** (an ordinary internet client — no VPN, no special
software, just plain `nc`). A few tests only need Machine A.

Machine A needs `portbridge` installed (see [INSTALL.md](INSTALL.md)) and
`portbridge setup` completed against a relay you've already deployed (see
README's [Deploying your own relay](README.md#deploying-your-own-relay)).

## 1. Basic end-to-end connection

```bash
# Machine A, terminal 1: a disposable local TCP service
portbridge listen --port 9001

# Machine A, terminal 2: start forwarding
portbridge start --port 9001
portbridge status
portbridge endpoint
# -> prints your relay's public host:port
```

```bash
# Machine B (ordinary client, no special software needed)
nc <PUBLIC_ENDPOINT_HOST> <PUBLIC_ENDPOINT_PORT>
# type something and press Enter
```

**Expected:** whatever you type on Machine B is echoed back (PortBridge's
`listen` test server echoes input), and it appears verbatim in Machine A
terminal 1. Data typed in either direction that reaches the other side
confirms the tunnel is forwarding bytes correctly.

**If you use plain `nc -l -p 9001` instead of `portbridge listen`:** use a
`nc` variant/flag that keeps listening after a connection ends (e.g.
`nc -lk`), or just re-run it each time it exits. Plain `nc -l` without a
keep-listening flag accepts exactly **one** connection and then quits —
that's normal `nc` behavior, not a PortBridge issue (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#my-nc--l-listener-keeps-dying-on-its-own)).

## 2. Forwarding stopped

```bash
# Machine A
portbridge stop
portbridge status   # Forwarding: STOPPED
```

```bash
# Machine B
nc <PUBLIC_ENDPOINT_HOST> <PUBLIC_ENDPOINT_PORT>
```

**Expected:** Machine B's connection fails/refuses immediately — the public
endpoint no longer accepts connections once stopped, and `frpc` is
confirmed gone (`pgrep -fa frpc` on Machine A shows nothing).

## 3. Local service absent

```bash
# Make sure nothing is listening on 9001 first
portbridge start --port 9001
```

**Expected:** PortBridge warns "nothing is currently listening on
127.0.0.1:9001" and asks whether to continue anyway. If you continue,
`portbridge status` shows `Local service: Listening: NO` and the health
checklist marks "Local port reachable" with an explanation — while
`Forwarding: Status` can still be `ACTIVE` (the tunnel itself is healthy;
only your local service is down, and PortBridge correctly does not treat
that as a tunnel failure).

## 4. Tunnel disconnected / auto-reconnect

```bash
portbridge start --port 9001
portbridge status   # confirm ACTIVE
pkill -f "frpc -c"  # simulates frpc crashing
portbridge status
```

**Expected:** within one `health_check_interval_seconds` (15s default),
status transitions to `RECONNECTING` with an attempt counter and a "next
retry" countdown, then back to `ACTIVE` once the monitor restarts `frpc`.
Watch it live:

```bash
portbridge logs --follow
```

## 5. Restart

```bash
portbridge start --port 9001
portbridge restart
portbridge status   # ACTIVE again, using the same local port
```

**Expected:** the old monitor (and its `frpc` child) is stopped, a new one
is started, and forwarding resumes with the same local port. Confirm only
one `frpc`/monitor pair is running: `pgrep -fa "frpc -c|portbridge _monitor"`.

## 6. Reboot

```bash
portbridge start --port 9001
sudo reboot
# after reboot:
portbridge status
```

**Expected:** without the optional systemd unit installed, PortBridge does
**not** auto-resume (by design — see README). `portbridge status` detects
the state file references a PID that no longer exists, reports it as stale
and cleared, and shows `STOPPED`. Run `portbridge start --port 9001` again
to resume. (With the systemd unit installed and enabled, forwarding
resumes automatically instead — verify with `systemctl status portbridge`.)

## 7. Duplicate start

```bash
portbridge start --port 9001
portbridge start
```

**Expected:** the second call does not spawn a second monitor or a second
`frpc`. It prints "Forwarding is already active." with the current
Local/Public mapping and asks whether to stop and reconfigure.

## 8. Invalid port

```bash
portbridge start --port 0
portbridge start --port 70000
portbridge start --port not-a-number
```

**Expected:** each is rejected with a clear message (no traceback) — the
first two via PortBridge's own range check, the third via argparse's
built-in type validation.

## 9. Relay unreachable

```bash
portbridge configure --server-addr does-not-exist.invalid
portbridge start --port 9001
portbridge configure --server-addr <your real relay address>   # restore afterward
```

**Expected:** a clear "Could not reach the relay at ...:..." error with a
suggested diagnostic command — no traceback.

## 10. Uninstall verification

```bash
./uninstall.sh
command -v portbridge   # should report "not found"
ls ~/.config/portbridge ~/.local/state/portbridge   # still present (kept by default)
```
