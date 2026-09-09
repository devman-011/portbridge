# PortBridge

PortBridge is a Linux CLI tool that exposes a TCP service running on your
machine to the public internet, so that **ordinary remote machines can
connect to it without installing any VPN client or special software**.

Only your Linux machine joins a tunneling network (Tailscale). The remote
side is a plain TCP client — `nc`, a browser, a game client, an SSH client,
whatever your service expects.

PortBridge forwards raw TCP bytes only. It does not interpret, modify, or
execute anything sent over the connection, and it does not run a remote
shell, RAT, or persistence mechanism of any kind.

```
LOCAL TCP SERVICE  (e.g. `nc -l -p 9001`, a game server, an SSH daemon...)
        |
        v
   PORTBRIDGE            <- runs only on your Linux machine
        |
        v
 TAILSCALE FUNNEL         <- your machine's tunnel into Tailscale's network
        |
        v
 PUBLIC TCP ENDPOINT       <yourmachine>.ts.net:{443,8443,10000}
        |
        v
 ORDINARY REMOTE TCP CLIENT   (no VPN, no Tailscale, no special software)
```

## Why Tailscale Funnel

PortBridge is built on [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)
because, as of this writing (verified against Tailscale's live documentation),
it's the only option that clears every one of these bars at once:

| Requirement | Tailscale Funnel |
|---|---|
| Remote client needs zero special software | Yes — plain TCP |
| Free indefinitely (not a trial) | Yes — included on the free Personal plan |
| No VPS required | Yes |
| Stable, documented CLI | Yes |

Alternatives were researched and rejected for concrete reasons — see
[Limitations](#limitations) and [Alternatives considered](#alternatives-considered)
below. None of them beat this combination without also failing one of the
above requirements.

## Supported external ports

**This is the single most important limitation to understand before using
PortBridge.** Tailscale Funnel does not support arbitrary external ports.
Per Tailscale's own documentation:

> Funnel can only listen on ports 443, 8443, and 10000.

PortBridge enforces this honestly: if you ask to forward local port 9001 to
external port 9001, and 9001 isn't one of the three allowed ports,
PortBridge will **not** silently pick a different port for you. It explains
the restriction and asks whether you want to use one of the allowed ports
instead:

```
  Local port: 9001
  Requested external port: 9001

  ERROR:
  The selected public forwarding mechanism does not support
  external TCP port 9001.

  Allowed external ports:
    443
    8443
    10000

  Would you like to use external port 10000?
  [y/N]
```

Your **local** port is not restricted — Funnel maps one of the three public
ports to whatever local port your service actually listens on. Only the
externally-visible port number is fixed to that list.

## Installation

See [INSTALL.md](INSTALL.md) for exact commands. Quick version:

```bash
git clone https://github.com/devman-011/portbridge.git
cd portbridge
./install.sh
portbridge --version
```

## First-time setup

Run:

```bash
portbridge setup
```

This is a guided, ask-before-acting check that gets Tailscale itself ready.
It walks through, in order:

1. **Is `tailscale` installed?** If not, it offers to run the official
   installer for you (`curl -fsSL https://tailscale.com/install.sh | sh`).
2. **Is the `tailscaled` background service running?** If not, it offers to
   start it (`sudo systemctl enable --now tailscaled`).
3. **Is this device authenticated?** If not, it offers to run
   `tailscale up` for you — you still complete the actual login in a
   browser yourself (Google/Microsoft/GitHub/Apple/passkey); PortBridge
   never sees or stores your credentials.
4. **(Optional)** offers to set you as the Tailscale operator
   (`sudo tailscale set --operator=$USER`) so the background auto-reconnect
   monitor doesn't need an interactive sudo password later. Without this,
   PortBridge still works fine for interactive `start`/`stop`, but an
   *unattended* reconnect after a tunnel drop will fail with a permission
   error until you either do this or run PortBridge as root (e.g. the
   systemd unit).
5. Finally, it prints the one thing it **can't** check or fix for you: the
   `funnel` ACL attribute in your tailnet's policy file (lives in your
   Tailscale account, not on this device). New tailnets grant it by
   default:

   ```json
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ],
   ```

   at https://login.tailscale.com/admin/acls. `portbridge start` will tell
   you clearly if this is missing.

Every step asks first — nothing installs or changes system state without
your confirmation (or `portbridge setup --yes` if you want it to proceed
non-interactively). `portbridge start` runs these same checks automatically
and offers the same fixes inline, so running `setup` separately is a
convenience, not a requirement.

## Starting forwarding

```bash
portbridge start --port 9001
```

or interactively:

```bash
portbridge
# choose 1) Start port forwarding, then enter the local port
```

PortBridge will, in order: validate the port, check whether your local
service is actually listening, check Tailscale is installed/authenticated/
connected, resolve the external port (prompting if your requested port
isn't in the allowed list), apply the Funnel configuration, and start a
background health-check/auto-reconnect monitor.

Running `portbridge start` again while forwarding is already active does
**not** create a duplicate tunnel — it shows the current mapping and offers
to stop/reconfigure it.

## Stopping forwarding

```bash
portbridge stop
```

This stops only the forwarding PortBridge itself started (verified by PID
and process identity, never an unrelated process) and removes the specific
Funnel mapping it created. It never touches whatever is listening on your
local port.

## Checking status

```bash
portbridge status
```

Prints Tailscale install/auth/connection state, whether your local service
is listening, current forwarding status (including reconnect attempt/
backoff countdown if reconnecting), the public endpoint, uptime, and a
health checklist that explains exactly what's wrong when something is.

`portbridge endpoint` prints just `host:port`. `portbridge test` (or
`--local`/`--external`) does a live TCP connectivity check against either
side.

## How the forwarding actually works

- `portbridge start` runs `tailscale funnel --bg --tcp=<external-port>
  tcp://<bind>:<local-port>` (or `--tls-terminated-tcp=` if you configure
  `mode = "tls-terminated-tcp"`). `--tcp` is a raw TCP forwarder — this is
  the default and what you want for a plain `nc`-style test, since it does
  not require the connecting client to perform a TLS handshake.
  `--tls-terminated-tcp` has Tailscale terminate TLS at its edge and hand
  your local service plaintext; use it only if your remote clients are
  expected to speak TLS to the public endpoint.
- Tailscale's own infrastructure (not PortBridge) handles NAT traversal,
  routing, and the public `*.ts.net` hostname/certificate. No inbound
  firewall or router port-forwarding changes are needed on your machine —
  Funnel traffic arrives via the outbound Tailscale connection your machine
  already maintains, not via a raw listening socket on your public
  interface. PortBridge never modifies firewall rules.
- PortBridge's own background monitor does **not** forward bytes itself; it
  only applies/removes the Funnel mapping and watches its health. The
  actual byte forwarding is done entirely by `tailscaled`.

## Security considerations

- PortBridge never stores a Tailscale auth token. Authentication is
  delegated entirely to `tailscale up`/`tailscale login`, which keep their
  own credentials inside `tailscaled`'s state directory.
- PortBridge only ever manages the exact port mapping you asked for. It
  never scans the network, never opens additional ports, and never expands
  scope beyond your request.
- PortBridge never modifies local firewall rules. If a firewall change were
  ever genuinely required for some setup, it would be explained and
  confirmed with you first — this version never needs to make one.
- The forwarded connection is treated as opaque bytes end to end. PortBridge
  does not parse, log payload content, or execute anything received over it.
- Prefer `bind_address = 127.0.0.1` (the default) so only Funnel — not your
  LAN — can reach the local service through PortBridge.

## Limitations

- External TCP port is restricted to 443, 8443, or 10000 (see above) — this
  is a Tailscale Funnel restriction, not a PortBridge one.
- The public hostname is always `<device>.<tailnet>.ts.net`; Funnel cannot
  use a custom domain.
- Funnel traffic is subject to Tailscale's non-configurable bandwidth
  limits (Tailscale does not publish a specific numeric cap).
- Exact CLI exit codes and error text for `tailscale` failures are not
  officially documented by Tailscale; PortBridge classifies errors from
  `stderr` text on a best-effort basis and always shows you the raw message
  alongside its interpretation.
- One local service can be forwarded at a time per PortBridge instance/
  state file (Funnel and Serve also cannot share a port number on one
  device, per Tailscale's own documentation).

### Alternatives considered

Researched and rejected when this tool was built (see also `portbridge
--help` and the code comments in `src/portbridge/tailscale.py` for sourcing):

- **Cloudflare Tunnel** — free, but its TCP service type requires the
  *remote* client to also run `cloudflared access tcp` (or WARP). Fails the
  "ordinary client, no special software" requirement.
- **Cloudflare Spectrum** — the right mechanism (ordinary clients, arbitrary
  TCP), but arbitrary/custom TCP is Enterprise-only (paid, contact-sales).
- **ngrok** — free-tier TCP endpoints exist but require a credit card on
  file, cap out at 3 concurrent endpoints / ~5,000 connections per month,
  and hand out a random address that changes on every restart.
- **bore / bore.pub** — genuinely free, zero client software, but a small
  single-maintainer hobby project with no SLA; the public relay has no
  authentication, so a requested fixed port isn't reserved and can be taken
  by someone else. A reliable deployment needs self-hosting the relay on
  your own VPS, which reintroduces the infrastructure this tool avoids.
- **playit.gg** — promising, but whether arbitrary/custom TCP is available
  on the free tier (versus a paid Premium tier) could not be confirmed from
  an official source at research time.

## Testing

See [TESTING.md](TESTING.md) for a full two-machine test procedure with
exact commands, and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common
failure modes.

## Uninstalling

```bash
./uninstall.sh            # keeps your config/state
./uninstall.sh --purge    # also deletes config/state/logs
```

## Files

| Path | Purpose |
|---|---|
| `~/.config/portbridge/config.toml` | Configuration (no secrets) |
| `~/.local/state/portbridge/state.json` | Runtime state |
| `~/.local/state/portbridge/portbridge.log` | Logs |

## Contributing

Bug reports, forks, and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, design
principles, and how to set up a dev environment.

## License

MIT — see [LICENSE](LICENSE).
