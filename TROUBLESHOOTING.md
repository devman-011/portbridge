# Troubleshooting

PortBridge tries to turn every failure into a plain-English message plus a
diagnostic command, instead of a Python traceback. If you ever see a raw
traceback, that's an unexpected error — it's also written in full to
`~/.local/state/portbridge/portbridge.log`; please include that when
reporting a bug.

## "frpc is not installed (or not on PATH)"

Quickest fix: `portbridge setup` detects this and offers to download it
for your machine's CPU architecture. To do it manually instead, grab the
right release from https://github.com/fatedier/frp/releases and put the
`frpc` binary somewhere on your `PATH` (e.g. `~/.local/bin/frpc`).

## "No relay is configured yet"

You haven't deployed a relay, or haven't told PortBridge about it yet.
See README's [Deploying your own relay](README.md#deploying-your-own-relay),
then:

```bash
portbridge setup
```

or set fields individually:

```bash
portbridge configure --server-addr <host> --server-port <port> \
  --remote-port <port> --public-addr <host> --public-port <port>
portbridge configure --relay-token <token>
```

## "Could not reach the relay at host:port"

```bash
ping -c1 <relay-host>
nc -zv <relay-host> <relay-port>   # or: portbridge test
```

Check the relay deployment is actually running (its own logs/dashboard),
and that `server_addr`/`server_port` in `portbridge configure` are
correct — that's the *control* port `frpc` connects to, not the public
data port remote clients use.

## Connected, but nothing comes through

Symptom: a remote client connects to the public endpoint fine, but no
data arrives at your local service.

Since PortBridge's relay model uses genuinely raw TCP (no TLS layer
imposed on the remote client), this usually means a config mismatch
rather than a protocol requirement:

- Confirm `remote_port` in `portbridge configure` matches `allowPorts` in
  your `frps.toml` and whatever the client is actually connecting to.
- Confirm the bridge service (if your platform needed one, e.g. Railway's
  one-port-per-service limit) is actually forwarding to `frps`'s private
  address correctly.
- `portbridge logs -n 100` shows `frpc`'s own log lines, including
  `start proxy success` on a working connection — if that line is
  missing, the tunnel never actually registered.

## "Something is already using 127.0.0.1:PORT" / port already in use

That's a check against your *local* service. Find what's using it:

```bash
ss -ltnp "sport = :9001"
```

If that's your intended service, this is expected — PortBridge is telling
you it found it listening, which is what you want before forwarding.

## "No service is listening on 127.0.0.1:PORT"

Start your local service first, or use PortBridge's disposable test
listener while you develop:

```bash
portbridge listen --port 9001
```

`portbridge start` will still let you proceed and forward the port in
advance if you confirm at the prompt — the health monitor will just report
the local side as down until your service starts.

## My `nc -l` listener keeps dying on its own

Plain `nc -l -p PORT` (without a keep-listening flag) accepts exactly
**one** TCP connection and then exits — that's normal `nc` behavior, not a
PortBridge bug. PortBridge's own status/health checks read the kernel's
socket table (`/proc/net/tcp`/`tcp6`) rather than connecting, so they
can't be the cause — but any real connection (a remote client, or
`portbridge test`) will still end a plain `nc -l`, same as it always has.

For repeated testing, prefer PortBridge's own disposable listener, which
loops and accepts connections one after another instead of exiting after
the first:

```bash
portbridge listen --port 9001
```

or use an `nc` variant/flag that keeps listening (e.g. `nc -lk` on
OpenBSD nc / ncat), if you specifically want to use `nc`.

## Forwarding shows RECONNECTING and never becomes ACTIVE

```bash
portbridge status
portbridge logs -n 100
```

This means `frpc`'s process keeps exiting and PortBridge's monitor keeps
restarting it. Check the log for `frpc`'s own error lines (bad token,
relay unreachable, port already claimed by another client on that relay).
If `reconnect_max_attempts` is set (non-zero) and exceeded, status shows
`FAILED` instead of endless `RECONNECTING` — run `portbridge restart`
once you've fixed the underlying issue.

## Machine was rebooted and forwarding didn't come back

By default PortBridge does not auto-start on boot — that's what the
optional systemd unit is for (see [INSTALL.md](INSTALL.md)). Without it,
after a reboot:

```bash
portbridge status     # will show STOPPED and note stale state was cleared
portbridge start --port <PORT>
```

## Stale / crashed state ("PID not found" cleanup message)

If the monitor process was killed outside of `portbridge stop` (crash,
`kill -9`, OOM, reboot), the next command you run detects the dead PID
automatically, clears the leftover state, and tells you it did so. This
also means `frpc` (the monitor's child) is gone too, since it dies with
its parent. This is expected self-healing, not an error — just run
`portbridge start` again.

## Unexpected error / raw-looking failure

```bash
portbridge logs -n 100
```

Every unexpected exception is logged with a full traceback even though the
terminal only shows a short notice. Include that log excerpt when
reporting an issue.
