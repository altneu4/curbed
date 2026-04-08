# Energy Curb Reverse Engineering Findings

## Device Overview

| Field | Value |
|-------|-------|
| Product | Energy Curb Home Energy Monitor |
| Company | Curb Inc. (defunct as of ~Feb 2026, brand now under "Powered By Elevation") |
| Serial Number | xxxxxxxx |
| Hardware Version | 1.5 |
| Model Number | 00615 |
| Board S/N | XXXXXXXXXXXXX |
| OS Version | 3.1.0-f8117d6 |
| Software Version | 3.3.0-2975f92 |
| Board Copyright | (c) 2016 Curb Inc. |
| In service since | At least April 2017 (SG7000 update markers on SD card) |

## Hardware Architecture

### Compute Module (SODIMM form factor)
The main compute is a **Ka-Ro TX28 System-on-Module** plugged into a standard DDR SODIMM socket on the carrier board.

| Component | Detail |
|-----------|--------|
| SoC | Freescale/NXP i.MX28 rev1.2 (ARM926EJ-S) at 454 MHz |
| Module | Ka-Ro TX28-41x0 |
| NAND Flash | Micron MT29F1G08ABAEAWP — 128MB SLC, 2048 byte page, 64 byte OOB, 128KB erase block |
| RAM | 64 MiB DDR2 (Nanya, on back of SODIMM module) |
| Clock | GD24000 CLK01 oscillator |

### Carrier Board
| Component | Detail |
|-----------|--------|
| Power Supply | RECOM RAC10-05SC/277 — 5V, 100-277V AC input (runs directly off mains) |
| HomePlug AV | ST&T SG7000.4 (QCA7000) — powerline networking to CT sensor hub |
| Analog Frontend MCU | Atmel ATSAM D21 (ARM Cortex-M0+) — handles CT current measurements |
| Ethernet | Single RJ45 jack (eth1, active interface via SPI-connected QCA7000) |
| USB | USB-A host port (USB OTG wired as host — not usable for USB boot without hardware modification) |
| SD Card | Standard slot, ext4 formatted, 8GB |
| Level Shifter | U9 (8-pin, near J6 FTDI header) |

### CT Sensor Hub (separate unit, connected via powerline)
| Component | Detail |
|-----------|--------|
| PLC Chip | QCA7420 |
| MAC | C4:71:54:EF:31:59 |
| Firmware | MAC-QCA7420-1.3.0.2134-00-20151212-CS |
| Role | CCO (Central Coordinator) on the HomePlug AV network |

### Connectors & Headers

| Connector | Type | Purpose | Status |
|-----------|------|---------|--------|
| J6 | 1x6 pin, 2.54mm pitch | **FTDI serial console** (labeled V5_FTDI on back) | Header soldered, FTDI cable on order |
| J9 | 2x4 pin, 1.27mm pitch | Debug header (likely JTAG/SWD + UART) | Accessible but needs fine-pitch adapter |
| J3 | Terminal block | CT clamp connector | In use |
| J11 | Terminal block | Phase A / Phase B coil connectors | In use |
| J4 | Unknown | Near Ethernet jack | |
| J14 | Unknown | Near SODIMM socket | |
| USB-A | USB host port | USB host (OTG wired as host) | Cannot be used for USB boot without mod |

