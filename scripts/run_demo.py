#!/usr/bin/env python3
"""Run one of the Avahi scale demo scenarios using Docker Engine on Linux."""

import argparse
import ipaddress
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "avahi-scale-demo:local"
DEFAULT_CONFIG = ROOT / "config" / "demo.env"


def command(args, *, capture=False, check=True):
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def read_env_file(path):
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise SystemExit(
            f"Config file not found: {path}\n"
            f"Copy {path.parent / 'demo.env.example'} to {path} and edit its LAN settings."
        )
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SystemExit(f"Invalid config entry at {path}:{line_number}; expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
            value = value[1:-1]
        values[key] = value
    return values


def env_bool(values, key, default=False):
    value = values.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off", ""):
        return False
    raise SystemExit(f"{key} must be true or false in config/demo.env")


def parse_args():
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    known, _ = bootstrap.parse_known_args()
    settings = read_env_file(known.config)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=("services", "clients"), default=settings.get("SCENARIO", "services"), help="override SCENARIO from config")
    parser.add_argument("--config", type=Path, default=known.config, help="config file (default: config/demo.env)")
    parser.add_argument("--scale", type=int, default=int(settings.get("SCALE", "10")), help="scale factor from 1 to 1000")
    parser.add_argument("--parent", default=settings.get("PARENT_INTERFACE"), help="wired parent interface connected to the Pi LAN")
    parser.add_argument("--subnet", default=settings.get("LAN_SUBNET"), help="LAN subnet in CIDR form")
    parser.add_argument("--gateway", default=settings.get("LAN_GATEWAY"), help="LAN gateway IPv4 address")
    parser.add_argument("--ip-range", default=settings.get("CONTAINER_IP_RANGE"), help="reserved Docker container IP range within the LAN subnet")
    parser.add_argument("--pi-ip", default=settings.get("PI_IP"), help="Raspberry Pi IPv4 address (used for report labeling)")
    parser.add_argument("--service-type", default=settings.get("SERVICE_TYPE", "_http._tcp"), help="DNS-SD service type")
    parser.add_argument("--timeout", type=int, default=int(settings.get("TIMEOUT", "90")), help="maximum discovery wait per client in seconds")
    parser.add_argument("--capture", action=argparse.BooleanOptionalAction, default=env_bool(settings, "CAPTURE"), help="capture UDP/5353 traffic with tcpdump")
    parser.add_argument("--results", type=Path, default=ROOT / settings.get("RESULTS_DIR", "results"), help="directory for JSON result files")
    args = parser.parse_args()
    if args.scenario not in ("services", "clients"):
        parser.error("SCENARIO in config/demo.env must be 'services' or 'clients'")
    missing = [name for name in ("parent", "subnet", "gateway", "ip_range", "pi_ip") if not getattr(args, name)]
    if missing:
        parser.error("missing LAN settings in config/demo.env: " + ", ".join(missing))
    return args


def validate(args):
    if platform.system() != "Linux":
        raise SystemExit("This demo requires Docker Engine on Linux; run it on the Ubuntu laptop.")
    if not 1 <= args.scale <= 1000:
        raise SystemExit("--scale must be between 1 and 1000")
    subnet = ipaddress.ip_network(args.subnet, strict=False)
    pool = ipaddress.ip_network(args.ip_range, strict=False)
    gateway = ipaddress.ip_address(args.gateway)
    pi = ipaddress.ip_address(args.pi_ip)
    if pool.version != 4 or subnet.version != 4 or gateway.version != 4 or pi.version != 4:
        raise SystemExit("This first demo version uses IPv4 only.")
    if not pool.subnet_of(subnet) or gateway not in subnet or pi not in subnet:
        raise SystemExit("--ip-range, --gateway, and --pi-ip must belong to --subnet")
    if pi in pool:
        raise SystemExit("--pi-ip must be outside --ip-range")
    clients = 1 if args.scenario == "services" else args.scale
    if pool.num_addresses - 2 < clients:
        raise SystemExit(f"--ip-range has too few usable addresses for {clients} client(s)")
    if args.timeout < 1:
        raise SystemExit("--timeout must be positive")
    return subnet, pool, clients


def make_network(name, args, subnet, pool):
    command([
        "docker", "network", "create", "--driver", "macvlan",
        "--subnet", str(subnet), "--gateway", args.gateway,
        "--ip-range", str(pool), "--opt", f"parent={args.parent}", name,
    ])


def build_image():
    command(["docker", "build", "--tag", IMAGE, str(ROOT)])


