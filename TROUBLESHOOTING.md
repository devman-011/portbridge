# Troubleshooting

PortBridge tries to turn every failure into a plain-English message plus a
diagnostic command, instead of a Python traceback. If you ever see a raw
traceback, that's an unexpected error — it's also written in full to
`~/.local/state/portbridge/portbridge.log`; please include that when
reporting a bug.

## Connected, but nothing comes through (client must speak TLS)

Symptom: a remote client "connects" to the public endpoint (no timeout,
no refused connection), you send data, but it never shows up at your
local service — and the client's connection often just ends right after.

This is a Tailscale Funnel characteristic, not a PortBridge bug. Quoting
Tailscale's own docs: *"Funnel only works over TLS-encrypted
connections"* — and that applies even to the `--tcp` ("raw") mode, not
just HTTPS. A plain, non-TLS client (bare `nc`, most simple TCP/game
clients) never completes a TLS handshake, so Tailscale's edge rejects it
before any bytes ever reach your machine. Another user hit this exact
issue with a plain Minecraft client:
[tailscale/tailscale#14240](https://github.com/tailscale/tailscale/issues/14240).

**Fix:** use PortBridge's default mode, `tls-terminated-tcp`, and connect
with a TLS-capable client:

```bash
portbridge configure --mode tls-terminated-tcp   # already the default since 1.0.2
openssl s_client -connect <host>:<port> -quiet
# or
ncat --ssl <host> <port>
```

In `tls-terminated-tcp` mode, Tailscale terminates that mandatory TLS for
you at its edge and hands your local service (plain `nc`,
`portbridge listen`, whatever) ordinary unencrypted bytes — your local
side needs no changes at all. Only the *remote* connecting client needs
the TLS-capable tool.

If you specifically need your local service to receive the raw,
still-encrypted TLS bytes itself (rare — only if your local service
already terminates TLS on its own), that's what `--mode tcp` is for
instead, but a plain client still can't be used to test it.

## "tailscale is not installed (or not on PATH)"

Quickest fix: `portbridge setup` detects this and offers to install it for
you. To do it manually instead:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
which tailscale
```

## "Could not reach the tailscaled daemon"

The `tailscaled` background service isn't running. `portbridge setup`
detects this and offers to run the fix below for you:

```bash
sudo systemctl status tailscaled
sudo systemctl enable --now tailscaled
```

If `tailscaled` isn't installed as a systemd service at all, re-run the
Tailscale install script above.

## "This machine is not logged in to a Tailscale account"

`portbridge setup` (and `portbridge start`) detects this and offers to run
`tailscale up` for you. Either way, you still have to complete the actual
login yourself in a browser — that step is tied to your identity and can't
be automated:

```bash
sudo tailscale up
```

Follow the printed URL in a browser to authenticate.

## "tailscale reports this device as offline" / network unavailable

```bash
ip addr
ping -c1 1.1.1.1
tailscale status
```

Check your general internet connectivity first, then Tailscale's own
status. `tailscale status` should show `Self` online.

## DNS resolution failed for `<host>.ts.net`

```bash
getent hosts <host>.ts.net
tailscale status --json | grep -i dnsname
```

MagicDNS may not be enabled for your tailnet, or the local resolver hasn't
picked it up yet. See https://tailscale.com/kb/1081/magicdns

## "External TCP port N is not supported by Tailscale Funnel"

This isn't a bug — Funnel is hard-restricted by Tailscale to exactly ports
443, 8443, and 10000. PortBridge will offer to use one of those instead;
see the README's [Supported external ports](README.md#supported-external-ports)
section.

## "Tailscale refused to enable Funnel: ..." / prereq errors

Usually means the `funnel` node attribute isn't granted to this device in
your tailnet policy file. Open
https://login.tailscale.com/admin/acls and confirm it includes:

```json
"nodeAttrs": [
  { "target": ["autogroup:member"], "attr": ["funnel"] }
]
```

Also confirm MagicDNS and HTTPS certificates are enabled for the tailnet
(Admin console → DNS). The very first `tailscale funnel` invocation on a
device also triggers a one-time web approval prompt — complete that if it
appears.

## "Something is already using 127.0.0.1:PORT" / port already in use

That's a check against your *local* service, not Funnel. Find what's using
it:

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
PortBridge bug. If you're testing with plain `nc`, any single incoming
connection ends it, including a completely harmless one.

(Versions before this fix landed had a real bug here: PortBridge's own
`portbridge status` and its background health-check loop tested "is the
local port listening?" by opening a real TCP connection and closing it —
which counted as nc's one connection and silently killed it, over and
over, every health-check interval. That's fixed: PortBridge now checks
listening state by reading the kernel's socket table
(`/proc/net/tcp`/`tcp6`), never by connecting, so passive status/health
checks can no longer end your listener. Update if you're on an older
checkout.)

What still **does** open a real connection, by design, because it's an
explicit on-demand test rather than a passive check: `portbridge test`
(and menu options 5/6). Running that against a plain `nc -l` will still
end it, the same as any other real client connecting — that's expected,
not a bug.

For repeated testing, prefer PortBridge's own disposable listener, which
loops and accepts connections one after another instead of exiting after
the first:

```bash
portbridge listen --port 9001
```

or use an `nc` variant/flag that keeps listening (e.g. `nc -lk` on
OpenBSD nc / ncat), if you specifically want to use `nc`.

## Permission denied running `tailscale funnel`/`tailscale up`

`tailscaled` normally requires root. Either let PortBridge prompt for
`sudo` interactively (default), or make yourself a Tailscale operator so no
`sudo` is needed at all — this is required for the *background* monitor's
automatic reconnect to work unattended, since it never issues an
interactive sudo prompt. `portbridge setup` offers to do this for you, or
run it manually:

```bash
sudo tailscale set --operator=$USER
```

## Forwarding shows RECONNECTING and never becomes ACTIVE

```bash
portbridge status
portbridge logs -n 100
tailscale funnel status
```

Common causes: the `funnel` node attribute was revoked, `tailscaled` was
restarted/updated and needs the mapping reapplied (should happen
automatically — check the log for the specific error each attempt hit), or
the operator/sudo permission issue above is blocking reapplication. If
`reconnect_max_attempts` is set (non-zero) and exceeded, status will show
`FAILED` instead of endless `RECONNECTING` — run `portbridge restart`.

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
automatically, clears the leftover state, and tells you it did so. This is
expected self-healing, not an error — just run `portbridge start` again.

## `portbridge stop` says forwarding stopped, but Tailscale still shows a mapping

```bash
tailscale funnel status
tailscale funnel reset   # clears ALL funnel/serve mappings on this device
```

`portbridge stop` warns you explicitly if this happens (rare — e.g. if
`tailscaled` itself was unreachable at the moment of stopping).

## Provider API changed / "could not parse output from the 'tailscale' CLI"

Tailscale's own docs warn that `--json` output format is "subject to
change." If a newer Tailscale release changed it in an incompatible way:

```bash
tailscale version
tailscale status --json | head -40
```

Please open an issue with that output; PortBridge falls back to text
parsing for Funnel status where possible, but a `tailscale status --json`
shape change can still surface this.

## Unexpected error / raw-looking failure

```bash
portbridge logs -n 100
```

Every unexpected exception is logged with a full traceback even though the
terminal only shows a short notice. Include that log excerpt when
reporting an issue.
