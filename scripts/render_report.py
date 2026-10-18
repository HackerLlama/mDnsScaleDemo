#!/usr/bin/env python3
"""Render a standalone HTML cumulative discovery curve from a run JSON file."""

import html
import json
import sys
from pathlib import Path


def fmt_seconds(value):
    return f"{value:.3f} s" if value is not None else "—"


def render(summary):
    events = summary.get("discovery_events", [])
    events = sorted(events, key=lambda event: event.get("discovered_at_unix_s") or 0)
    expected = max(1, int(summary.get("expected_discovery_events", 1)))
    first = summary.get("first_discovery_unix_s")
    last = summary.get("last_discovery_unix_s")
    span = summary.get("first_to_last_discovery_s")
    x_max = max(1.0, (span or 0.0) * 1.08)
    width, height = 900, 480
    left, right, top, bottom = 82, 30, 32, 76
    plot_w = width - left - right
    plot_h = height - top - bottom
    x = lambda seconds: left + (seconds / x_max) * plot_w
    y = lambda count: top + plot_h - (count / expected) * plot_h

    parts = []
    for tick in range(6):
        seconds = x_max * tick / 5
        xpos = x(seconds)
        parts.append(f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" y2="{top + plot_h}" class="grid"/>')
        parts.append(f'<text x="{xpos:.1f}" y="{top + plot_h + 25}" class="tick" text-anchor="middle">{seconds:.2f}</text>')
    for tick in range(6):
        count = round(expected * tick / 5)
        ypos = y(count)
        parts.append(f'<line x1="{left}" y1="{ypos:.1f}" x2="{left + plot_w}" y2="{ypos:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 12}" y="{ypos + 4:.1f}" class="tick" text-anchor="end">{count}</text>')
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" class="axis"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="axis"/>')

    if events and first is not None:
        current = 0
        points = [(x(0), y(0))]
        index = 0
        while index < len(events):
            event_time = events[index].get("discovered_at_unix_s") or first
            relative = max(0.0, event_time - first)
            group_count = 0
            while index < len(events) and events[index].get("discovered_at_unix_s") == event_time:
                group_count += 1
                index += 1
            points.append((x(relative), y(current)))
            current += group_count
            points.append((x(relative), y(current)))
        points.append((left + plot_w, y(current)))
        point_text = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        parts.append(f'<polyline points="{point_text}" class="curve"/>')
        parts.append(f'<circle cx="{x(0):.1f}" cy="{y(1):.1f}" r="5" class="event"/>')
        parts.append(f'<circle cx="{x(span or 0):.1f}" cy="{y(len(events)):.1f}" r="5" class="event"/>')
    else:
        parts.append(f'<text x="{left + plot_w / 2:.1f}" y="{top + plot_h / 2:.1f}" class="empty" text-anchor="middle">No discovery events recorded</text>')

    parts.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 18}" class="label" text-anchor="middle">Seconds from first discovery</text>')
    parts.append(f'<text x="20" y="{top + plot_h / 2:.1f}" class="label" text-anchor="middle" transform="rotate(-90 20 {top + plot_h / 2:.1f})">Cumulative {"services" if summary.get("scenario") == "services" else "clients"} discovered</text>')
    svg = f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative discovery curve">{"".join(parts)}</svg>'

    scenario = html.escape(str(summary.get("scenario", "unknown")))
    scale = html.escape(str(summary.get("scale", "?")))
    success = f'{summary.get("success_count", 0)} / {summary.get("result_count", 0)}'
    events_count = f'{summary.get("discovery_event_count", len(events))} / {summary.get("expected_discovery_events", "?")}'
    median = summary.get("discovery_time_s", {}).get("median")
    capture = summary.get("capture_file")
    capture_card = f'<div class="card"><span>Packet capture</span><strong>{html.escape(str(capture))}</strong></div>' if capture else ""
    traffic = summary.get("traffic") or {}
    traffic_cards = ""
    if traffic:
        traffic_cards = (
            f'<div class="card"><span>mDNS packets</span><strong>{traffic.get("mdns_udp_packets", 0):,}</strong></div>'
            f'<div class="card"><span>mDNS frame bytes</span><strong>{traffic.get("mdns_udp_frame_bytes", 0):,}</strong></div>'
            f'<div class="card"><span>Queries / responses</span><strong>{traffic.get("dns_query_packets", 0):,} / {traffic.get("dns_response_packets", 0):,}</strong></div>'
        )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>mDNS discovery report — {scenario}</title>
<style>
body{{font:16px system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:1100px;color:#182230;background:#f5f7fa}}
h1{{margin-bottom:.25rem}} .sub{{color:#586779;margin-top:0}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.75rem;margin:1.4rem 0}}
.card{{background:#fff;border:1px solid #dce3eb;border-radius:10px;padding:1rem}} .card span{{display:block;color:#586779;font-size:.85rem;margin-bottom:.35rem}}
.card strong{{font-size:1.35rem;overflow-wrap:anywhere}} .primary{{border-color:#168b8a;background:#e9f7f5}} .chart{{background:#fff;border:1px solid #dce3eb;border-radius:10px;padding:1rem}}
svg{{width:100%;height:auto}} .grid{{stroke:#e6ebf0;stroke-width:1}} .axis{{stroke:#64748b;stroke-width:1.4}} .tick{{fill:#526173;font-size:13px}}
.label{{fill:#263546;font-size:14px}} .curve{{fill:none;stroke:#087f8c;stroke-width:4;stroke-linejoin:round;stroke-linecap:round}}
.event{{fill:#e76f51;stroke:white;stroke-width:2}} .empty{{fill:#64748b;font-size:16px}}
.note{{color:#586779;font-size:.9rem}} code{{background:#e9eef4;padding:.1rem .3rem;border-radius:4px}}
</style></head><body>
<h1>mDNS discovery report</h1><p class="sub">Scenario: <b>{scenario}</b> · Scale: <b>{scale}</b> · Service type: <code>{html.escape(str(summary.get("service_type", "")))}</code></p>
<section class="cards">
<div class="card primary"><span>First → last discovery</span><strong>{fmt_seconds(span)}</strong></div>
<div class="card"><span>Start → last discovery</span><strong>{fmt_seconds(summary.get("start_to_last_discovery_s"))}</strong></div>
<div class="card"><span>Successful clients</span><strong>{success}</strong></div>
<div class="card"><span>Discovery events</span><strong>{events_count}</strong></div>
<div class="card"><span>Median client completion</span><strong>{fmt_seconds(median)}</strong></div>
{capture_card}
{traffic_cards}
</section>
<section class="chart"><h2>Cumulative discovery</h2>{svg}<p class="note">The highlighted span runs from the first resolved service/client event to the last. Each event is counted once per service instance or client, according to the scenario.</p></section>
<p class="note">Raw data: <code>{html.escape(str(summary.get("run_id", "")))}.json</code>. Times use a synchronized start gate after all Avahi clients report ready.</p>
</body></html>'''


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: render_report.py RUN.json OUTPUT.html")
    source, destination = map(Path, sys.argv[1:])
    summary = json.loads(source.read_text(encoding="utf-8"))
    destination.write_text(render(summary), encoding="utf-8")


if __name__ == "__main__":
    main()
