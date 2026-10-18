#!/usr/bin/env python3
"""Generate and optionally install Avahi static DNS-SD service instances."""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

PREFIX = "avahi-scale-demo-"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10, help="number of services (default: 10; max: 1000)")
    parser.add_argument("--service-type", default="_http._tcp", help="DNS-SD type, e.g. _http._tcp")
    parser.add_argument("--port", type=int, default=8000, help="advertised service port (default: 8000)")
    parser.add_argument("--directory", type=Path, default=Path("/etc/avahi/services"), help="Avahi service directory")
    parser.add_argument("--install", action="store_true", help="write files and restart avahi-daemon (requires root)")
    parser.add_argument("--clear", action="store_true", help="remove only this tool's generated files before writing")
    return parser.parse_args()


def main():
    args = parse_args()
    if not 1 <= args.count <= 1000:
        raise SystemExit("--count must be between 1 and 1000")
    if not re.fullmatch(r"_[A-Za-z0-9-]+\._(?:tcp|udp)", args.service_type):
        raise SystemExit("invalid service type; expected a form such as _http._tcp")
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")

    output = args.directory
    if args.install:
        if os.geteuid() != 0:
            raise SystemExit("--install requires root; run with sudo")
        output.mkdir(parents=True, exist_ok=True)
        if args.clear:
            for path in output.glob(PREFIX + "*.service"):
                path.unlink()

    for index in range(1, args.count + 1):
        name = f"Scale Demo {index:04d}"
        xml = (
            "<?xml version=\"1.0\" standalone='no'?>\n"
            "<!DOCTYPE service-group SYSTEM \"avahi-service.dtd\">\n"
            "<service-group>\n"
            f"  <name>{escape(name)}</name>\n"
            "  <service>\n"
            f"    <type>{escape(args.service_type)}</type>\n"
            f"    <port>{args.port}</port>\n"
            f"    <txt-record>demo-index={index:04d}</txt-record>\n"
            "  </service>\n"
            "</service-group>\n"
        )
        filename = f"{PREFIX}{index:04d}.service"
        if args.install:
            target = output / filename
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output, delete=False) as tmp:
                tmp.write(xml)
                tmp_path = Path(tmp.name)
            # Avahi drops root privileges before reading service definitions.
            # NamedTemporaryFile defaults to mode 0600, which would make the
            # generated files unreadable to the avahi user.
            tmp_path.chmod(0o644)
            tmp_path.replace(target)
        else:
            print(f"--- {filename} ---")
            sys.stdout.write(xml)

    if args.install:
        subprocess.run(["systemctl", "restart", "avahi-daemon"], check=True)
        print(f"Installed {args.count} service instance(s) in {output}; restarted avahi-daemon.")


if __name__ == "__main__":
    main()
