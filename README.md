# curbed

Root access and local data recovery for the **Curb Energy Monitor** — a home energy monitoring device bricked when Curb Inc. shut down their cloud services in February 2026.

## What Is This?

The Curb Energy Monitor is a whole-home energy monitoring system that relied entirely on cloud services. When the company shut down, every device became a brick — still sampling energy data, but unable to do anything with it. This project gives you your device back.

## Quick Start (No Hardware Required)

You need: a computer on your network running Python 3, and the ability to add a DNS entry.

### 1. Add DNS redirect

Point `updates.energycurb.com` to the machine that will run the server:

- **Pi-hole**: Local DNS → DNS Records → `updates.energycurb.com` → `<your-server-ip>`
- **Router**: Add a static DNS entry (varies by router)
- **dnsmasq**: `address=/updates.energycurb.com/<your-server-ip>`

### 2. Run the server

```bash
cd payload
sudo python3 serve.py
```

### 3. Reboot your Curb

Power cycle the device. The update runs ~90 seconds after boot.

The server will show green text when the payload is delivered. The device reboots twice (once for the dummy OS update, once after the password change).

### 4. Log in

```bash
ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
```
Password: `curb123`

Or via serial console (J6 header, 115200 8N1): `root` / `curb123`

> **Note:** The default IP `<curb-ip>` is assigned via DHCP over the HomePlug AV powerline network. Your device may have a different IP — check your router's DHCP leases.

## How It Works

The Curb's firmware update mechanism has no real security:

1. `wget --no-check-certificate` — accepts any SSL certificate
2. Falls back to plain HTTP
3. GPG symmetric encryption — but the passphrase is the same on every device

We serve a fake "firmware update" that the device downloads, decrypts with its own GPG passphrase, and executes. The payload changes the root password and enables SSH.

## What the Payload Does

The `setup.sh` script (viewable in `payload/setup.sh`) runs as root and:

- Changes `root` and `curb` passwords to `curb123`
- Enables SSH password authentication (removes Dropbear `-s` flag)
- Disables the update script (so it stops hitting the fake server)

All changes are to `/etc/shadow` and `/etc/default/dropbear` on the read-only rootfs. The rootfs is temporarily remounted read-write, modified, synced, and remounted read-only.

## After Getting Root

### Change the Default Password

```bash
mount -o remount,rw /
passwd root
sync
mount -o remount,ro /
```

### Add Your SSH Key

```bash
mount -o remount,rw /
cat >> /root/.ssh/authorized_keys << 'EOF'
your-ssh-public-key-here
EOF
chmod 600 /root/.ssh/authorized_keys
sync
mount -o remount,ro /
```

### Set Up Local Data Collection

The device is already collecting energy data. You just need to redirect it to a local server.

**Option A: Edit the config** — Point the API endpoints at your local server:
```bash
vi /data/hub-config.json
# Change the "endpoints" URLs to your server
```

