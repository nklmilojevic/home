#!/usr/bin/env python3
"""Parse Apple Health export.zip / export.xml sleep data.

Stats mode (default) reports what's in the export without touching anything.
--out writes VictoriaMetrics /api/v1/import JSON-lines; --url POSTs them.

Stdlib only. Usage:
    ./apple_health_sleep.py ~/Downloads/export.zip
    ./apple_health_sleep.py ~/Downloads/export.zip --out /tmp/sleep.jsonl
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"

# HKCategoryValueSleepAnalysis* suffix -> our stage name.
# "Asleep" (no suffix) is the pre-watchOS-9 undifferentiated value; anything
# tracked before Sept 2022 (or by a source that doesn't do staging) lands there.
STAGES = {
    "InBed": "in_bed",
    "Asleep": "asleep_unspecified",
    "AsleepUnspecified": "asleep_unspecified",
    "AsleepCore": "core",
    "AsleepDeep": "deep",
    "AsleepREM": "rem",
    "Awake": "awake",
}
ASLEEP_STAGES = ("core", "deep", "rem", "asleep_unspecified")

# Nightly quantity types worth carrying alongside the stages.
QUANTITIES = {
    "HKQuantityTypeIdentifierHeartRate": "heart_rate_bpm",
    # Apple reports SDNN, Oura (via the HA feed) reports rMSSD. Same concept,
    # different algorithm, so the metric name stays source-neutral.
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv_ms",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate_bpm",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate_bpm",
    "HKQuantityTypeIdentifierOxygenSaturation": "oxygen_saturation_percent",
    "HKQuantityTypeIdentifierAppleSleepingWristTemperature": "wrist_temperature_celsius",
}

# A gap longer than this between segments starts a new sleep session.
SESSION_GAP = timedelta(minutes=60)


def parse_ts(raw: str) -> datetime:
    # "2026-07-29 23:34:11 +0200"
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")


def night_of(start: datetime) -> str:
    """Apple's noon-to-noon sleep day: a session belongs to its wake-up date."""
    return (start + timedelta(hours=12)).date().isoformat()


def open_xml(path: str) -> io.BufferedIOBase:
    if path.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        name = next(
            (n for n in zf.namelist() if n.endswith("export.xml") and "cda" not in n.lower()),
            None,
        )
        if name is None:
            sys.exit(f"no export.xml inside {path} (found: {zf.namelist()[:5]})")
        return zf.open(name)
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def iter_records(path: str, types: set[str]):
    """Stream <Record> elements of the given types, keeping memory flat."""
    handle = open_xml(path)
    try:
        context = ET.iterparse(handle, events=("start", "end"))
        _, root = next(context)
        seen = 0
        for event, elem in context:
            if event != "end" or elem.tag != "Record":
                continue
            if elem.get("type") in types:
                yield elem
            elem.clear()
            seen += 1
            if seen % 20000 == 0:
                root.clear()
    finally:
        handle.close()


# Stage precedence when two segments start at the same instant. A specific
# asleep stage beats an unspecified one, and any asleep stage beats Awake.
PRIORITY = {"deep": 4, "rem": 3, "core": 2, "asleep_unspecified": 1, "awake": 0}


def flatten(segments: list[tuple[datetime, datetime, str]]) -> dict[str, float]:
    """Collapse possibly-overlapping stage segments into non-overlapping totals.

    Oura writes some nights twice, offset by ~29s (two interleaved copies of the
    same hypnogram), so naively summing segment durations inflates a night to
    ~1.8x its real length. Sweeping the timeline and letting the most recently
    started segment own each instant collapses duplicate coverage, and guarantees
    sum(stages) <= session span.
    """
    import heapq

    if not segments:
        return {}
    points = sorted({t for start, end, _ in segments for t in (start, end)})
    ordered = sorted(segments, key=lambda s: s[0])
    totals: dict[str, float] = defaultdict(float)
    active: list[tuple[float, int, datetime, str]] = []
    idx = 0

    for a, b in zip(points, points[1:]):
        while idx < len(ordered) and ordered[idx][0] <= a:
            start, end, stage = ordered[idx]
            heapq.heappush(active, (-start.timestamp(), -PRIORITY[stage], end, stage))
            idx += 1
        while active and active[0][2] <= a:
            heapq.heappop(active)
        if active:
            totals[active[0][3]] += (b - a).total_seconds() / 3600
    return totals


