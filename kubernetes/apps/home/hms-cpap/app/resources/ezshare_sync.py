#!/usr/bin/env python3
import http.client
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = os.environ.get("EZ_BASE", "http://10.50.0.22").rstrip("/")
MODE = os.environ.get("EZ_MODE", "mule")
OUT = os.environ.get("EZ_OUT", "/data/ezshare-card")
DAYS = int(os.environ.get("EZ_DAYS", "7"))
INTERVAL = int(os.environ.get("EZ_INTERVAL", "900"))
TIMEOUT = int(os.environ.get("EZ_TIMEOUT", "90"))
PAUSE = float(os.environ.get("EZ_PAUSE", "0.1"))
RUN_ONCE = os.environ.get("EZ_RUN_ONCE", "") == "1"
CHUNK = int(os.environ.get("EZ_CHUNK", str(256 * 1024)))
RETRIES = int(os.environ.get("EZ_RETRIES", "4"))
SKIP_TYPES = {t for t in os.environ.get("EZ_SKIP_TYPES", "").upper().split(",") if t}

ROOT_FILES = ["STR.edf", "STR.crc", "Identification.tgt", "Identification.crc", "journal.dat", "journal.jnl"]
LATE = [t for t in ["BRP", "PLD", "SAD"] if t not in SKIP_TYPES]
EARLY = [t for t in ["EVE", "CSL"] if t not in SKIP_TYPES]
STATE_PATH = os.environ.get("EZ_STATE", os.path.join(OUT, ".ezshare-sync.json"))


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def url_for(path):
    path = "/" + path.lstrip("/")
    if MODE == "mule":
        return f"{BASE}/raw?path={urllib.parse.quote(path, safe='/')}"
    return BASE + urllib.parse.quote(path, safe="/")


def fetch(path, rng=None):
    req = urllib.request.Request(url_for(path), headers={"User-Agent": "hms-cpap-ezshare-sync"})
    if rng:
        req.add_header("Range", rng)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    finally:
        time.sleep(PAUSE)


def is_placeholder(body):
    head = body[:512]
    return b"REDIRECTFORM" in head or b"<title>Success" in head or b"<html" in head[:64].lower()


class UpstreamError(Exception):
    pass


def check_upstream(status, body):
    if status not in (200, 206):
        raise UpstreamError(f"HTTP {status} from {BASE}: {body[:120]!r}")


def exists(path):
    status, body = fetch(path, "bytes=1-1")
    check_upstream(status, body)
    if status == 206:
        return True
    return len(body) > 0 and not is_placeholder(body)


def edf_expected_size(head):
    try:
        header_bytes = int(head[184:192])
        nrec = int(head[236:244])
        ns = int(head[252:256])
        spr_off = 256 + ns * (16 + 80 + 8 + 8 + 8 + 8 + 8)
        spr = [int(head[spr_off + i * 8:spr_off + (i + 1) * 8]) for i in range(ns)]
    except ValueError:
        return None
    if nrec < 0 or len(head) < spr_off + ns * 8:
        return None
    return header_bytes + nrec * sum(spr) * 2


