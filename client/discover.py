#!/usr/bin/env python3
"""Browse for DNS-SD instances and emit their discovery timestamps as JSON."""

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


service_type = os.environ["SERVICE_TYPE"]
expected = int(os.environ["EXPECTED_SERVICES"])
timeout_s = float(os.environ.get("DISCOVERY_TIMEOUT", "60"))
control_dir = Path(os.environ["CONTROL_DIR"])
control_id = os.environ["CONTROL_ID"]
ready_dir = control_dir / "ready"
ready_dir.mkdir(parents=True, exist_ok=True)
(ready_dir / control_id).touch()

# The host opens this gate only after all containers have Avahi ready. This
# gives the many-client case a common browse start and removes container boot
# skew from the first-to-last discovery interval.
start_file = control_dir / "START_AT"
startup_deadline = time.monotonic() + float(os.environ.get("STARTUP_TIMEOUT", "180"))
while not start_file.exists() and time.monotonic() < startup_deadline:
    time.sleep(0.05)
if not start_file.exists():
    print(json.dumps({
        "ok": False,
        "container": os.environ.get("HOSTNAME", "unknown"),
        "expected_services": expected,
        "discovered_services": 0,
        "reason": "host did not release the synchronized start gate",
        "instances": [],
    }), flush=True)
    sys.exit(2)

start_at = float(start_file.read_text(encoding="ascii").strip())
while time.time() < start_at:
    time.sleep(min(0.01, max(0.0, start_at - time.time())))

started_wall = time.time()
started = time.monotonic()
seen = {}
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
            proc.wait()
    instances = sorted(seen.values(), key=lambda item: item["discovered_at_unix_s"])
    first = instances[0] if instances else None
    last = instances[-1] if instances else None
    result = {
        "ok": bool(ok),
        "container": os.environ.get("HOSTNAME", "unknown"),
        "expected_services": expected,
        "discovered_services": len(instances),
        "discovery_time_s": round(elapsed, 6),
        "started_unix_s": round(started_wall, 6),
        "first_discovered_at_unix_s": first["discovered_at_unix_s"] if first else None,
        "last_discovered_at_unix_s": last["discovered_at_unix_s"] if last else None,
        "first_to_last_discovery_s": round(last["elapsed_s"] - first["elapsed_s"], 6) if first and last else None,
        "reason": reason,
        "instances": instances,
    }
    print(json.dumps(result, separators=(",", ":")), flush=True)
    sys.exit(0 if ok else 2)


deadline = started + timeout_s
while time.monotonic() < deadline:
    remaining = max(0.0, deadline - time.monotonic())
    ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.25))
    if not ready:
        if proc.poll() is not None:
            last_error = proc.stderr.read().decode("utf-8", errors="replace").strip()
            break
        continue
    line = proc.stdout.readline().decode("utf-8", errors="replace")
    if not line:
        if proc.poll() is not None:
            last_error = proc.stderr.read().decode("utf-8", errors="replace").strip()
            break
        continue
    fields = line.rstrip("\n").split(";")
    # Parsable resolved events contain interface, protocol, name, type, domain,
    # host, address, port, and TXT. Count each unique instance once.
    if len(fields) >= 9 and fields[0] == "=":
        key = (fields[3], fields[6], fields[7])
        if key not in seen:
            seen[key] = {
                "name": fields[3],
                "address": fields[6],
                "port": fields[7],
                "discovered_at_unix_s": round(time.time(), 6),
                "elapsed_s": round(time.monotonic() - started, 6),
            }
        if len(seen) >= expected:
            finish(True, "target count discovered")

reason = "deadline exceeded"
if last_error:
    reason = "avahi-browse exited: " + last_error
finish(False, reason)
