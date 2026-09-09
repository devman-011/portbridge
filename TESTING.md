# Testing PortBridge

These tests use two machines you control: **Machine A** (runs PortBridge)
and **Machine B** (an ordinary internet client — no Tailscale, no VPN).
A few tests only need Machine A.

Machine A needs `portbridge` installed (see [INSTALL.md](INSTALL.md)),
authenticated (`sudo tailscale up`), and granted the `funnel` node
attribute (see README's First-time setup).

## 1. Basic end-to-end connection

```bash
# Machine A, terminal 1: a disposable local TCP service
portbridge listen --port 9001

# Machine A, terminal 2: start forwarding
portbridge start --port 9001
portbridge status
portbridge endpoint
# -> prints something like: yourmachine.your-tailnet.ts.net:10000
```

```bash
# Machine B (ordinary client, no Tailscale installed).
# Must be TLS-capable -- Tailscale requires TLS at its edge even in the
# default tls-terminated-tcp mode (it terminates it for you, but the
# connecting client still has to complete the handshake). A bare `nc`
# will connect at the TCP level and then get nothing through. Use one of:
openssl s_client -connect <PUBLIC_ENDPOINT_HOST>:<PUBLIC_ENDPOINT_PORT> -quiet
# or:
ncat --ssl <PUBLIC_ENDPOINT_HOST> <PUBLIC_ENDPOINT_PORT>
# then type something and press Enter
```

**Expected:** whatever you type on Machine B is echoed back (PortBridge's
`listen` test server echoes input), and it appears verbatim in Machine A
terminal 1. Data typed in either direction that reaches the other side
confirms the tunnel is forwarding bytes correctly. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#connected-but-nothing-comes-through-client-must-speak-tls)
if a plain `nc` "connects" but nothing ever arrives.

**If you use plain `nc -l -p 9001` instead of `portbridge listen`:** use a
`nc` variant/flag that keeps listening after a connection ends (e.g.
`nc -lk`), or just re-run it each time it exits. Plain `nc -l` without a
keep-listening flag accepts exactly **one** connection and then quits —
and PortBridge's own status/health checks are enough to trigger that quit
even with nobody else connecting (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#my-nc--l-listener-keeps-dying-on-its-own)),
which is why `portbridge listen` (loops forever, handles connections one
after another) is the recommended stand-in here.

## 2. Forwarding stopped

```bash
# Machine A
portbridge stop
portbridge status   # Forwarding: STOPPED
```

```bash
# Machine B
openssl s_client -connect <PUBLIC_ENDPOINT_HOST>:<PUBLIC_ENDPOINT_PORT> -quiet
```

**Expected:** Machine B's connection fails/refuses immediately — the public
endpoint no longer accepts connections once stopped.

## 3. Wrong / unsupported external port

```bash
portbridge start --port 9001 --external-port 9001
```

**Expected:** since 9001 isn't one of Funnel's allowed ports, PortBridge
prints the exact "ERROR: ... does not support external TCP port 9001"
block, lists 443/8443/10000, and asks whether to use the suggested
alternative — it does **not** silently substitute a port.

## 4. Local service absent

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

## 5. Tunnel disconnected / auto-reconnect

```bash
portbridge start --port 9001
portbridge status   # confirm ACTIVE
sudo tailscale funnel reset   # simulates the mapping being dropped externally
portbridge status
```

**Expected:** within one `health_check_interval_seconds` (15s default),
status transitions to `RECONNECTING` with an attempt counter and a "next
retry" countdown, then back to `ACTIVE` once PortBridge reapplies the
mapping. Watch it live:

```bash
portbridge logs --follow
```

## 6. Restart

```bash
portbridge start --port 9001
portbridge restart
portbridge status   # ACTIVE again, using the same ports
```

**Expected:** the old monitor is stopped, the mapping is briefly removed
and reapplied, and forwarding resumes with the same configuration.

## 7. Reboot

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

## 8. Duplicate start

```bash
portbridge start --port 9001
portbridge start
```

**Expected:** the second call does not spawn a second monitor or apply a
second Funnel config. It prints "Forwarding is already active." with the
current Local/Public mapping and asks whether to stop and reconfigure.

## 9. Invalid port

```bash
portbridge start --port 0
portbridge start --port 70000
portbridge start --port not-a-number
```

**Expected:** each is rejected with a clear message (no traceback) — the
first two via PortBridge's own range check, the third via argparse's
built-in type validation.

## 10. Unsupported public port (see also test 3)

```bash
portbridge start --port 9001 --external-port 5555 --yes
```

**Expected:** with `--yes`, PortBridge auto-accepts the suggested allowed
port (10000) instead of prompting, and forwarding starts on that port.

## 11. Provider unavailable

```bash
sudo systemctl stop tailscaled
portbridge start --port 9001
sudo systemctl start tailscaled   # restore afterward
```

**Expected:** a clear "Could not reach the tailscaled daemon" error with a
suggested diagnostic command — no traceback.

## 12. Uninstall verification

```bash
./uninstall.sh
command -v portbridge   # should report "not found"
ls ~/.config/portbridge ~/.local/state/portbridge   # still present (kept by default)
```
