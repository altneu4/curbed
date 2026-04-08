# The Journey: Reverse Engineering the Curb Energy Monitor

A chronicle of how we went from a bricked IoT device to full root access, including the dead ends, breakthroughs, and lessons learned.

## The Starting Point

In February 2026, Curb Inc. (later "Powered By Elevation") shut down their cloud services. Every Curb Energy Monitor — a device that reads whole-home energy data via CT clamps — became a brick. The hardware still worked, still sampled power data, still tried to phone home. But with the cloud gone, the data had nowhere to go.

We had:
- A Curb device with serial console access (J6 FTDI header, 115200 8N1)
- A login prompt we couldn't get past
- A boot process we couldn't interrupt

## Phase 1: Serial Login Brute Force (Days 1-3)

### The Attempt

The device presented a BusyBox login prompt. U-Boot's boot delay was overridden by Curb, so we couldn't interrupt the boot process. USB boot mode required access to SODIMM pins not broken out on the carrier board.

That left brute forcing the serial login.

We wrote `curb_crack.py` — a Python script that automated login attempts over serial at ~12 attempts/minute (limited by the 3-second failure delay).

### What We Tried

- **760 targeted passwords** — device identifiers, MAC addresses, serial numbers, company names, hash derivations, Caesar ciphers, base64 encodings
- **8,315 comprehensive passwords** — 4-character brute force patterns, common passwords, embedded system defaults, energy industry terms, Austin TX references, keyboard patterns
- Both `root` and `curb` usernames
- ~50 hours of continuous automated attempts

### What We Learned

The password was not guessable. Not a dictionary word, not derived from any visible device identifier, not a common embedded default. It was likely generated randomly during manufacturing.

**Lesson:** If a vendor uses per-device random passwords for embedded Linux, serial brute force is not viable at 12 attempts/minute.

### What Else We Explored

While the brute force ran, we investigated every other angle:

- **HomePlug AV DAK reverse-lookup** — The device's PLC (powerline) interface exposed a Device Access Key in the status page. We tried reversing it to find the network password. No match.
- **Web server exploitation** — Port 80 served a static status page via lighttpd. No CGI, no path traversal, no query parameter injection.
- **SSH (Dropbear)** — Running but unreachable from the Pi (different subnet, or firewall).
- **GPG passphrase cracking** — The encrypted firmware files used AES256 with only 65536 S2K iterations (very weak), but dictionary attacks with 170K+ words found nothing.
- **DNS redirect / MITM** — `border.prod.energycurb.com` already resolved to a local IP (`192.168.8.21`). A capture server had previously received a health check from the device. But the streamer used `curl` with SSL verification, rejecting our self-signed cert.

## Phase 2: The Dev Board (Day 14)

### The Idea

A Reddit user had mentioned the root password might be related to the serial number. We'd exhausted that avenue. The device used a Ka-Ro TX28 SODIMM module — what if we could access it from a development board?

