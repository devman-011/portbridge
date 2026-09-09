# PortBridge

PortBridge is a Linux CLI tool that exposes a TCP service running on your
machine to the public internet, so that **ordinary remote machines can
connect to it with a plain TCP client — no VPN, no special software, no
TLS wrapper needed on their end.**

It works by running [frp](https://github.com/fatedier/frp)'s client
(`frpc`) as a managed subprocess, connecting *out* to a small relay server
(`frps`) that you deploy yourself, on any host that gives you one public
TCP port — a $5-20/mo VPS, or a platform like Railway's TCP Proxy (the
reference deployment below uses Railway, but nothing about PortBridge is
Railway-specific).

```
LOCAL TCP SERVICE   (e.g. `nc -l -p 9000`, a game server, anything)
        |
        v
   PORTBRIDGE          <- runs only on your Linux machine; supervises frpc
        |
        v
      frpc              <- connects OUT to your relay, no inbound ports needed here
        |
        v
  YOUR RELAY (frps)      <- a small server you deploy, with one public TCP port
        |
        v
 REMOTE TCP CLIENT   (plain `nc host port` -- no VPN, no TLS, no special software)
```

## Why this design (and why not Tailscale Funnel)

PortBridge was originally built on Tailscale Funnel. In practice that
turned out to fail the actual requirement: Tailscale Funnel mandates TLS
at its edge for every connection, even in its "raw TCP" mode — a genuinely
plain client (`nc`, most game clients, stock `ssh`) cannot connect through
it without being wrapped in `openssl s_client`/`ncat --ssl` first. If your
use case tolerates that, Tailscale Funnel is still a fine option and needs
no relay deployment of your own — see the CLI's `--help` history/git log
for that version. PortBridge now defaults to a self-hosted relay because
it's the only approach that gives a truly ordinary client (bare `nc`, a
game client, anything) direct access with no client-side TLS or software
requirement at all — the tradeoff is you deploy and pay for (or use spare
capacity on) your own small relay instance.

## Deploying your own relay

This is a one-time setup, done once per relay (not per PortBridge install).
The reference deployment is two tiny services:

**1. `frps` — the relay server itself.**

`Dockerfile`:
```dockerfile
FROM alpine:3.20
RUN apk add --no-cache ca-certificates wget
ARG FRP_VERSION=0.71.0
RUN wget -q https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_amd64.tar.gz \
    && tar -xzf frp_${FRP_VERSION}_linux_amd64.tar.gz \
    && mv frp_${FRP_VERSION}_linux_amd64/frps /usr/local/bin/frps \
    && rm -rf frp_${FRP_VERSION}_linux_amd64*
COPY frps.toml /etc/frp/frps.toml
EXPOSE 7000 6000
CMD ["/usr/local/bin/frps", "-c", "/etc/frp/frps.toml"]
```

