# Avahi DNS-SD scale demo

This project compares two mDNS/DNS-SD workloads on one Ubuntu laptop, with a Raspberry Pi advertising the services:

1. **One device discovers N services:** one isolated Avahi client browses N service instances advertised by the Pi.
2. **N devices discover one service:** N isolated Avahi clients each browse one service instance advertised by the Pi.

`N` defaults to **10** and can be set from 1 through 1,000. Each client has its own Linux network namespace and LAN address. A Docker macvlan network lets its mDNS multicast packets reach the Pi on the local Ethernet segment.

## Requirements

- Ubuntu 26.04.1 LTS laptop with Docker Engine and Python 3.
- A **wired Ethernet** connection from the laptop to the same LAN/VLAN as the Raspberry Pi. Wi-Fi access points often do not pass traffic for the extra MAC addresses used by macvlan. A USB Ethernet adapter is a practical option.
- A Raspberry Pi on that LAN, running Avahi and able to advertise the demo records.
- A reserved, unused IPv4 address range on the LAN for the containers. Keep it outside the DHCP pool and away from static devices. For 1,000 clients, reserve at least 1,000 usable addresses; a dedicated `/22` test subnet/VLAN is a good fit. A typical `/24` cannot hold 1,000 client addresses.
- For packet capture: `tcpdump` and `tshark`. The capture command invokes `sudo tcpdump`.

The laptop's Docker macvlan containers appear as separate LAN devices. Confirm the LAN or switch permits that many addresses and multicast clients before increasing scale. Container addresses need not resolve in DNS; discovery is via mDNS.

## Prepare the Raspberry Pi

Copy `pi/generate_services.py` to the Pi, install Avahi if needed, and generate records. Choose the same service count as the laptop scenario:

```bash
cd mDnsScaleDemo
scp pi/generate_services.py pi@raspberrypi.local:~/generate_services.py
```

On the Pi, run:

```bash
sudo apt update
sudo apt install avahi-daemon python3
sudo systemctl enable --now avahi-daemon

# For one client discovering 10 services:
sudo python3 generate_services.py --install --clear --count 10 --service-type _http._tcp

# For 10 clients discovering one service:
sudo python3 generate_services.py --install --clear --count 1 --service-type _http._tcp
```

The helper creates static service groups named `Scale Demo 0001`, etc., with a `demo-index` TXT record, then restarts Avahi. They advertise port 8000; no application listener is required for this discovery-only test. `--clear` removes only files created by this helper. Avahi's own host service may also appear in the browse output, but the client counts only resolved instances of the requested service type and expects the requested number. Keep other `_http._tcp` advertisers off the test VLAN or use a dedicated service type for a clean run.

For example, to advertise a private test type instead, use `--service-type _scale._tcp` on the Pi and pass the same type to `run_demo.py`.

## Configure and run from Ubuntu

Copy `config/demo.env.example` to `config/demo.env` and edit it for your LAN. The example addresses are placeholders: set the interface, subnet, gateway, Pi address, and IP pool. The IP pool must be reserved and large enough for the selected client count. The runner reads this file directly; command-line flags are optional overrides.

Builds the client image on the first run. No Docker Hub image is required.

```bash
cd mDnsScaleDemo
cp config/demo.env.example config/demo.env
# Edit config/demo.env with the LAN settings and desired SCENARIO/SCALE.
python3 scripts/run_demo.py
```

Set `SCENARIO=services` for one client discovering `SCALE` services, or `SCENARIO=clients` for `SCALE` clients discovering one service. Before each run, configure the Pi with the matching count using `generate_services.py`. Increase `SCALE` from 10 to 100 and later 1,000 as the test LAN and Pi are ready. For the clients scenario, the IP range must have at least that many free addresses. The services scenario needs only one client IP, but the Pi needs to handle the advertised record count.

You can override individual settings without editing the file, for example `python3 scripts/run_demo.py clients --scale 100`, or choose another config with `--config path/to/demo.env`. `--capture` / `--no-capture` override the `CAPTURE` setting. If the local image is already built and Docker Hub is unavailable, use `python3 scripts/run_demo.py --skip-build`.

The script prints each client's JSON result and writes a JSON summary plus a standalone HTML report under `results/`. Containers start concurrently, each starts its Avahi daemon, and each signals readiness through a shared start gate. Once all clients are ready, they begin browsing together. This keeps container and daemon startup skew out of the discovery interval.

The report highlights **first-to-last discovery span** and plots a cumulative discovery curve. In `services` mode, each point is one unique Pi service instance resolved by the browser. In `clients` mode, each point is one client that resolved the service. The report also shows start-to-last time, success count, discovery event count, and median client completion time. JSON contains absolute discovery timestamps and per-event elapsed times for further analysis.

To include UDP/5353 packet and frame-byte counts, install capture tools and set `CAPTURE=true` in `config/demo.env` (or add `--capture`):

```bash
sudo apt install tcpdump tshark
python3 scripts/run_demo.py --capture
python3 scripts/analyze_capture.py results/<run-name>.pcap
```

Captures include UDP/5353 frames visible on the selected physical interface during the run. They include traffic from all devices on that interface that is visible to the laptop, not just demo packets; use a quiet, dedicated VLAN for clean comparisons. Counts include both queries and responses. Capture support is optional; discovery timing and per-client success do not require it.

## What to compare

For each scenario and scale, compare:

- successful clients and discovered instance counts;
- minimum, median, and maximum time to discovery;
- mDNS query and response packet counts and captured bytes;
- Raspberry Pi and laptop CPU/memory use during the run.

The two cases stress different work. A single browse for many service instances must receive and resolve many PTR/SRV/TXT/address records. Many clients create more independent browser state and multicast query sources. The mDNS protocol and Avahi can coalesce or suppress some traffic, so packet counts and timing must be measured on the target LAN rather than inferred as a simple multiplication by N.

## Limits and troubleshooting

- `macvlan` requires Linux and a parent NIC that can carry the additional source MAC addresses. Docker's macvlan network also isolates the host from its macvlan containers; this demo collects results through Docker logs, so host-to-container access is not needed.
- Start on wired Ethernet. If clients do not discover the Pi, check that the Pi and allocated container IPs are on the same VLAN/subnet, that UDP 5353 multicast is allowed, and that the IP pool is not in use. `tcpdump -ni <interface> udp port 5353` helps confirm packets reach the laptop NIC.
- The demo uses IPv4 only. Containers disable service publishing and only browse the configured service type.
- This measures discovery over one LAN segment. Docker Swarm overlays and ordinary NAT bridge networking are not appropriate for mDNS multicast on this test path.
- Raspberry Pi Avahi capacity and the LAN's multicast handling will likely become limiting before the laptop can run arbitrarily many clients. Increase scale gradually and observe both hosts.

## Files

- `scripts/run_demo.py`: creates the temporary macvlan network, synchronizes client start, collects timestamped JSON results, and cleans up containers/network.
- `scripts/render_report.py`: renders the cumulative discovery curve as a standalone HTML report.
- `client/`: minimal Avahi browser image and client config.
- `pi/generate_services.py`: creates the Pi's static DNS-SD service records.
- `scripts/analyze_capture.py`: summarizes packet and byte counts from an optional capture.
- `config/demo.env.example`: example LAN settings; copy it to the ignored local `config/demo.env` and edit it.