def merge_spans(spans: list[tuple[datetime, datetime]]) -> float:
    """Total hours covered by the union of possibly-overlapping intervals."""
    total = 0.0
    current_start = current_end = None
    for start, end in sorted(spans):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds() / 3600
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += (current_end - current_start).total_seconds() / 3600
    return total


class Session:
    __slots__ = ("source", "start", "end", "_sleep", "_bed", "_stages")

    def __init__(self, source: str, start: datetime):
        self.source = source
        self.start = start
        self.end = start
        self._sleep: list[tuple[datetime, datetime, str]] = []
        self._bed: list[tuple[datetime, datetime]] = []
        self._stages: dict[str, float] | None = None

    def add(self, stage: str, start: datetime, end: datetime) -> None:
        self.end = max(self.end, end)
        if stage == "in_bed":
            self._bed.append((start, end))
        else:
            self._sleep.append((start, end, stage))
            self.start = min(self.start, start)

    @property
    def stages(self) -> dict[str, float]:
        if self._stages is None:
            self._stages = defaultdict(float, flatten(self._sleep))
        return self._stages

    @property
    def asleep(self) -> float:
        return sum(self.stages[s] for s in ASLEEP_STAGES)

    @property
    def in_bed(self) -> float:
        return merge_spans(self._bed) or (self.end - self.start).total_seconds() / 3600

    @property
    def in_bed_start(self) -> datetime | None:
        return min((s for s, _ in self._bed), default=None)

    @property
    def has_stages(self) -> bool:
        return any(self.stages[s] for s in ("core", "deep", "rem"))


def collect_sessions(path: str) -> list[Session]:
    """Group raw sleep segments into per-source sessions."""
    per_source: dict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for elem in iter_records(path, {SLEEP_TYPE}):
        value = (elem.get("value") or "").replace("HKCategoryValueSleepAnalysis", "")
        stage = STAGES.get(value)
        if stage is None:
            continue
        # Apple writes "Apple Watch" with a non-breaking space, which makes any
        # hand-written {source="...Apple Watch"} selector silently match nothing.
        source = (elem.get("sourceName") or "unknown").replace(" ", " ")
        per_source[source].append(
            (parse_ts(elem.get("startDate")), parse_ts(elem.get("endDate")), stage)
        )

    sessions: list[Session] = []
    for source, segments in per_source.items():
        segments.sort(key=lambda s: s[0])
        current: Session | None = None
        for start, end, stage in segments:
            if current is None or start - current.end > SESSION_GAP:
                current = Session(source, start)
                sessions.append(current)
            current.add(stage, start, end)

    # Some sources (old iOS "Clock" bedtime, Ominous) only ever write InBed
    # intervals — no asleep segments at all, so they'd contribute 0h nights.
    dropped = {s.source for s in sessions if s.asleep == 0} - {s.source for s in sessions if s.asleep}
    if dropped:
        print(f"note: ignoring InBed-only source(s): {', '.join(sorted(dropped))}", file=sys.stderr)
    return [s for s in sessions if s.asleep > 0]


