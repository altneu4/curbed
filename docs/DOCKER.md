# Running the update server in Docker

A containerized version of `payload/serve.py` — the server that impersonates
`updates.energycurb.com` so an abandoned Curb Energy Monitor can be recovered
by its owner. Same recovery workflow as the main README, just packaged so
you don't need Python set up locally and it stays running reliably in the
background.

## 1. Point `updates.energycurb.com` at this server

The Curb only knows where to look because of DNS — you have to make
`updates.energycurb.com` resolve to whichever machine runs this container.
Pick one:

- **Pi-hole**: Local DNS → DNS Records → `updates.energycurb.com` → this
  host's LAN IP
- **Router**: add a static DNS override (varies by router)
- **dnsmasq**: `address=/updates.energycurb.com/<this-host-ip>`
- **Don't have any of those?** This repo includes an optional throwaway
  DNS container — see [Optional: built-in DNS](#optional-built-in-dns)
  below.

## 2. Run the server

```bash
docker compose up -d
```

This pulls the published image if one's available for this repo/fork, or
builds it locally from the `Dockerfile` if not — either way, no extra flags
needed. It listens on the host's ports 80 and 443 (mapped to unprivileged
8080/8443 inside the container — the process never runs as root).

Watch for a device checking in:

```bash
docker compose logs -f curb-update-server
```

