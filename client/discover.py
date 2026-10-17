#!/usr/bin/env python3
"""Run one DNS-SD browse and emit a single JSON result line to stdout."""

import json
import os
import signal
import subprocess
import sys
import time


service_type = os.environ["SERVICE_TYPE"]
expected = int(os.environ["EXPECTED_SERVICES"])
timeout_s = float(os.environ.get("DISCOVERY_TIMEOUT", "60"))
started_wall = time.time()
started = time.monotonic()
seen = set()
last_error = None

proc = subprocess.Popen(
    ["avahi-browse", "--resolve", "--parsable", service_type],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0,
)

def finish(ok, reason):
    elapsed = time.monotonic() - started
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    result = {
        "ok": bool(ok),
        "container": os.environ.get("HOSTNAME", "unknown"),
        "expected_services": expected,
        "discovered_services": len(seen),
        "discovery_time_s": round(elapsed, 6),
        "started_unix_s": round(started_wall, 6),
        "reason": reason,
        "instances": sorted(seen),
    }
    print(json.dumps(result, separators=(",", ":")), flush=True)
    sys.exit(0 if ok else 2)


deadline = started + timeout_s
while time.monotonic() < deadline:
    remaining = max(0.0, deadline - time.monotonic())
    # readline is blocking, so poll the child in a helper-free loop with a
    # short select timeout to retain a reliable overall deadline.
    import select

    ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.25))
    if not ready:
        if proc.poll() is not None:
            last_error = proc.stderr.read().strip()
            break
        continue
    line = proc.stdout.readline().decode("utf-8", errors="replace")
    if not line:
        if proc.poll() is not None:
            last_error = proc.stderr.read().strip()
            break
        continue
    fields = line.rstrip("\n").split(";")
    # avahi-browse's parsable resolved event begins with '=' and contains
    # interface, protocol, name, type, domain, host, address, port, TXT.
    if len(fields) >= 9 and fields[0] == "=":
        seen.add((fields[3], fields[6], fields[7]))
        if len(seen) >= expected:
            finish(True, "target count discovered")

reason = "deadline exceeded"
if last_error:
    reason = "avahi-browse exited: " + last_error
finish(False, reason)