def start_capture(path, interface):
    if shutil.which("tcpdump") is None:
        raise SystemExit("--capture requires tcpdump installed on the Ubuntu host")
    proc = subprocess.Popen(
        ["sudo", "tcpdump", "-U", "-n", "-i", interface, "-w", str(path), "udp", "port", "5353"],
        stdout=subprocess.DEVNULL,
        stderr=None,
    )
    time.sleep(1)
    if proc.poll() is not None:
        raise RuntimeError("tcpdump exited before the test started")
    return proc


def stop_capture(proc):
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def wait_for_results(names, timeout):
    # Each worker exits on success or its own discovery deadline. Waiting in
    # parallel avoids a thousand serial Docker CLI polls at the target scale.
    with ThreadPoolExecutor(max_workers=min(64, max(1, len(names)))) as pool:
        futures = {pool.submit(subprocess.run, ["docker", "wait", name], text=True, capture_output=True): name for name in names}
        try:
            completed = set(as_completed(futures, timeout=timeout + 60))
        except TimeoutError:
            completed = {future for future in futures if future.done()}
        for future in completed:
            name = futures[future]
            try:
                future.result()
            except Exception:
                pass
            result = subprocess.run(["docker", "logs", name], text=True, capture_output=True)
            payload = None
            for line in result.stdout.splitlines():
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "discovered_services" in candidate:
                    payload = candidate
            if payload is None:
                payload = {"ok": False, "container": name, "reason": "container did not emit a result before timeout"}
            yield name, payload
        for future, name in futures.items():
            if not future.done():
                yield name, {"ok": False, "container": name, "reason": "container did not exit before harness timeout"}


def main():
    args = parse_args()
    subnet, pool, client_count = validate(args)
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("Docker CLI not found; install Docker Engine and ensure 'docker' works for your user.")
    args.results.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    network = "avahi-scale-" + uuid.uuid4().hex[:8]
    capture_path = args.results / f"{run_id}-{args.scenario}.pcap"
    names = []
    capture = None
    network_created = False
    payloads = []
    try:
        build_image()
        make_network(network, args, subnet, pool)
        network_created = True
        if args.capture:
            capture = start_capture(capture_path, args.parent)
        print(f"Running {args.scenario} scenario at scale {args.scale}; clients={client_count}.", flush=True)

        if args.scenario == "services":
            instances = [("services-0001", args.scale)]
        else:
            instances = [(f"client-{i:04d}", 1) for i in range(1, args.scale + 1)]

        run_commands = []
        for label, expected in instances:
            name = f"avahi-{run_id}-{label}".lower()
            names.append(name)
            run_commands.append([
                "docker", "run", "--detach", "--name", name,
                "--network", network,
                "--cap-add", "NET_RAW",
                "--env", f"SERVICE_TYPE={args.service_type}",
                "--env", f"EXPECTED_SERVICES={expected}",
                "--env", f"DISCOVERY_TIMEOUT={args.timeout}",
                IMAGE,
            ])
        # Start clients in parallel so a 1,000-client run does not time out
        # early instances while the harness is still creating later ones.
        with ThreadPoolExecutor(max_workers=min(64, max(1, len(run_commands)))) as pool:
            futures = [pool.submit(subprocess.run, cmd, check=True, stdout=subprocess.DEVNULL) for cmd in run_commands]
            for future in as_completed(futures):
                future.result()

        for name, result in wait_for_results(names, args.timeout):
            payloads.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)

        stop_capture(capture)
        capture = None
        times = [p["discovery_time_s"] for p in payloads if p.get("ok") and "discovery_time_s" in p]
        summary = {
            "run_id": run_id,
            "scenario": args.scenario,
            "scale": args.scale,
            "client_count": client_count,
            "advertised_service_count": args.scale if args.scenario == "services" else 1,
            "service_type": args.service_type,
            "pi_ip": args.pi_ip,
            "success_count": sum(bool(p.get("ok")) for p in payloads),
            "result_count": len(payloads),
            "discovery_time_s": {
                "min": min(times) if times else None,
                "median": statistics.median(times) if times else None,
                "max": max(times) if times else None,
            },
            "capture_file": str(capture_path) if args.capture else None,
            "results": payloads,
        }
        result_file = args.results / f"{run_id}-{args.scenario}.json"
        result_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Summary written to {result_file}")
        if args.capture:
            print(f"Capture written to {capture_path}; run scripts/analyze_capture.py on it for packet and byte counts.")
        return 0 if summary["success_count"] == summary["result_count"] and summary["result_count"] > 0 else 2
    finally:
        stop_capture(capture)
        for name in names:
            subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if network_created:
            subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Command failed ({exc.returncode}): {exc.cmd}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