A real device's request is highlighted in green and named by serial number
— `*** PAYLOAD DELIVERED to serial <serial>! ***` — once it downloads the
password-reset payload. Curb devices check hourly (plus a random 0–30
minute delay); rebooting the device does *not* reliably force an immediate
check (see [Troubleshooting](#troubleshooting) below) — it's still worth
doing, but budget for up to an hour either way.

## 3. Log in

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
```

Password: `curb123` (find `<curb-ip>` from your router's DHCP leases — the
Curb talks to the LAN over its HomePlug AV powerline adapter, and Docker's
own logs may not show the device's real IP; see
[Troubleshooting](#troubleshooting)). Or use the serial console (J6 header,
115200 8N1), same credentials.

From here, changing the password, adding an SSH key, backing up the device,
and redirecting its data collection are all covered in the main
[README](../README.md#after-getting-root) — nothing about that part is
Docker-specific.

## Optional: built-in DNS

If you don't already run a DNS server you control, this repo can spin up a
disposable `dnsmasq` container that redirects just
`updates.energycurb.com` and forwards everything else upstream:

```bash
echo "CURB_SERVER_IP=192.168.1.50" > .env   # this host's LAN IP
docker compose --profile dns up -d
```

Then point your Curb device's DHCP/DNS (or your whole LAN, temporarily) at
this host for DNS. Remove it once you're done:

```bash
docker compose --profile dns down
```

## Running behind an existing reverse proxy

If host ports 80/443 are already spoken for — common on a NAS (Synology,
TrueNAS, etc.) running its own reverse proxy or other services — point that
proxy at this container instead of letting it bind 80/443 directly. Set in
`.env`:

```bash
CURB_HOST_HTTP_PORT=8080
CURB_HOST_HTTPS_PORT=8443
```

Then in your proxy, add **two** rules for the source hostname
`updates.energycurb.com` — one for HTTP, one for HTTPS — both pointed at
this host's `8080`/`8443`. Both protocols matter: per the documented
request order in `docs/FINDINGS.md`, the Curb tries HTTPS first, falls back
to plain HTTP, then retries HTTPS without a serial number in the path — a
proxy rule for only one protocol will silently miss part of that sequence.
It doesn't matter whether the proxy terminates TLS with its own real
certificate or passes through to this container's self-signed one — the
Curb doesn't validate certificates either way (`wget
--no-check-certificate`).

## Configuration

`serve.py` reads these environment variables (already set correctly in
`docker-compose.yml`; override there if you need to):

| Variable | Default | Purpose |
|---|---|---|
| `CURB_HTTP_PORT` | `8080` | Port the HTTP listener binds inside the container |
| `CURB_HTTPS_PORT` | `8443` | Port the HTTPS listener binds inside the container |
| `CURB_DATA_DIR` | `/data` | Where the generated webroot cache and self-signed TLS cert/key are stored (a named volume, `curb_data`, so they survive `docker compose restart`) |
| `CURB_CERT_CN` | `updates.energycurb.com` | CN on the self-signed cert; only matters if you're redirecting a different hostname |

`docker-compose.yml` itself reads a few more from a `.env` file in this
directory:

| Variable | Default | Purpose |
|---|---|---|
| `CURB_IMAGE` | `ghcr.io/codearranger/curbed:latest` | Which registry image to pull before falling back to a local build |
| `CURB_HOST_HTTP_PORT` | `80` | Host port published for HTTP — change if something else (e.g. a NAS reverse proxy) already owns 80 |
| `CURB_HOST_HTTPS_PORT` | `443` | Host port published for HTTPS — same idea, for 443 |
| `CURB_SERVER_IP` | *(none)* | This host's LAN IP, only used by the optional DNS container |

## Troubleshooting

A few non-obvious things that came up recovering a real device with this
setup, worth knowing before you spend time chasing them:

**The device's update check is a standard hourly cron job, not a
boot-triggered one.** `/bin/run-parts /etc/cron.hourly` fires on the wall
clock, not relative to uptime — so power-cycling the Curb doesn't
necessarily produce a check within ~90 seconds; that depends on whether its
`crond` catches up on a missed run at boot, which isn't guaranteed. Budget
up to an hour after power-cycling before concluding something's wrong, and
in the meantime confirm the device actually reconnected to your network at
all (check your router's DHCP lease list for it — it also advertises via
mDNS as `curb-xxxxxxxx.local`).

**On Docker Desktop (Windows/Mac), the container never sees a real client
IP.** Docker Desktop forwards published ports into its VM "over a
shared-memory channel" rather than routing directly, so every connection —
including a real Curb device's — shows up as an internal gateway address
(e.g. `172.23.0.1`), not the device's actual LAN IP. Enabling Docker
Desktop's "Host Networking" feature does not fix this; a Docker maintainer
has confirmed it only mirrors requests via vpnkit rather than providing
genuine host networking. This means the IP the log prints alongside a
delivered payload is not reliable — always get the real IP from your
router's DHCP leases instead. (On a native Linux Docker host — no VM
boundary — this isn't an issue; real source IPs are preserved.)

**Test reachability from another device on your LAN, not just the Docker
host.** A successful `curl localhost/...` from the machine running the
container proves nothing about whether other devices on your network can
reach it — Windows Firewall (or an AP's client-isolation setting) can block
inbound LAN connections to a published port even though loopback works
fine. From your phone or another computer, confirm both:

```
http://<docker-host-LAN-IP>/api/firmware/os.tar.gz.gpg      # isolates port reachability
https://updates.energycurb.com/api/firmware/os.tar.gz.gpg   # confirms DNS + reachability together
```

The second one will throw a certificate warning in a browser — that's
expected, the cert is self-signed and the Curb doesn't validate it either
(`wget --no-check-certificate`).

## Notes

- The device accepts any TLS certificate and falls back to plain HTTP, so
  the self-signed cert here is only present because the firmware tries
  HTTPS first — it isn't providing real security, and doesn't need to be
  trusted by anything.
- `docker compose up` needs to bind host ports 80/443; on Linux that
  usually means running it as root or via `sudo`.
- This only needs to run until the device is rooted — `setup.sh` disables
  the Curb's own update checker as one of its last steps, so once you've
  logged in you can stop the container (`docker compose down`) and remove
  the DNS override. Don't repurpose this as an ongoing deployment channel
  for the device — the payload mechanism has essentially no real
  authentication (a GPG passphrase shared across every Curb ever made,
  disabled cert validation, HTTP fallback), which is exactly why it worked
  as a way in; leaving it running would leave that same door open to
  anyone else on your network.
