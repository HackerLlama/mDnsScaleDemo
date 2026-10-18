#!/bin/sh
set -eu

mkdir -p /run/dbus /run/avahi-daemon
dbus-daemon --system --fork
avahi-daemon --daemonize --no-chroot --no-drop-root --file=/etc/avahi/avahi-daemon.conf

# Wait until Avahi has registered on the system bus before starting its client.
ready=0
attempt=0
while [ "$attempt" -lt 50 ]; do
    if avahi-browse --parsable --terminate _services._dns-sd._udp >/dev/null 2>&1; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
if [ "$ready" -ne 1 ]; then
    echo "Avahi did not become available on D-Bus" >&2
    exit 1
fi

python3 /opt/avahi-demo/discover.py