`frps.toml` (generate a real random token — `openssl rand -hex 24` —
don't use a placeholder):
```toml
bindPort = 7000

auth.method = "token"
auth.token = "REPLACE_WITH_A_LONG_RANDOM_SECRET"

allowPorts = [
  { start = 6000, end = 6000 }
]
```

Deploy this container anywhere with **one exposed public TCP port**
mapped to container port `7000` — that's the control port `frpc` connects
to. Most platforms (Railway, Render, Fly.io, a plain VPS with Docker) can
do this directly.

**2. A tiny bridge for the second port, if your platform only allows one
exposed port per service** (Railway's TCP Proxy does — check yours).
`frp` needs a *second* public port for actual client traffic (`6000` in
the config above), separate from the control port. If your platform
allows exposing two ports on one service, skip this and expose `6000`
directly instead. Otherwise, deploy a second minimal service on the same
private network as `frps`:

```dockerfile
FROM alpine:3.20
RUN apk add --no-cache socat
CMD ["sh", "-c", "socat TCP-LISTEN:6000,fork,reuseaddr TCP:<frps-service-private-hostname>:6000"]
```

Expose *this* service's port `6000` publicly instead. On Railway, that
private hostname is `<service-name>.railway.internal` — zero-config
internal DNS between services in the same project.

You now have two public addresses: the **control endpoint**
(`frps-host:7000`, only `frpc` ever talks to this) and the **public data
endpoint** (`bridge-host:6000`, what remote clients actually connect to).
Feed both into `portbridge setup`.

## Installation

```bash
git clone https://github.com/devman-011/portbridge.git
cd portbridge
./install.sh
portbridge --version
```

See [INSTALL.md](INSTALL.md) for exact commands and options.

## First-time setup

```bash
portbridge setup
```

Checks whether `frpc` is installed (offers to download it for this
machine's CPU architecture if not), then walks you through entering your
relay's control address/port, data port, public address/port, and its
auth token — the token is written to `~/.config/portbridge/relay_token`
(mode 600), **never** into `config.toml`, which stays safe to read or
share. Finishes by confirming the relay is actually reachable.

## Starting forwarding

```bash
portbridge start --port 9000
```

Validates the port, warns (but lets you proceed) if nothing's listening
there yet, checks `frpc`/relay readiness, then starts a background monitor
that runs `frpc` and supervises it — restarting it with exponential
backoff if the process dies, and never spawning duplicates if you run
`start` again while already active (it'll offer to stop/reconfigure
instead).

## Stopping forwarding

```bash
portbridge stop
```

Terminates PortBridge's own monitor process (verified by PID and process
identity, never an unrelated process), which cleanly stops its `frpc`
child as part of shutdown — that's what actually tears down the tunnel.
Your local service is never touched.

## Checking status

```bash
portbridge status
```

Shows whether `frpc` is installed, the relay is configured/reachable,
your local service is listening, current forwarding status (including
reconnect attempt/backoff countdown), the public endpoint, uptime, and a
health checklist. `portbridge endpoint` prints just `host:port`.
`portbridge test` (or `--local`/`--external`) does a live TCP check.

## How the forwarding actually works

- `portbridge start` generates an `frpc` config from `config.toml` + your
  saved token, then runs `frpc -c <generated config>` as a subprocess it
  directly supervises (unlike a design where the tunnel daemon runs
  independently, `frpc` *is* the tunnel — if the process dies, the
  tunnel is gone, so PortBridge's monitor restarts it on failure).
- `frpc` connects **outbound** to your relay's control port — no inbound
  firewall/router changes are ever needed on your machine for this to
  work, since your machine never accepts a connection from the internet
  directly.
- Your relay (`frps`) is the only thing with a public IP in this picture.
  It's real infrastructure you deploy and are responsible for — this is
  the tradeoff for arbitrary ports and zero client-side requirements.
- `frpc` has its own internal reconnect logic for transient network drops;
  PortBridge's health loop mainly reacts to the `frpc` *process* itself
  exiting, not brief blips.

## Security considerations

- The relay auth token is the one real secret PortBridge holds. It's kept
  in `~/.config/portbridge/relay_token` (mode 600), separate from
  `config.toml`, and is only ever read to generate `frpc`'s config file
  (also mode 600) at start time.
- PortBridge only ever forwards the exact local port you asked for. It
  never scans the network, never opens additional ports, and never
  expands scope beyond your request.
- PortBridge never modifies local firewall rules — none are needed, since
  `frpc` only makes outbound connections.
- The forwarded connection is treated as opaque bytes end to end.
  PortBridge does not parse, log payload content, or execute anything
  received over it.
- Prefer `bind_address = 127.0.0.1` (the default) so only `frpc` — not
  your LAN — can reach the local service through PortBridge.
- Your relay is your responsibility to secure: keep its auth token
  private (anyone with it can register tunnels through your relay), and
  treat the relay host like any other small internet-facing service.

## Limitations

- You must deploy and maintain your own relay — this is not a zero-setup,
  zero-cost solution the way a hosted tunnel service would be. See
  "Deploying your own relay" above.
- One local service can be forwarded at a time per PortBridge instance
  (one `frps` public data port per relay deployment, in the reference
  setup above).
- The public port is whatever you configured when deploying your relay —
  PortBridge doesn't renegotiate it per `start`.
- Exact `frpc` exit codes/log formats aren't a stable, versioned API;
  PortBridge treats "process alive" as the primary health signal rather
  than parsing log text, which is intentionally conservative.

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
| `~/.config/portbridge/relay_token` | Relay auth token (mode 600) |
| `~/.local/state/portbridge/state.json` | Runtime state |
| `~/.local/state/portbridge/portbridge.log` | Logs (includes `frpc`'s own log output) |

## Contributing

Bug reports, forks, and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, design
principles, and how to set up a dev environment.

## License

MIT — see [LICENSE](LICENSE).
