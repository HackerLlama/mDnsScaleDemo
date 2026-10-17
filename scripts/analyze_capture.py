#!/usr/bin/env python3
"""Summarize captured mDNS packet and byte counts using tshark."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="pcap/pcapng file produced with run_demo.py --capture")
    args = parser.parse_args()
    tshark = shutil.which("tshark")
    if tshark is None:
        raise SystemExit("tshark not found; install Wireshark's command-line tools (tshark) on Ubuntu.")
    result = subprocess.run(
        [tshark, "-r", str(args.capture), "-Y", "udp.port == 5353", "-T", "fields", "-E", "separator=,", "-e", "frame.len", "-e", "dns.flags.response"],
        check=True,
        text=True,
        capture_output=True,
    )
    packets = queries = responses = bytes_total = 0
    non_dns_udp = 0
    for row in result.stdout.splitlines():
        fields = row.split(",")
        if not fields or not fields[0]:
            continue
        packets += 1
        bytes_total += int(fields[0])
        response_flag = fields[1] if len(fields) > 1 else ""
        if response_flag == "0":
            queries += 1
        elif response_flag == "1":
            responses += 1
        else:
            non_dns_udp += 1
    print(json.dumps({
        "capture": str(args.capture),
        "mdns_udp_packets": packets,
        "mdns_udp_frame_bytes": bytes_total,
        "dns_query_packets": queries,
        "dns_response_packets": responses,
        "non_dns_udp_5353_packets": non_dns_udp,
    }, indent=2))


if __name__ == "__main__":
    main()