We found a Ka-Ro TX28-SV30 Starterkit 5 on eBay for $250. It had:
- A SODIMM socket for the TX28 module
- Proper USB OTG (the Curb's USB was host-only)
- JTAG headers
- Boot mode jumpers

### USB Recovery Mode

Pulled the TX28 module from the Curb, inserted into the dev board, set the boot mode jumper to USB boot. Connected mini USB to the Pi.

```
Bus 001 Device 011: ID 15a2:004f Freescale Semiconductor, Inc. i.MX28 SystemOnChip in RecoveryMode
```

First try. The i.MX28 was in recovery mode, waiting for a boot image.

### Building U-Boot

The i.MX28 expects `.sb` (Secure Boot) format files. We needed to build U-Boot from the Ka-Ro fork (based on U-Boot 2013.07) and convert it to SB format.

**Problem:** GCC 12 (the version on our Pi 5) is not compatible with 2013-era U-Boot. Multiple issues:
- `extern inline` semantics changed — functions in `asm/io.h` caused multiple definition errors
- `alias` to `inline` functions prohibited — LED functions in `board.c`
- `-fno-common` default change — global variables in headers caused link errors
- `-Werror` in the Ka-Ro config turned every warning into a build failure

**Fixes:**
- `extern inline` → `static inline` in `arch/arm/include/asm/io.h`
- Removed `inline` from aliased functions in `arch/arm/lib/board.c` and `common/main.c`
- Added `-fcommon` to global CPPFLAGS
- Changed `-Werror` to `-Wno-error` in board config
- Created `compiler-gcc12.h` symlink
- Fixed `elftosb` linker flags (added `-lm`)
- Fixed hardcoded `/usr/include/sys/types.h` path for `elftosb`

After all that, `u-boot.sb` built successfully.

### First Boot

Sent U-Boot via UUU:
```
sudo uuu u-boot.sb
```

Serial output:
```
U-Boot 2013.01 ...
CPU:   Freescale i.MX28 rev1.2 at 454 MHz
NAND:  128 MiB
```

But then it hung. The device showed "Using default environment" and stopped.

**Problem:** The U-Boot build included LCD support. The dev board doesn't have the same LCD hardware as the Curb carrier board. The `stdio_init()` call hung trying to initialize the LCD as a console device.

**Fix:** Disabled `CONFIG_LCD` in the config. U-Boot booted to a prompt.

### The NAND Dump

With a U-Boot shell, we could read NAND. But we couldn't mount the UBIFS filesystem because the U-Boot build didn't include UBI commands.

We tried building with `CONFIG_CMD_UBI` and `CONFIG_CMD_UBIFS` enabled, but the larger binary caused U-Boot to hang during initialization (likely memory layout issues with only 64MB RAM).

**Solution:** Dump the raw NAND over serial using `md.b` (hex dump), then parse the UBI/UBIFS image on the Pi with `ubidump`.

**Problem:** 32MB of NAND via hex dump over 115200 baud serial = ~4 hours of transfer.

**Problem within the problem:** The `md.b` command buffered megabytes of hex text in U-Boot's serial output queue. When we tried to send new commands, they'd get lost in the flood. Ctrl+C was needed to interrupt the output.

After 4 hours of transfer, we had `rootfs2.bin` — the 32MB root filesystem partition.

### Extracting Secrets

```bash
ubidump --cat etc/shadow rootfs2.bin
```

```
root:$1$rpBjPVks$cOeVNIXQKqd6UfnaR8eRz/:10933:0:99999:7:::
curb:$1$/uzZhtuK$ZjwGgm.w0i0vl0fiBpFhq/:::::::
```

**The password hashes.** MD5crypt. And:

```bash
ubidump --cat root/.gnupg/passphrase rootfs2.bin
```

```
uf0ZZkz6n@*k$XeoMm@cA%jn!Cp9M0d8
```

**The GPG passphrase.** In plaintext. Universal across all devices.

### Decrypting the Firmware

```bash
gpg --passphrase 'uf0ZZkz6n@*k$XeoMm@cA%jn!Cp9M0d8' --decrypt update-generic.tar.gz.gpg | tar tzf -
```

All the Lua application source code — sampler, streamer, load-controller, config, everything.

## Phase 3: Cracking the Password Hash (Day 14)

We threw the MD5crypt hashes at a Xeon server with an RTX 3080 + RTX 4090:
- rockyou.txt (14M words): nothing
- rockyou + best64 rules (2.2B combinations): nothing  
- Brute force lowercase+digits 1-8 chars: running (days)

The password was strong enough to resist GPU cracking. But we didn't need it anymore.

## Phase 4: The DNS Redirect Attack (Day 14)

### The Discovery

Reading the `update.sh` script extracted from the NAND dump:

```bash
WGET_OPTS="--no-check-certificate -q"
```

No certificate verification. Falls back to HTTP. The only "security" was GPG encryption — and we had the passphrase.

### The Attack

1. Created a `setup.sh` that changes the root password and enables SSH
2. Encrypted it with the universal GPG passphrase
3. Set up a Python HTTPS/HTTP server on the network
4. Pointed `updates.energycurb.com` DNS to our server (via Pi-hole)
5. Rebooted the Curb

### The Complication

The update script checks for OS updates first, then software updates. If the OS check fails, `set -e` kills the script before reaching our software payload.

**First attempt:** Served a dummy OS checksum. The device saw a "new" OS version, tried to download the OS image (which we didn't have), failed, and the script died.

**Second attempt:** Served the real OS checksum from our downloaded firmware. But it didn't match what was installed on the device (different version), so same problem.

**Third attempt:** Created a complete dummy OS update — a GPG-encrypted tar.gz containing a harmless `update-os.sh` and valid `files.md5sum`. The device downloaded it, decrypted it, ran the harmless OS script, and rebooted. On the second boot, the OS checksum now matched (we served the same one), so it skipped to the software update — our password change payload.

**Two reboots to root.**

### The Result

```
curb-xxxxxxxx login: root
Password: curb123
# id
uid=0(root) gid=0(root) groups=0(root),10(wheel)
```

## Phase 5: SSH Access (Day 14)

SSH was running (Dropbear) but had two issues:

1. **Password auth disabled** — The `-s` flag in `/etc/default/dropbear` disabled password login. Our payload fixed this, but we initially edited the wrong file (`/etc/init.d/S50dropbear` instead of `/etc/default/dropbear`).

2. **Legacy RSA only** — The old Dropbear only supports `ssh-rsa` (SHA-1 signatures). Modern OpenSSH disables this by default. Fix: `ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa`.

3. **Key location** — We tried putting authorized keys in `/etc/dropbear/authorized_keys` and `/root/.ssh/authorized_keys`. Both paths needed to exist with correct permissions.

## Key Takeaways

### For the Community

1. **The GPG passphrase is universal.** Every Curb device uses the same passphrase for firmware encryption. This means the DNS redirect attack works on any Curb device.

2. **No special hardware needed.** The DNS redirect method requires only a computer on the same network and a DNS override.

3. **The device is still useful.** It's still sampling energy data. You just need to redirect it to a local server.

### For IoT Security

1. **`wget --no-check-certificate`** made the update mechanism trivially MITM-able. The GPG encryption was the only protection, and a shared symmetric passphrase is no protection at all.

2. **Shared secrets across all devices** — The GPG passphrase, the signing key, and the update mechanism were identical on every unit. Compromising one compromises all.

3. **The root password was strong** — MD5crypt with a random password that resisted GPU cracking. But it didn't matter because the update mechanism was wide open.

4. **Plaintext secrets on flash** — The GPG passphrase was stored as a plain file. The EEPROM contained the API secret in a JSON blob. Neither was encrypted at rest.

### Technical Stats

| Metric | Value |
|--------|-------|
| Serial brute force attempts | 9,075+ |
| Serial brute force duration | ~50 hours |
| Dev board cost | $250 |
| NAND dump (serial) | 4 hours |
| NAND dump (SSH) | 7 minutes |
| Time from dev board arrival to root | ~12 hours |
| Time from DNS redirect to root | ~5 minutes |
| GPU cracking attempts | billions (ongoing, no result) |
| Total project duration | ~2 weeks |