def stream_into(path, data, start):
    req = urllib.request.Request(url_for(path), headers={"User-Agent": "hms-cpap-ezshare-sync"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if start and r.status == 200:
                del data[:]
            check_upstream(r.status, b"")
            while True:
                piece = r.read(65536)
                if not piece:
                    return True
                data += piece
    except urllib.error.HTTPError as e:
        check_upstream(e.code, e.read())
        return True
    except http.client.IncompleteRead as e:
        data += e.partial
        return False
    finally:
        time.sleep(PAUSE)


def download(path, dest):
    data = bytearray()
    expected = None
    is_edf = path.lower().endswith(".edf")
    for attempt in range(RETRIES):
        start = len(data)
        try:
            complete = stream_into(path, data, start)
        except UpstreamError:
            raise
        except Exception as e:
            complete = False
            log(f"  {path}: read failed at {len(data)} bytes ({e!r}), retry {attempt + 1}/{RETRIES}")
        if start == 0 and (not data or is_placeholder(bytes(data[:512]))):
            return False
        if is_edf and expected is None and len(data) >= 256:
            if data[:8].strip() != b"0":
                log(f"  bad EDF header for {path}, not saving")
                return False
            expected = edf_expected_size(bytes(data[:65536]))
        if expected is not None and len(data) >= expected:
            del data[expected:]
            break
        if complete:
            if expected is None:
                break
            log(f"  {path}: stream ended at {len(data)} of {expected} bytes, retry {attempt + 1}/{RETRIES}")
        time.sleep(2 * (attempt + 1))
    if expected is not None and len(data) != expected:
        log(f"  {path}: got {len(data)} bytes, EDF header says {expected}, not saving")
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return True


def parse_str(data):
    hdr = data[:256]
    num_records = int(hdr[236:244].decode().strip())
    num_signals = int(hdr[252:256].decode().strip())
    header_bytes = int(hdr[184:192].decode().strip())
    dd, mm, yy = hdr[168:176].decode().strip().split(".")
    hh, mi, ss = hdr[176:184].decode().strip().split(".")
    year = 2000 + int(yy) if int(yy) < 85 else 1900 + int(yy)
    start = datetime(year, int(mm), int(dd), int(hh), int(mi), int(ss))
    labels = [data[256 + i * 16:256 + (i + 1) * 16].decode().strip() for i in range(num_signals)]
    spr_off = 256 + num_signals * (16 + 80 + 8 + 8 + 8 + 8 + 8 + 80)
    spr = [int(data[spr_off + i * 8:spr_off + (i + 1) * 8].decode().strip()) for i in range(num_signals)]
    total = sum(spr)
    on_i, off_i = labels.index("MaskOn"), labels.index("MaskOff")
    on_off, off_off = sum(spr[:on_i]), sum(spr[:off_i])
    n = min(spr[on_i], spr[off_i])
    sessions = []
    for rec in range(num_records):
        base = header_bytes + rec * total * 2
        noon = (start + timedelta(days=rec)).replace(hour=12, minute=0, second=0)
        ons = struct.unpack_from("<" + "h" * n, data, base + on_off * 2)
        offs = struct.unpack_from("<" + "h" * n, data, base + off_off * 2)
        for on, off in zip(ons, offs):
            if on <= 0 or off <= 0 or on >= 1440 or off >= 1440 or off <= on:
                continue
            sessions.append({"rec": noon.strftime("%Y%m%d"), "start": noon + timedelta(minutes=on), "dur": off - on})
    return sessions


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(OUT, exist_ok=True)
    tmp = STATE_PATH + ".part"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def find_seconds(rec, fdate, hhmm, ftype, near=None):
    order = range(60)
    if near is not None:
        ref = int(near)
        order = [ref] + [c for o in range(1, 16) for c in (ref + o, ref - o) if 0 <= c < 60]
    for ss in order:
        if exists(f"DATALOG/{rec}/{fdate}_{hhmm}{ss:02d}_{ftype}.edf"):
            return f"{ss:02d}"
    return None


def sync_root():
    got = 0
    for name in ROOT_FILES:
        dest = os.path.join(OUT, name)
        status, body = fetch(name)
        check_upstream(status, body)
        if not body or is_placeholder(body):
            continue
        if name.lower().endswith(".edf") and body[:8].strip() != b"0":
            continue
        try:
            with open(dest, "rb") as f:
                if f.read() == body:
                    continue
        except OSError:
            pass
        os.makedirs(OUT, exist_ok=True)
        with open(dest + ".part", "wb") as f:
            f.write(body)
        os.replace(dest + ".part", dest)
        got += 1
        log(f"  root {name} updated ({len(body)} bytes)")
    return got


def sync_datalog(sessions, state):
    cutoff = (datetime.now() - timedelta(days=DAYS)).strftime("%Y%m%d")
    todo = [s for s in sessions if s["rec"] >= cutoff]
    downloaded = probes_before = 0
    for s in todo:
        fdate, hhmm = s["start"].strftime("%Y%m%d"), s["start"].strftime("%H%M")
        key = f"{s['rec']}/{fdate}_{hhmm}"
        entry = state.setdefault(key, {"files": {}, "missing": []})
        if entry.get("done"):
            continue
        if "BRP" not in entry["files"]:
            ss = find_seconds(s["rec"], fdate, hhmm, "BRP")
            if ss is None:
                entry["attempts"] = entry.get("attempts", 0) + 1
                log(f"  {key}: BRP not found (attempt {entry['attempts']})")
                if entry["attempts"] >= 3:
                    entry["done"] = True
                save_state(state)
                continue
            entry["files"]["BRP"] = ss
        brp_ss = entry["files"].get("BRP")
        for ftype in LATE + EARLY:
            if ftype == "BRP" or ftype in entry["files"] or ftype in entry["missing"]:
                continue
            ss = find_seconds(s["rec"], fdate, hhmm, ftype, near=brp_ss)
            if ss is None:
                entry["missing"].append(ftype)
            else:
                entry["files"][ftype] = ss
        all_ok = True
        for ftype, ss in entry["files"].items():
            rel = f"DATALOG/{s['rec']}/{fdate}_{hhmm}{ss}_{ftype}.edf"
            dest = os.path.join(OUT, rel)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                continue
            if download(rel, dest):
                downloaded += 1
                log(f"  got {rel} ({os.path.getsize(dest)} bytes)")
            else:
                all_ok = False
                log(f"  failed {rel}")
        if all_ok:
            entry["done"] = True
        save_state(state)
    return downloaded


def cycle():
    state = load_state()
    sync_root()
    str_path = os.path.join(OUT, "STR.edf")
    if not os.path.exists(str_path):
        log("no STR.edf yet, card unreachable?")
        return
    with open(str_path, "rb") as f:
        sessions = parse_str(f.read())
    n = sync_datalog(sessions, state)
    log(f"cycle done: {len(sessions)} sessions in STR, {n} files downloaded")


def main():
    log(f"ezshare-sync mode={MODE} base={BASE} out={OUT} days={DAYS} interval={INTERVAL}s")
    while True:
        try:
            cycle()
        except Exception as e:
            log(f"cycle failed: {e!r}")
        if RUN_ONCE:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