def collect_quantities(path: str, windows: list[tuple[datetime, datetime, str, str]]):
    """Average each quantity type over each (main) sleep session window.

    windows: (start, end, night, source) for main sessions only.
    Returns {(night, source, metric): mean_value} plus min heart rate.
    """
    windows.sort(key=lambda w: w[0])
    starts = [w[0] for w in windows]
    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    daily: dict[tuple[str, str], float] = {}

    import bisect

    for elem in iter_records(path, set(QUANTITIES)):
        metric = QUANTITIES[elem.get("type")]
        try:
            value = float(elem.get("value"))
        except (TypeError, ValueError):
            continue
        if metric == "oxygen_saturation_percent":
            value *= 100  # HealthKit stores SpO2 as a 0-1 fraction
        ts = parse_ts(elem.get("startDate"))

        if metric == "resting_heart_rate_bpm":
            # Daily summary sample, not tied to a session window.
            daily[(ts.date().isoformat(), metric)] = value
            continue

        idx = bisect.bisect_right(starts, ts) - 1
        if idx < 0:
            continue
        start, end, night, source = windows[idx]
        if ts <= end:
            acc[(night, source, metric)].append(value)

    out: dict[tuple[str, str, str], float] = {}
    for (night, source, metric), values in acc.items():
        out[(night, source, metric)] = sum(values) / len(values)
        if metric == "heart_rate_bpm":
            out[(night, source, "heart_rate_min_bpm")] = min(values)
    for (night, metric), value in daily.items():
        out[(night, "daily", metric)] = value
    return out


def build_series(sessions: list[Session], quantities) -> dict[tuple[str, tuple], dict]:
    """Fold sessions + quantities into VM import series keyed by (metric, labels)."""
    series: dict[tuple[str, tuple], dict[str, list]] = defaultdict(
        lambda: {"values": [], "timestamps": []}
    )

    def emit(metric: str, source: str, night: str, tz, value: float) -> None:
        noon = datetime.fromisoformat(night).replace(hour=12, tzinfo=tz)
        key = (f"health_{metric}", (("source", source),))
        series[key]["values"].append(round(value, 4))
        series[key]["timestamps"].append(int(noon.timestamp() * 1000))

    # Main session per (night, source); everything else counts as a nap.
    by_night: dict[tuple[str, str], list[Session]] = defaultdict(list)
    for s in sessions:
        by_night[(night_of(s.start), s.source)].append(s)

    for (night, source), group in sorted(by_night.items()):
        main = max(group, key=lambda s: s.asleep)
        tz = main.start.tzinfo
        midnight = datetime.fromisoformat(night).replace(tzinfo=tz)

        emit("sleep_asleep_hours", source, night, tz, main.asleep)
        emit("sleep_in_bed_hours", source, night, tz, main.in_bed)
        for stage in ("deep", "rem", "core", "awake", "asleep_unspecified"):
            if main.stages[stage]:
                emit(f"sleep_{stage}_hours", source, night, tz, main.stages[stage])
        if main.in_bed:
            emit(
                "sleep_efficiency_percent", source, night, tz,
                min(100.0, 100 * main.asleep / main.in_bed),
            )
        # Bedtime as hours relative to the wake day's midnight (23:30 -> -0.5).
        emit(
            "sleep_bedtime_offset_hours", source, night, tz,
            (main.start - midnight).total_seconds() / 3600,
        )
        emit(
            "sleep_waketime_hours", source, night, tz,
            (main.end - midnight).total_seconds() / 3600,
        )
        if main.in_bed_start:
            emit(
                "sleep_latency_minutes", source, night, tz,
                max(0.0, (main.start - main.in_bed_start).total_seconds() / 60),
            )
        naps = sum(s.asleep for s in group if s is not main)
        if naps:
            emit("sleep_nap_hours", source, night, tz, naps)
        emit("sleep_sessions", source, night, tz, float(len(group)))

        for metric in (
            "heart_rate_bpm", "heart_rate_min_bpm", "hrv_ms",
            "respiratory_rate_bpm", "oxygen_saturation_percent",
            "wrist_temperature_celsius",
        ):
            value = quantities.get((night, source, metric))
            if value is not None:
                emit(f"sleep_{metric}", source, night, tz, value)

        rhr = quantities.get((night, "daily", "resting_heart_rate_bpm"))
        if rhr is not None:
            emit("resting_heart_rate_bpm", "daily", night, tz, rhr)

    return series


