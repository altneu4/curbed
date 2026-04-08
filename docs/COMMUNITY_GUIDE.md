# Curb Energy Monitor — Community Recovery Guide

## What This Is

When Curb Inc. (later "Powered By Elevation") shut down their cloud services in February 2026, every Curb energy monitor became a brick. This guide documents how to regain full root access to your device and set up local data collection — no cloud required.

## What You'll Get

- **Root shell access** via serial console and SSH
- **All Lua application source code** (sampler, streamer, load-controller, etc.)
- **GPG firmware decryption passphrase** (universal across all devices)
- **Hub configuration** with sensor calibration data
- **Path to local data pipeline** (Home Assistant, InfluxDB, Grafana, MQTT)

## Key Discovery: Universal Secrets

These are the same on **every Curb device**:

| Secret | Value |
|--------|-------|
| GPG firmware passphrase | `uf0ZZkz6n@*k$XeoMm@cA%jn!Cp9M0d8` |
| GPG signing key ID | `E13C6056` ("Curb Sign") |
| API auth | Basic auth with `serialNumber:secret` (secret is per-device, in EEPROM) |
| Update mechanism | `wget --no-check-certificate` — accepts any cert, falls back to HTTP |

## Three Ways to Get Root Access

### Method 1: DNS Redirect (Easiest — No Hardware Required)

The firmware update mechanism has no security:
- Uses `wget --no-check-certificate` (accepts self-signed certs)
- Falls back to plain HTTP
- The only "protection" is GPG encryption — and the passphrase is above

**Steps:**
1. Redirect `updates.energycurb.com` to your server via your DNS (Pi-hole, router, etc.)
2. Create a `setup.sh` that changes the root password
3. Encrypt it with the GPG passphrase: `gpg --symmetric --cipher-algo AES256 --passphrase 'uf0ZZkz6n@*k$XeoMm@cA%jn!Cp9M0d8' ...`
4. Serve the encrypted payload and matching MD5 checksum via HTTPS (any cert) or HTTP
5. The device checks for updates hourly — it will download, decrypt, and execute your script as root

See the `sd_payload/` directory in this repo for ready-made payloads.

**Important:** You also need to serve a valid `os.tar.gz.gpg` and `os.tar.gz.gpg.md5sum` — the update script checks the OS first and exits on failure. Serve a dummy OS update that decrypts to a harmless `update-os.sh`.

### Method 2: Dev Board + USB Boot (Most Thorough)

If you can get a Ka-Ro TX28 Starterkit 5 (or similar SODIMM dev board):
1. Remove the TX28 SODIMM module from the Curb carrier board
2. Insert into the dev board with boot mode jumper set to USB
3. Load U-Boot via `imx_usb` / UUU over USB
4. Dump the NAND flash and extract all secrets
5. Modify the rootfs and write it back

### Method 3: SD Card Attack

The device mounts an SD card at `/data/sd`. While no scripts auto-run from SD, you can:
1. Pull the SD card from the Curb
2. Place files that will be picked up by the update mechanism
3. The `update.sh` script uses `/data/sd/tmp/` as a working directory

## Device Architecture

```
┌─────────────────────────────────────────────┐
│  Curb Energy Monitor                        │
│                                             │
│  SoC: i.MX28 (ARM926EJ-S @ 454MHz)        │
│  Module: Ka-Ro TX28-41x0 (SODIMM)         │
│  RAM: 64MB DDR2                            │
│  NAND: 128MB (Micron MT29F1G08ABAEAWP)     │
│  OS: Linux 3.16.0-karo, BusyBox, Buildroot │
│  Network: QCA7000 HomePlug AV (powerline)  │
│                                             │
│  NAND Layout:                              │
│  0x000000 - 0x300000  U-Boot (3MB)         │
│  0x300000 - 0x380000  Environment (512K)    │
│  0x380000 - 0x400000  Environment2 (512K)   │
│  0x400000 - 0x800000  Linux kernel 1 (4MB)  │
│  0x780000 - actual kernel load offset       │
│  0x800000 - 0xC00000  Linux kernel 2 (4MB)  │
│  0xC00000 - 0xD80000  rootfs1 (UBI)        │
│  0xD80000 - 0x2D80000 rootfs2 (UBI/UBIFS)  │
│  0x2D80000 - end      userfs (UBI/UBIFS)   │
│                                             │
│  Active boot: kernel2 + rootfs2 (A/B)      │
└─────────────────────────────────────────────┘
```

## Data Pipeline

The Curb reads energy from ADE chips via SPI, processes it in Lua, and streams to the cloud:

