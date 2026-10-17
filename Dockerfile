FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends avahi-daemon avahi-utils python3 \
    && mkdir -p /run/avahi-daemon \
    && rm -rf /var/lib/apt/lists/*

COPY client/avahi-daemon.conf /etc/avahi/avahi-daemon.conf
COPY client/discover.py /opt/avahi-demo/discover.py
COPY client/entrypoint.sh /usr/local/bin/avahi-demo-entrypoint
RUN chmod 0755 /usr/local/bin/avahi-demo-entrypoint

ENTRYPOINT ["/usr/local/bin/avahi-demo-entrypoint"]