def print_stats(sessions: list[Session], series) -> None:
    by_night: dict[tuple[str, str], list[Session]] = defaultdict(list)
    for s in sessions:
        by_night[(night_of(s.start), s.source)].append(s)
    mains = {k: max(v, key=lambda s: s.asleep) for k, v in by_night.items()}

    nights = sorted({night for night, _ in mains})
    raw = sum(len(s._sleep) + len(s._bed) for s in sessions)
    over = [
        (night, source) for (night, source), m in mains.items()
        if m.in_bed and m.asleep / m.in_bed > 1.001
    ]
    print(f"\nsleep segments : {raw:,} across {len(sessions):,} sessions")
    print(f"nights with efficiency > 100% (should be 0): {len(over)}")
    print(f"nights         : {len(nights):,}  ({nights[0]} -> {nights[-1]})" if nights else "nights: 0")

    print("\nper source:")
    per_source: dict[str, list[Session]] = defaultdict(list)
    for (_, source), main in mains.items():
        per_source[source].append(main)
    for source, mains_s in sorted(per_source.items(), key=lambda kv: -len(kv[1])):
        staged = sum(1 for m in mains_s if m.has_stages)
        avg = sum(m.asleep for m in mains_s) / len(mains_s)
        first = min(night_of(m.start) for m in mains_s)
        last = max(night_of(m.start) for m in mains_s)
        print(
            f"  {source:<24} {len(mains_s):>5} nights  {first} -> {last}"
            f"  stages: {staged:>5} ({100 * staged / len(mains_s):.0f}%)"
            f"  avg asleep: {avg:.2f}h"
        )

    samples = sum(len(v["values"]) for v in series.values())
    print(f"\nseries         : {len(series):,}")
    print(f"samples        : {samples:,}")
    print(f"import payload : ~{sum(len(json.dumps({'metric': dict(k[1], __name__=k[0]), **v})) for k, v in series.items()) / 1e6:.1f} MB uncompressed")

    print("\nlast 7 nights (main session per source):")
    print(f"  {'night':<12} {'source':<22} {'asleep':>7} {'deep':>6} {'rem':>6} {'core':>6} {'awake':>6} {'eff':>5}")
    for night, source in sorted(mains)[-7:]:
        m = mains[(night, source)]
        eff = 100 * m.asleep / m.in_bed if m.in_bed else 0
        print(
            f"  {night:<12} {source:<22} {m.asleep:>6.2f}h {m.stages['deep']:>5.2f}h"
            f" {m.stages['rem']:>5.2f}h {m.stages['core']:>5.2f}h {m.stages['awake']:>5.2f}h {eff:>4.0f}%"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", help="export.zip, export.xml or export.xml.gz")
    ap.add_argument("--out", help="write VictoriaMetrics import JSON-lines here")
    ap.add_argument("--url", help="POST to a VM /api/v1/import endpoint")
    ap.add_argument("--no-quantities", action="store_true", help="skip the HR/HRV/SpO2 pass")
    args = ap.parse_args()

    print(f"pass 1/2: sleep records from {args.export} ...", file=sys.stderr)
    sessions = collect_sessions(args.export)
    if not sessions:
        sys.exit("no sleep records found in the export")

    quantities: dict = {}
    if not args.no_quantities:
        print("pass 2/2: heart rate / HRV / respiratory / SpO2 ...", file=sys.stderr)
        by_night: dict[tuple[str, str], list[Session]] = defaultdict(list)
        for s in sessions:
            by_night[(night_of(s.start), s.source)].append(s)
        windows = [
            (main.start, main.end, night, source)
            for (night, source), group in by_night.items()
            for main in [max(group, key=lambda s: s.asleep)]
        ]
        quantities = collect_quantities(args.export, windows)

    series = build_series(sessions, quantities)
    print_stats(sessions, series)

    if not (args.out or args.url):
        return

    lines = [
        json.dumps({"metric": {"__name__": name, **dict(labels)}, **payload})
        for (name, labels), payload in sorted(series.items())
    ]
    body = ("\n".join(lines) + "\n").encode()
    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(body)
        print(f"\nwrote {args.out} ({len(body) / 1e6:.1f} MB, {len(lines)} series)")
    if args.url:
        req = urllib.request.Request(
            args.url.rstrip("/") + "/api/v1/import",
            data=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        with urllib.request.urlopen(req) as resp:
            print(f"POST {args.url} -> {resp.status}")


if __name__ == "__main__":
    main()