#### J6 Serial Console Pinout (confirmed with multimeter)
```
Square pad                                    Round pad
   ●        ●        ●        ●        ●        ●
  Pin 1    Pin 2    Pin 3    Pin 4    Pin 5    Pin 6
  GND      CTS      VCC      TX       RX       RTS
```
- Pin 1 (square pad) = GND — **confirmed with multimeter**
- Standard FTDI cable color code: Black=GND, Brown=CTS, Red=VCC, Orange=TXD, Yellow=RXD, Green=RTS
- Serial settings: **115200 baud, 8N1**
- Device runs `getty` on `ttyAMA0` at 115200
- FTDI cable ordered: DTECH USB to TTL Serial 5V Adapter with FT232RL (6-pin 0.1" female header)

### Key Test Points
| Test Point | Label | Function |
|------------|-------|----------|
| TP22 | GND | Ground |
| TP30 | D21_SW0 | **BOOTMODE** — i.MX28 boot mode select (SODIMM pin 8). Tie to GND for USB boot. |
| TP32 | D21_ID | Chip ID |
| TP40 | D21_RESETN | Reset line |
| TP43 | — | Near FTDI header |
| TP48 | — | Near FTDI header |
| TP86 | — | Near FTDI header |
| TP83 | AD2_REF_OUT | Analog reference |
| TP84 | AD1_REF_OUT1 | Analog reference |
| TP85 | MON_REF_OUT1 | Monitor reference |

## SoC Security Features (i.MX287)

Key points:

| Feature | Status |
|---------|--------|
| NVRAM | **None** — no battery-backed NVRAM on i.MX28 |
| OTP eFuses | 1,280 bits (40 × 32-bit words) via OCOTP controller |
| Boot encryption | AES-128 via DCP (Data Co-Processor) — **separate from GPG firmware encryption** |
| HAB (Secure Boot) | HAB4 supported, likely in **OPEN mode** (default, auth errors ignored) |
| JTAG disable | **No eFuse to disable JTAG** — controlled only by DEBUG pin state |
| Crypto key | 128-bit OTP key for boot encryption (likely read-locked, but irrelevant to GPG passphrase) |
| USB boot | Supported — ROM enters USB HID mode when BOOTMODE pin pulled LOW |

**Critical insight**: The GPG passphrase for firmware updates is stored as a plain file (`/root/.gnupg/passphrase`) on the NAND filesystem, NOT in eFuses. The OTP crypto key is for boot-level encryption only — a completely separate mechanism.

## Software Architecture

### Operating System
- Linux 3.16.0-karo (built Oct 14, 2020 with Buildroot 2016.02-rc2 / GCC 4.8.5)
- BusyBox v1.24.1 init system
- 64 MiB RAM (40MB available to userspace after kernel/CMA reservation)
- **Dual A/B boot scheme** — two copies of kernel and rootfs for safe updates
- Active boot slot: **B** (linux2 + rootfs2)
- UBIFS root filesystem (28MB usable) on UBI volume 0 (mtd5/rootfs2)
- UBIFS user filesystem (43MB usable) on UBI volume 1 (mtd6/userfs) at `/data`
- ext4 on SD card (7.4GB) at `/data/sd`
- Kernel command line: `init=/linuxrc console=ttyAMA0,115200 ro debug panic=1 ubi.mtd=rootfs2 ubi.mtd=userfs root=ubi0:rootfs rootfstype=ubifs`

### Running Services

| Process | Binary | User | Description |
|---------|--------|------|-------------|
| linuxrc (init) | /sbin/init (BusyBox) | root | Init system |
| syslogd | /sbin/syslogd -n -b 1 -s 100 | root | System logging (1 rotation, 100KB max) |
| klogd | /sbin/klogd -n | root | Kernel logging |
| crond | /usr/sbin/crond | root | Cron scheduler |
| dropbear | /usr/sbin/dropbear -s -R | root | SSH server (key-only auth, `-s` disables passwords) |
| lighttpd | /usr/sbin/lighttpd -f /etc/lighttpd/lighttpd.conf | www-data | Web server (static status page on port 80) |
| avahi-daemon | avahi-daemon | avahi | mDNS (advertises as `curb-xxxxxxxx.local`) |
| hm | /usr/bin/hm -c /etc/hm.conf | root | Hardware monitor daemon (manages Lua processes) |
| udhcpc | udhcpc -R -b | root | DHCP clients for eth0 and eth1 |
| htpdate | /usr/bin/htpdate -s -t -l -e -D | root | HTTP-based time sync (v1.1.3, uses google.com, linux.org, ntp.org) |
| getty | /sbin/getty -L ttyAMA0 115200 linux | root | Serial console login |
| sampler.lua | luajit /data/lamarr/sampler.lua | curb | Reads energy samples from hardware (1/sec) |
| streamer.lua | luajit /data/lamarr/streamer.lua | curb | Streams data to cloud (POST to border server) |
| load-controller.lua | luajit /data/lamarr/load-controller.lua | curb | Aggregates and controls loads |

### Application Layer (Lamarr)
The application software lives in `/data/lamarr/` and consists of LuaJIT scripts. Named after Hedy Lamarr. Known files from the update log:

- `sampler.lua` — reads raw energy samples from the hardware
- `streamer.lua` — streams data to the cloud API
- `load-controller.lua` — aggregates samples and manages loads
- `config.lua` — configuration management
- `hub-messaging.lua` — communication with CT sensor hub
- `compressed-message.lua` — message compression
- `ade.lua` — ADE (Analog Devices Energy) chip interface
- `daemonize.lua` — process daemonization
- `diags.lua` — diagnostics
- `dweet.lua` — dweet.io integration
- `influx.lua` — InfluxDB integration

### NAND Partition Layout (confirmed from boot log)

The device uses a **dual A/B partition scheme** for safe over-the-air updates:

```
Offset              Size    Name      Contents
0x000000020000      1MB     u-boot    U-Boot 2013.07 (Apr 23 2015)
0x000000120000      384KB   env       U-Boot environment (bootdelay overridden)
0x000000180000      6MB     linux1    Kernel slot A
0x000000780000      6MB     linux2    Kernel slot B (ACTIVE — Linux 3.16.0-karo)
0x000000D80000      32MB    rootfs1   Root filesystem slot A
0x000002D80000      32MB    rootfs2   Root filesystem slot B (ACTIVE)
0x000004D80000      49MB    userfs    User data — /data/lamarr/*.lua, hub-config.json
0x000007F00000      512KB   dtb       Device tree blob
0x000007F80000      512KB   bbt       Bad block table (read-only)
```

- NAND: Micron MT29F1G08ABAEAWP, 128MB SLC
- Page size: 2048 bytes, OOB: 64 bytes
- PEB (Physical Erase Block): 128 KiB, LEB (Logical Erase Block): 124 KiB (126976 bytes)
- UBI overhead: 4096 bytes per PEB (2048 VID header + 2048 data offset)
- Bad PEBs: 0 (both rootfs2 and userfs report 0 bad blocks)

### U-Boot Details

| Field | Value |
|-------|-------|
| Version | U-Boot 2013.07 (Apr 23 2015 - 13:42:37) |
| Boot source | NAND, 3V3 |
| Board ID | Ka-Ro TX28-41x0 |
| Baseboard | stk5-v3 |
| MAC from fuse | xx:xx:xx:xx:xx:xx |
| Boot delay | **Overridden to 0 or -1** — no "Hit any key" prompt visible |
| Active kernel | linux2 (offset 0x780000) — Linux-3.16.0-karo, 3.8MB |
| Active rootfs | rootfs2 (ubi.mtd=rootfs2) |

**U-Boot cannot currently be interrupted** — Curb overrode `bootdelay` in the saved environment to prevent users from accessing the bootloader. The compiled-in default is 3 seconds with `CONFIG_ZERO_BOOTDELAY_CHECK`, but the NAND-stored environment takes precedence.

### Ethernet PHY
- PHY: SMSC LAN8710/LAN8720 (on eth0, connected to FEC MAC at 800f0000)
- eth0 gets no DHCP lease (3 discovers, no response — not connected to network)
- eth1: SPI-connected QCA7000 (powerline), gets DHCP lease <curb-ip>

### Configuration
- **Hub config**: `/data/hub-config.json` — loaded by load-controller.lua, revision `1624952529` (June 29, 2021). Updated via SIGUSR1 signal when streamer downloads new config from border server.
- **Hardware monitor**: `/etc/hm.conf`
- **Web server**: `/etc/lighttpd/lighttpd.conf`

### Cron Jobs
| Schedule | Command |
|----------|---------|
| Every minute | `/etc/wifi_cron.sh` |
| Every minute | `date +%m%d%H%M%Y > /data/sd/last_timestamp` |
| Every 5 minutes | `/usr/local/bin/curb_status.sh` (regenerates status page) |
| Every 4 minutes | `/usr/local/sbin/htpdate_restart.sh` |
| Every 10 minutes | `/usr/local/sbin/htpdate_monitor.sh` |
| Hourly | `/bin/run-parts /etc/cron.hourly` (includes firmware update check) |

## Network Configuration

### Interfaces
| Interface | Status | IP | MAC | Purpose |
|-----------|--------|-----|-----|---------|
| eth0 | DOWN | none | 00:0C:C6:84:72:B1 | Unused Ethernet (standard i.MX28 MAC) |
| eth1 | UP | <curb-ip>/24 | xx:xx:xx:xx:xx:xx | Active network (SPI-connected via QCA7000 powerline) |
| lo | UP | 127.0.0.1 | — | Loopback |

**Note:** eth1 uses an SPI-connected Ethernet interface (`eth1: SPI thread exit` in kernel logs), confirming the QCA7000 HomePlug AV chip connects to the i.MX28 via SPI bus.

### Network Isolation
The device is on subnet 192.168.1.x. Our machine (192.168.8.21) can only be reached by the device outbound (via router bridging), but we **cannot initiate connections to the device** from our subnet. The device's status page and SSH were accessed from a machine on the same 192.168.1.x subnet.

### Open Ports
| Port | Service | Auth |
|------|---------|------|
| 22/tcp | SSH (Dropbear) | Public key only (no password auth) |
| 80/tcp | HTTP (lighttpd) | None (static status page) |

### HomePlug AV (Powerline) Network
| Device | Role | MAC | Chip | Firmware |
|--------|------|-----|------|----------|
| Power Hub (this device) | STA (Station) | xx:xx:xx:xx:xx:xx | QCA7000 | MAC-QCA7000-1.1.0.730-04-20140815-CS |
| CT Sensor Hub | CCO (Coordinator) | C4:71:54:EF:31:59 | QCA7420 | MAC-QCA7420-1.3.0.2134-00-20151212-CS |

PLC Network Keys:
- DAK: `65:F6:D8:C0:9B:8F:9F:FA:44:F7:25:6A:F7:AF:3C:D9`
- NMK: `02:2A:C3:67:99:E1:38:99:0B:46:A1:2A:BF:2B:B2:0E`
- NID: `56:7E:7D:B8:DE:F3:0B`

## Cloud Infrastructure

| Server | DNS | IP | Purpose | Status |
|--------|-----|----|---------|--------|
| border.prod.energycurb.com | **Gone from public DNS** | Was AWS | API for samples + config | Dead. ELB returns 404 with correct SNI. User has local DNS pointing to 192.168.8.21. |
| updates.energycurb.com | Still resolves to AWS ELB | 52.41.16.86 / 44.240.63.18 | Firmware updates | **Alive.** nginx/1.11.3, expired cert (Mar 2019), still serves firmware files. |
| diagnostics.energycurb.com | 35.161.218.8 | 35.161.218.8 | Status reporting | **Partially alive.** Port 3000 accepts `POST /curb/status` (returns 200). SSH on 22 (OpenSSH 6.6.1p1, Ubuntu 14.04). |
| www.energycurb.com | 104.198.104.86 (GCP) | — | Website | Redirects to `poweredbyelevation.com/curb-energy-monitoring/` |
| app.energycurb.com | S3 bucket IPs | — | Web app | Connection timeout |
| admin.energycurb.com | S3 bucket IPs | — | Admin panel | Connection timeout |

### API Endpoints
- `POST https://border.prod.energycurb.com/v3/samples` — streamer posts energy samples (30 batched per request, **unauthenticated**)
- `GET https://border.prod.energycurb.com/...` — streamer downloads config revision every 5 minutes (revision 1624952529 = June 29, 2021)
- `GET https://border.prod.energycurb.com/health` — health check (from status script via wget)
- `POST http://diagnostics.energycurb.com:3000/curb/status` — device status reporting (**still alive, returns 200**)

### Streamer Behavior
- Posts 30 batched samples per request
- **Unauthenticated** — no API keys, bearer tokens, or auth headers found in any log
- Checks config revision every 5 minutes, signals load-controller via SIGUSR1 on change
- Config stored locally at `/data/hub-config.json`
- Restarts every ~10 minutes (managed by `hm` daemon)
- Uses curl with **SSL certificate verification** (rejects self-signed certs with `SSL_CACERT` error 60)
- Original server cert: `*.energycurb.com` (also covers `*.legacy.energycurb.com`) signed by Amazon CA (expired March 2019)

### Status Page (wget) vs Streamer (curl)
- `wget --no-check-certificate` — used by status script and update mechanism, **does not verify SSL**
- `curl` (LuaJIT libcurl) — used by streamer, **verifies SSL certificates**

## Firmware Update Mechanism

### Process
1. Cron triggers hourly update check
2. Downloads MD5 checksum files from `updates.energycurb.com/api/firmware/`
3. Compares with installed checksums
4. If different, downloads GPG-encrypted firmware archive
5. Decrypts with `gpg --decrypt --passphrase-file /root/.gnupg/passphrase --no-tty --ignore-time-conflict --no-mdc-warning`
6. Extracts tar.gz and runs `setup.sh`
7. Reboots

### Update URL Priority (tries in order)
1. `https://updates.energycurb.com/api/firmware/{serial}/update.tar.gz.gpg`
2. `http://updates.energycurb.com/api/firmware/{serial}/update.tar.gz.gpg` (**plain HTTP fallback — MITM-able**)
3. `https://updates.energycurb.com/api/firmware/update.tar.gz.gpg`

Uses `wget --no-check-certificate` (does NOT verify SSL).

### Encryption
- Algorithm: AES256 symmetric (GPG)
- S2K: SHA-1 with iteration count 65536 (very low — modern GPG uses millions)
- S2K salt (update): `D0EBBCE01F1CD084`
- S2K salt (os): `0B536A741F855670`
- GPG packet version: 4
- `--no-mdc-warning` flag used — indicates older encryption format without Modification Detection Code
- Passphrase: stored in `/root/.gnupg/passphrase` on device (unknown)
- Signing key: RSA key ID E13C6056, signer "Curb Sign"

### Downloaded Firmware Files
| File | Size | Status |
|------|------|--------|
| `update-generic.tar.gz.gpg` | 35KB | Downloaded, GPG AES256 encrypted, no plaintext leaks |
| `os-generic.tar.gz.gpg` | 23MB | Downloaded, GPG AES256 encrypted, no plaintext leaks |
| Serial-specific URLs | 169B each | **Fake** — nginx/1.11.3 404 HTML pages, not GPG data |

### Passphrase Cracking Attempts
- Custom Curb-specific wordlist (203 words): **Failed**
- cracklib-small dictionary (54K words): **Failed**
- american-english dictionary (104K words): **Failed**
- The passphrase is not a common English word or obvious company-related term

## SD Card Contents (`/data/sd`)

The SD card (8GB, ext4) was physically removed and mounted at `/mnt/` for analysis.

### SQLite Databases
| Database | Size | Rows | Autoincrement | Description |
|----------|------|------|---------------|-------------|
| `offline-queue.sqlite3` | 71MB | 52,343 | — | Full-resolution samples the streamer couldn't deliver |
| `aggregate-queue.sqlite3` | 129KB | 360 | 2,783,943 | Pre-aggregated samples (watt-only, for status page) |
| `sample-q-1-minute.sqlite3` | 16KB | 4 | 2,485,178 | 1-minute averaged sample queue |
| `sample-q-1-hour.sqlite3` | 77KB | — | — | Hourly averaged sample queue |
| `sample-q-5-minute.sqlite3` | 37KB | — | — | 5-minute averaged sample queue |

All databases use schema: `CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT, item BLOB)`

Items are JSON text stored as BLOBs. The high autoincrement values (~2.5M) indicate ~2.5 million samples have been processed through these queues over the device's lifetime.

**No configuration, registration, authentication, or non-energy data exists in any database.**

### Log Files (from `/data/sd/log/`, dated June 30, 2021)
- `streamer.log` / `.1` / `.2` — streamer POST activity
- `sampler.log` / `.1` / `.2` — sample queuing (only "Queuing sample" entries)
- `load-controller.log` / `.1` / `.2` — aggregated energy data with JSON, config loading events
- `messages` / `.0` — system syslog (cron, update process, SSH probe, shutdown sequence)
- `lighttpd-access.log` / `lighttpd-error.log` — web server logs (only periodic GET / from 192.168.1.1)
- `plc.log` — HomePlug AV / powerline communication logs (SG7000 update status)

### Log Analysis Highlights
- **SSH probe detected**: Connection from 192.168.1.1:56750 disconnected before auth ("Exit before auth: Exited normally")
- **No WiFi adapter**: `wlan0: error fetching interface information: Device not found`
- **Shutdown sequence**: linuxrc → /etc/init.d/rcK → dropbear killed → avahi exits → eth1 SPI thread exit
- **Config revision updates**: load-controller observed config update from revision 1624952094 to 1624952529 via SIGUSR1

## Energy Data Format

### Full Resolution Sample (from offline-queue)
12 circuits across 2 groups (split-phase 240V panel), sampled every second, aggregated to 60-second periods:

```json
{
  "t": 1774201980,         // Unix timestamp
  "p": 60,                 // Period (seconds) — also seen as 300 (5-min)
  "g": [                   // Groups (2 = Phase A, Phase B)
    {
      "t": 3.884,          // Apparent power total (kVA)
      "f": 59.993,         // Line frequency (Hz)
      "tg": 3.860,         // True power total (kW)
      "v": 123.08,         // Voltage (V RMS)
      "ts": 3.874,         // True power sum (kW)
      "c": [               // Circuits (6 per group)
        {
          "i": 11.706,     // Current (A RMS)
          "w": 0.3684,     // Real power (kWh over period)
          "var": -0.1101,  // Reactive power (kVARh)
          "p": 0.9597      // Power factor
        }
        // ... 5 more circuits
      ]
    }
    // ... Group 2 (same structure)
  ]
}
```

### Aggregated Sample (from aggregate-queue / status page load controller log)
Simplified format with only wattage per circuit:

```json
{
  "t": 1774202030,
  "g": [
    { "c": [{"w": 22.26}, {"w": 0.14}, {"w": 0.26}, {"w": -0.01}, {"w": -11.78}, {"w": 0.05}] },
    { "c": [{"w": 10.97}, ...] }
  ]
}
```

### Circuit Layout
- **Group 0**: 6 circuits on Phase A (circuits 0-5)
- **Group 1**: 6 circuits on Phase B (circuits 6-11)
- **Total**: 12 monitored circuits
- Circuit-to-breaker mapping requires manual identification (toggle breakers and observe readings)

## Access Attempts & Status

### What Works
- [x] HTTP status page scraping (live energy data every 60 seconds)
- [x] SD card direct access (physical removal, read/write)
- [x] Firmware download from update server (encrypted)
- [x] DNS redirection for border.prod.energycurb.com
- [x] HTTPS capture server receives health check from device (wget, no cert verify)

### What Doesn't Work
- [ ] SSH access (public key only, no known keys)
- [ ] GPG firmware decryption (passphrase unknown, dictionary attacks exhausted)
- [ ] Streamer interception (curl verifies SSL certs, rejects self-signed — error 60)
- [ ] Web server exploitation (static file serving only, no CGI, no path traversal, no query param injection)
- [ ] USB boot mode (USB-A port is wired as host, USBOTG_ID likely tied to GND)
- [ ] Cloud API (company infrastructure dead)
- [ ] APK analysis (download sources blocked/unavailable — returned Cloudflare challenges and 404s)
- [ ] Network-initiated connections to device (different subnet, no route from 192.168.8.x to 192.168.1.x)

### Serial Console — Current Status
- [x] J6 header soldered on carrier board
- [x] FTDI cable connected and working — full boot log captured
- [x] Boot output confirmed: U-Boot 2013.07, Linux 3.16.0-karo, BusyBox login prompt
- [ ] **BLOCKED: U-Boot boot delay overridden** — no "Hit any key" prompt, boots straight to Linux
- [ ] **BLOCKED: Linux root password unknown** — `curb-xxxxxxxx login:` prompt requires password
- [ ] Try holding spacebar during power-on (CONFIG_ZERO_BOOTDELAY_CHECK may catch it if bootdelay=0)
- [ ] If that fails: USB boot mode (TP30→GND), JTAG via J9, or root password brute-force

### Serial Brute-Force — Attempted Passwords (2026-03-24)
Automated brute-force via `curb_crack.py` over serial at ~12 attempts/min:
- **root user**: 255 device-derived passwords (hashes, MACs, serials, company words) — all failed
- **curb user**: 255 device-derived passwords — all failed
- **root user**: 250 additional passwords (Caesar shifts, version strings, firmware wordlist) — in progress
- **GPG passphrase**: 542 candidates tested via fast GPG decrypt (375/s) — all failed
- HomePlug AV DAK (`65:F6:D8:C0:9B:8F:9F:FA:44:F7:AF:3C:D9`) reverse-lookup attempted — no match
- Total unique passwords tried: ~760+ across both users
- No lockout observed (BusyBox getty has no rate limiting beyond ~3s failure delay)


### Files to Extract via Serial Console
| File | Purpose |
|------|---------|
| `/root/.gnupg/passphrase` | GPG passphrase — decrypt firmware archives |
| `/data/hub-config.json` | Device config, circuit labels, API endpoints, config revision |
| `/data/lamarr/*.lua` | All application source code (sampler, streamer, load-controller, etc.) |
| `/root/.ssh/authorized_keys` | SSH authorized keys (to add our own for remote access) |
| `/etc/shadow` | Check if root has a password |
| `/etc/hm.conf` | Hardware monitor configuration |
| `/etc/lighttpd/lighttpd.conf` | Web server configuration |
| `/etc/crontab` or `/var/spool/cron/` | Full cron schedule |
| `/etc/dropbear/` | SSH host keys and config |
| `/usr/local/bin/curb_*.sh` | Status, update, post-status scripts |
| `/etc/init.d/` | Init scripts (service startup order) |
| `/data/software_version` | Installed software version string |
| `/data/update.tar.gz.gpg.md5sum.installed` | Currently installed update checksum |
| `/data/os.tar.gz.gpg.md5sum.installed` | Currently installed OS checksum |

### Fallback — USB Boot Mode
U-Boot boot delay is overridden and Linux requires a root password. Next options:
1. **Hold spacebar during power-on** — may work if bootdelay=0 (not -1)
2. **USB boot mode**: Tie TP30 (D21_SW0) to GND (TP22), load fresh U-Boot via USB with `imx_usb_loader`. Requires soldering to SODIMM USB OTG pins since USB-A port is host-only.
3. **JTAG via J9**: Use OpenOCD to dump NAND directly. Needs 1.27mm pitch adapter.
4. **Root password brute-force**: At the serial login prompt. Reddit user u/ElfLogic mentioned root password may be related to serial number.

## Related Documents

## Community Resources

- [Curb-to-MQTT](https://github.com/pvanbaren/Curb-to-Mqtt) — Node.js script that scrapes the status page and publishes to MQTT with Home Assistant discovery
- [Curb-v2 GitHub Org](https://github.com/Curb-v2) — Official repos (third-party-app-integration, developer-docs)
- [CurbBridge](https://github.com/jhaines0/CurbBridge) — SmartThings integration (no longer working)
- [curb_energy Python library](https://github.com/ginoledesma/curb_energy) — Python API client (requires dead cloud)
- [Home Assistant thread](https://community.home-assistant.io/t/curb-energy-monitor-live-data-to-home-assistant/842428) — Community discussion on local data access
- [Powered By Elevation](https://poweredbyelevation.com/curb-energy-monitoring/) — Current brand owner

## Next Steps

1. **Serial console** — Connect FTDI cable to J6, get root shell at 115200 8N1
2. **Extract secrets** — Read GPG passphrase, SSH keys, Lua scripts, hub-config.json
3. **Add SSH key** — Write our public key to `/root/.ssh/authorized_keys` for persistent remote access
4. **Decrypt firmware** — Use passphrase to decrypt downloaded firmware archives
5. **Analyze Lua scripts** — Understand the full data pipeline (sampler → load-controller → streamer)
6. **Build local data pipeline** — Replace dead cloud with local collection (MQTT/InfluxDB/Grafana)
7. **Reconfigure streamer** — Modify config or replace streamer.lua to point at local API server
8. **Document protocol** — Full API documentation for `/v3/samples` endpoint format