**Option B: Scrape the status page** — The device serves live data on port 80. Use [Curb-to-MQTT](https://github.com/pvanbaren/Curb-to-Mqtt) to feed it into Home Assistant.

**Option C: Use the built-in InfluxDB support** — The firmware includes `influx.lua` that posts to InfluxDB line protocol. Edit `/data/lamarr/influx.lua` to point at your instance.

### Back Up Your Device

```bash
ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
mkdir -p /data/sd/nand_dump
for mtd in 0 1 2 3 4 5 6 7 8; do
    name=$(grep "mtd${mtd}:" /proc/mtd | awk -F'"' '{print $2}')
    dd if=/dev/mtd${mtd}ro of=/data/sd/nand_dump/mtd${mtd}_${name}.bin bs=4096
done
```

Then copy `/data/sd/nand_dump/` to your computer.

## Device Details

| Component | Details |
|-----------|---------|
| SoC | Freescale/NXP i.MX28 (ARM926EJ-S @ 454MHz) |
| Module | Ka-Ro TX28-41x0 (SODIMM form factor) |
| RAM | 64MB DDR2 |
| NAND | 128MB Micron MT29F1G08ABAEAWP |
| OS | Linux 3.16.0-karo, BusyBox v1.24.1, Buildroot 2016.02 |
| Network | QCA7000 HomePlug AV (powerline) on SPI |
| App | LuaJIT scripts in `/data/lamarr/` |

### NAND Partition Layout

| Partition | Offset | Size | Contents |
|-----------|--------|------|----------|
| u-boot | 0x000000 | 1MB | Bootloader |
| env | 0x100000 | 384KB | U-Boot environment |
| linux1 | 0x400000 | 6MB | Kernel (slot A) |
| linux2 | 0x400000 | 6MB | Kernel (slot B, active) |
| rootfs1 | — | 32MB | Root filesystem (slot A) |
| rootfs2 | 0xD80000 | 32MB | Root filesystem (slot B, active) |
| userfs | 0x2D80000 | 50MB | User data (`/data`) |
| dtb | — | 512KB | Device tree |
| bbt | — | 512KB | Bad block table |

### API Endpoints

The device authenticates with HTTP Basic Auth (`serialNumber:secret` from EEPROM):

| Endpoint | Method | Format |
|----------|--------|--------|
| `/v3/samples/{serial}` | POST | Compressed MessagePack (zlib + CRC32) |
| `/v3/diagnostics/{serial}` | POST | Compressed MessagePack |
| `/v3/hub_config/{serial}` | GET | JSON |
| `/v3/messages/{serial}` | GET/POST | JSON |

### Sample Data Format

Each energy sample contains per-channel power data:
```json
{
  "t": 1774327936,
  "h": "serialnum",
  "g": [{
    "v": 121.5,
    "f": 60.0,
    "c": [
      {"i": 0.5, "w": 60.2, "var": 1.2, "p": 0.99},
      {"i": 0.1, "w": 12.0, "var": 0.3, "p": 0.98}
    ]
  }]
}
```

## SSH Notes

The device runs an old version of Dropbear SSH that only supports the legacy `ssh-rsa` algorithm. Add this to your `~/.ssh/config`:

```
Host curb
    HostName <curb-ip>
    User root
    PubkeyAcceptedAlgorithms +ssh-rsa
```

## Files in This Repo

```
payload/                  — Ready-to-use root access payload
  serve.py                — Python HTTPS/HTTP server
  setup.sh                — The script that runs on the device
  os.tar.gz.gpg           — Dummy OS update (GPG encrypted)
  os.tar.gz.gpg.md5sum    — OS checksum
  update.tar.gz.gpg       — Password change payload (GPG encrypted)
  update.tar.gz.gpg.md5sum — Software checksum
docs/
  FINDINGS.md             — Complete reverse engineering findings
  COMMUNITY_GUIDE.md      — Detailed technical guide
  JOURNEY.md              — The reverse engineering process and pitfalls
```

## Documentation

- **[Reverse Engineering Findings](docs/FINDINGS.md)** — Hardware details, NAND layout, network analysis, access attempts, and everything discovered about the device
- **[Community Technical Guide](docs/COMMUNITY_GUIDE.md)** — Detailed guide covering all three root access methods, data pipeline setup, API formats, and device architecture
- **[The Journey](docs/JOURNEY.md)** — The full story of how we went from a bricked device to root access, including 50 hours of failed brute forcing, building U-Boot from source, and the DNS redirect attack that finally worked


## Support This Project

This project required purchasing a $250 development board and weeks of reverse engineering. If it saved your Curb device from the landfill, consider buying us a coffee:

**[☕ ko-fi.com/codearranger](https://ko-fi.com/codearranger)**

## Community

- [Home Assistant thread](https://community.home-assistant.io/t/curb-energy-monitor-live-data-to-home-assistant/842428)
- [Curb-to-MQTT](https://github.com/pvanbaren/Curb-to-Mqtt) — Status page scraper → MQTT → Home Assistant

## License

This project is for educational purposes and to help Curb device owners recover functionality from their own hardware after the manufacturer abandoned the product.

MIT License
