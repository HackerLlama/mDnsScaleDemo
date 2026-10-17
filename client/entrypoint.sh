#!/bin/sh
set -eu

avahi-daemon --daemonize --no-chroot --no-drop-root --file=/etc/avahi/avahi-daemon.conf
python3 /opt/avahi-demo/discover.py
