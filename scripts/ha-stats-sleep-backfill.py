#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets"]
# ///
"""Backfill Oura-only sleep metrics from Home Assistant long-term statistics.

Sleep score and breathing disturbance index have no Apple Health equivalent, so
the export-based importer cannot produce them — but HA's recorder keeps hourly
long-term statistics forever for any sensor with a state_class, which covers the
ring's whole history.

Reads HA's `recorder/statistics_during_period` over the WebSocket API (statistics
are not exposed over REST) and writes one sample per night into the same
VictoriaMetrics instance and metric names the live HA feed uses.

    HA_TOKEN=$(kubectl get secret -n ai ha-mcp-secret \
        -o jsonpath='{.data.HOMEASSISTANT_TOKEN}' | base64 -d) \
      ./scripts/ha-stats-sleep-backfill.py --ha http://10.40.0.16:8123 \
        --vm http://localhost:8428
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import websockets

# HA statistic_id -> VictoriaMetrics metric name. Both are Oura-only.
METRICS = {
    "sensor.oura_ring_sleep_score": "health_sleep_score",
    "sensor.oura_ring_breathing_disturbance_index": "health_sleep_breathing_disturbance_index",
}
TZ = ZoneInfo("Europe/Belgrade")


async def fetch_statistics(ha_url: str, token: str, start: datetime, end: datetime) -> dict:
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            sys.exit(f"unexpected greeting from HA: {hello}")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            sys.exit(f"auth failed: {auth}")

        await ws.send(json.dumps({
            "id": 1,
            "type": "recorder/statistics_during_period",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "statistic_ids": list(METRICS),
            # Hourly, not daily: a daily bucket spans two nights, because the
            # sensor still holds the previous night's value until the ring syncs
            # in the morning. min/mean/max over that are all meaningless. The
            # last hour of the day is the settled value for that night.
            "period": "hour",
            "types": ["mean"],
        }))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                if not msg.get("success", False):
                    sys.exit(f"statistics query failed: {msg.get('error')}")
                return msg["result"]


def per_night(rows: list[dict]) -> dict[str, float]:
    """Collapse hourly rows to one settled value per local calendar day."""
    by_day: dict[str, tuple[int, float]] = {}
    for row in rows:
        value = row.get("mean")
        if value is None:
            continue
        start_ms = row["start"] if isinstance(row["start"], int) else int(row["start"])
        local = datetime.fromtimestamp(start_ms / 1000, TZ)
        day = local.date().isoformat()
        # keep the latest hour seen for that day
        if day not in by_day or start_ms > by_day[day][0]:
            by_day[day] = (start_ms, float(value))
    return {day: value for day, (_, value) in by_day.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ha", required=True, help="Home Assistant base URL")
    ap.add_argument("--vm", required=True, help="VictoriaMetrics base URL")
    ap.add_argument("--days", type=int, default=400, help="how far back to look")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("HA_TOKEN")
    if not token:
        sys.exit("HA_TOKEN is not set")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    result = asyncio.run(fetch_statistics(args.ha, token, start, end))

    series = []
    for statistic_id, metric in METRICS.items():
        nights = per_night(result.get(statistic_id, []))
        if not nights:
            print(f"  {metric:<44} no statistics returned", file=sys.stderr)
            continue
        days = sorted(nights)
        values, timestamps = [], []
        for day in days:
            # Local noon, matching the Apple Health importer's per-night stamp.
            noon = datetime.fromisoformat(day).replace(hour=12, tzinfo=TZ)
            values.append(round(nights[day], 4))
            timestamps.append(int(noon.timestamp() * 1000))
        series.append({
            "metric": {"__name__": metric, "source": "Oura"},
            "values": values,
            "timestamps": timestamps,
        })
        print(f"  {metric:<44} {len(days):>3} nights  {days[0]} -> {days[-1]}"
              f"  last={values[-1]}", file=sys.stderr)

    if not series:
        sys.exit("nothing to import")

    body = ("\n".join(json.dumps(s) for s in series) + "\n").encode()
    if args.dry_run:
        print(body.decode())
        return
    req = urllib.request.Request(
        args.vm.rstrip("/") + "/api/v1/import",
        data=body,
        headers={"Content-Type": "application/x-ndjson"},
    )
    with urllib.request.urlopen(req) as resp:
        print(f"POST {args.vm} -> {resp.status}")


if __name__ == "__main__":
    main()
