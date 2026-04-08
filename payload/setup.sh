#!/bin/sh
#
# Curb Energy Monitor — Community Root Access Payload
# 
# This script runs as root via the firmware update mechanism.
# It changes the root password and enables SSH password login.
#
# Default password: curb123
#
# To use a different password, generate a hash with:
#   openssl passwd -1 -salt curb 'yourpassword'
# and replace the NEW_HASH below.
#

NEW_HASH='$1$curb$8L5K9LOoKj7c8uLQEe/Wz0'

echo "=== Curb Community Root Access ==="
echo "Changing root and curb passwords..."

mount -o remount,rw /

# Change password hashes
sed -i "s|^root:[^:]*|root:${NEW_HASH}|" /etc/shadow
sed -i "s|^curb:[^:]*|curb:${NEW_HASH}|" /etc/shadow

# Enable SSH password authentication
# Dropbear uses -s flag to disable password auth
sed -i 's/DROPBEAR_ARGS=.*-s.*/DROPBEAR_ARGS="-R"/' /etc/default/dropbear

# Disable the update script so it doesn't overwrite our changes
# or keep hitting the fake update server
chmod -x /usr/local/sbin/update.sh

sync
mount -o remount,ro /

echo "=== Done! ==="
echo "Password: curb123"
echo "SSH: ssh -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<curb-ip>"
echo "Serial: 115200 8N1 on J6 header"