```
ADE Chips (SPI) → sampler.lua → Message Queue → streamer.lua → Cloud API
                                              → load-controller.lua → Aggregation
```

### Sample Data Format

Samples are compressed MessagePack with zlib and a CRC32 trailer:
```
payload → MessagePack.pack() → zlib.compress() → append CRC32 → HTTP POST
```

Each sample contains:
```json
{
  "t": 1774327936,           // Unix timestamp
  "h": "xxxxxxxx",          // Hub serial number
  "g": [                     // Groups (one per ADE chip pair)
    {
      "v": 121.5,            // Voltage (V)
      "f": 60.0,             // Frequency (Hz)
      "c": [                 // Channels (one per CT clamp)
        {
          "i": 0.5,          // Current (A)
          "w": 60.2,         // Power (W)
          "var": 1.2,        // Reactive power (VAR)
          "p": 0.99          // Power factor
        }
      ]
    }
  ]
}
```

### API Authentication

All API calls use HTTP Basic Auth:
```
Authorization: Basic base64(serialNumber:secret)
```
- `serialNumber`: From EEPROM (e.g., `xxxxxxxx`)
- `secret`: Per-device, stored in EEPROM (e.g., `your-device-secret-from-eeprom`)

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v3/samples/{serial}` | POST | Energy sample data (compressed MessagePack) |
| `/v3/diagnostics/{serial}` | POST | Device diagnostics (compressed MessagePack) |
| `/v3/hub_config/{serial}` | GET | Download hub configuration (JSON) |
| `/v3/messages/{serial}` | GET/POST | Hub messaging (JSON) |

### Built-in InfluxDB Support

The firmware already has `influx.lua` that can post to InfluxDB line protocol! It was hardcoded to `http://138.68.19.36:8086/write?db=curb&precision=s` (a Curb development server). You can modify this to point to your local InfluxDB.

## Setting Up Local Data Collection

### Option A: Modify the Streamer (Best)

Edit `/data/hub-config.json` to point endpoints at your local server:
```json
{
  "endpoints": {
    "samples": "http://your-server:8080/v3/samples",
    "messages": "http://your-server:8080/v3/messages",
    "hub_config": "http://your-server:8080/v3/hub_config",
    "diagnostics": "http://your-server:8080/v3/diagnostics"
  }
}
```

Build a server that:
1. Accepts the compressed MessagePack POST
2. Decompresses (zlib) and unpacks (MessagePack)
3. Stores in InfluxDB/TimescaleDB
4. Serves a static config on GET

### Option B: Scrape the Status Page (Simplest)

The HTTP status page at port 80 already exposes live power data. Use the existing [Curb-to-MQTT](https://github.com/pvanbaren/Curb-to-Mqtt) project to scrape it.

### Option C: Modify influx.lua (Direct InfluxDB)

Edit `/data/lamarr/influx.lua` and change the URL to your local InfluxDB instance. Enable it in the streamer configuration.

## Important Files on the Device

| Path | Purpose |
|------|---------|
| `/data/hub-config.json` | Hub configuration with sensor calibration |
| `/data/lamarr/*.lua` | All application Lua scripts |
| `/root/.gnupg/passphrase` | GPG passphrase for firmware updates |
| `/etc/shadow` | Password hashes |
| `/etc/init.d/S*` | Init scripts (boot sequence) |
| `/etc/hm.conf` | Health monitor / process supervisor config |
| `/usr/local/sbin/update.sh` | Firmware update script |
| `/data/sd/` | SD card mount point (SQLite databases) |
| EEPROM (`i2c-0/0-0050`) | JSON: serialNumber, secret, buildCode, hardwareVersion, modelNumber |

## SSH Access

After changing the root password, SSH is available:
```bash
ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
```
The old Dropbear only supports legacy `ssh-rsa`. Add to `~/.ssh/config`:
```
Host curb
    HostName <curb-ip>
    User root
    PubkeyAcceptedAlgorithms +ssh-rsa
```

## Hardware Notes

- **Serial console**: J6 header, FTDI 3.3V, 115200 8N1
- **Network**: <curb-ip> via HomePlug AV powerline (QCA7000 on SPI)
- **U-Boot**: Boot delay overridden by Curb — cannot interrupt without hardware mod
- **JTAG**: J9 header (1.27mm pitch), no disable fuse — works with OpenOCD
- **USB boot**: SODIMM pin 8 (BOOTMODE) LOW = USB/UART recovery

## Credits

Reverse engineered April 2026. This work is for educational purposes and to help
Curb device owners recover functionality from their own hardware after the manufacturer
abandoned the product.
