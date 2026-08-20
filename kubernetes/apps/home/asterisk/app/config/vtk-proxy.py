#!/usr/bin/env python3
"""vtk-proxy: vendor "myMediaProxy" emulator for the DT/DX470 intercom monitor.

Protocol (reverse-engineered from the VDP Connect APK and DX470 firmware):
- While the monitor is in a SIP call it connects out via TCP to port 8850 and
  logs in (V2): [2,16,1,0, acct(31), pwd(15), relAcct(31)].
- Proxy replies (V2 rsp): [3,16,1,0, acct(31), pwd(15), relAcct(31),
  audioPort(2), videoPort(2)], ports big-endian at offsets 84/86.
- Control frames: [16,16,1,0, ctlcode, 0, ascii payload...]
    ctlcode 2 + "0x3X#"  -> start relaying door station X video/audio
    ctlcode 1 + lockIdx  -> unlock (8-byte frame, no payload)
- The monitor then streams H264 RTP to the advertised video port.
"""

import json
import random
import re
import socket
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_CTRL_IP = "0.0.0.0"
CTRL_PORT = 8850        # monitor connects here (pod multus IP 10.50.0.248)
VIDEO_PORT = 38080      # monitor sends H264 RTP here
AUDIO_PORT = 39080
HTTP_PORT = 8851        # cluster-internal API

MONITOR_IP = "10.50.0.190"
MONITOR_ENDPOINT = "640000033c9b"
MON_CODE = "1000"       # R-URI user that makes the monitor auto-answer (channel holder)
TAP_EXTEN = "9920"      # dialplan: channel-holder call parks here
DIVERT_EXTEN_RE = re.compile(r"^2000$")

AMI_HOST, AMI_PORT = "127.0.0.1", 5038
AMI_USER, AMI_PASS = "admin", "hass123"

DS_CODES = {1: "0x34#", 2: "0x35#", 3: "0x36#", 4: "0x37#"}
RTP_TIMEOUT = 6.0       # s without RTP -> session over
LOGIN_WAIT = 15.0
TAP_MAX_SECONDS = 180.0  # cap on one refresh chain: never pin the monitor forever
TAP_COOLDOWN = 30.0      # after the cap, leave the monitor alone this long
AUDIO_PT = 48           # monitor's G.711 µ-law payload type (nonstandard)
AUDIO_FRAME = 160       # 20ms @ 8kHz mono


class Session:
    def __init__(self, mode):
        self.mode = mode            # "tap" | "divert"
        self.ctrl = None            # TCP conn to monitor
        self.ctrl_addr = None
        self.call_channel = None    # AMI channel name when we originated it
        self.tap_code = None
        self.h264 = bytearray()
        self.audio = bytearray()
        self.audio_pt = None
        self.audio_peer = None
        self.audio_tx = 0
        self.consumers = 0
        self.last_rtp = 0.0
        self.cond = threading.Condition()
        self.closed = False

    def send_ctrl(self, frame):
        if self.ctrl:
            try:
                self.ctrl.sendall(frame)
                return True
            except OSError:
                pass
        return False


STATE = {
    "session": None,
    "started": time.time(),
    "taps": 0,
    "doorbells": 0,
}
LOCK = threading.Lock()


def current():
    return STATE["session"]


def log(msg):
    print(f"[vtk-proxy] {msg}", flush=True)


# ---------------------------------------------------------------- AMI client

class Ami(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.sock = None
        self.events = []           # parsed event dicts for waiting callers
        self.ev_cond = threading.Condition()

    def connect(self):
        s = socket.create_connection((AMI_HOST, AMI_PORT), timeout=10)
        s.settimeout(120)
        s.recv(1024)
        s.sendall((f"Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {AMI_PASS}\r\n\r\n").encode())
        s.settimeout(1.0)
        time.sleep(0.3)
        try:
            s.recv(4096)
        except socket.timeout:
            pass
        self.sock = s
        log("AMI connected")

    def run(self):
        while True:
            try:
                if self.sock is None:
                    self.connect()
                self._read()
            except Exception as e:
                log(f"AMI error: {e}; reconnecting")
                self.sock = None
                time.sleep(3)

    def _read(self):
        buf = b""
        while True:
            try:
                d = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not d:
                raise ConnectionError("AMI closed")
            buf += d
            while b"\r\n\r\n" in buf:
                block, buf = buf.split(b"\r\n\r\n", 1)
                evt = {}
                for line in block.decode(errors="replace").splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        evt.setdefault(k.strip(), v.strip())
                self._dispatch(evt)

    def _dispatch(self, evt):
        kind = evt.get("Event") or evt.get("Response")
        if kind == "Newexten" and evt.get("Context") == "from-intercom":
            if DIVERT_EXTEN_RE.match(evt.get("Exten", "")):
                threading.Thread(target=on_doorbell, daemon=True).start()
        if kind == "OriginateResponse":
            with self.ev_cond:
                self.events.append(evt)
                self.ev_cond.notify_all()

    def action(self, fields):
        msg = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
        self.sock.sendall(msg.encode())


    def wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline and self.sock is None:
            time.sleep(1)
        return self.sock is not None

    def originate_tap(self):
        """Call the monitor (auto-answers) and park it on TAP_EXTEN."""
        if not self.wait_ready():
            return None
        tag = str(time.time_ns())
        with self.ev_cond:
            self.events = [e for e in self.events if e.get("ActionID") != tag]
            self.events = [e for e in self.events if e.get("Response") != "Success"]
        self.action({
            "Action": "Originate",
            "Channel": f"PJSIP/{MON_CODE}@{MONITOR_ENDPOINT}",
            "Context": "default",
            "Exten": TAP_EXTEN,
            "Priority": "1",
            "Timeout": "15000",
            "Async": "true",
            "ActionID": tag,
            "CallerID": "vtk-proxy <8001>",
        })
        deadline = time.time() + 20
        with self.ev_cond:
            while time.time() < deadline:
                for e in self.events:
                    if e.get("ActionID") == tag:
                        self.events.remove(e)
                        if e.get("Response") == "Success":
                            return e.get("Channel")
                        return None
                self.ev_cond.wait(1)
        return None

    def hangup(self, channel):
        if channel:
            try:
                self.action({"Action": "Hangup", "Channel": channel})
            except Exception:
                pass


AMI = Ami()


# ---------------------------------------------------------------- sessions

def on_doorbell():
    STATE["doorbells"] += 1
    log("doorbell divert call detected")
    with LOCK:
        s = STATE["session"]
    if s and s.mode == "divert":
        return
    # Don't originate anything: the monitor's own divert call is the channel
    # holder; it will connect to us on 8850. Arm a divert session and wait.
    sess = Session("divert")
    with LOCK:
        STATE["session"] = sess
    threading.Thread(target=session_supervisor, args=(sess,), daemon=True).start()


def ensure_tap(ds, ptt=True):
    """Manual tap: originate the channel-holder call, wait for monitor login."""
    with LOCK:
        s = STATE["session"]
    if s and not s.closed and s.ctrl:
        _send_code(s, ds, ptt)
        return s
    now = time.time()
    if now < STATE.get("tap_cooldown_until", 0.0):
        log("tap refused: cooling down after cap")
        return None
    chain = STATE.get("tap_chain_start") or now
    if now - chain > TAP_MAX_SECONDS:
        STATE["tap_cooldown_until"] = now + TAP_COOLDOWN
        STATE["tap_chain_start"] = None
        log(f"tap refused: {TAP_MAX_SECONDS:.0f}s cap hit, cooling down {TAP_COOLDOWN:.0f}s")
        return None
    STATE["tap_chain_start"] = chain
    sess = Session("tap")
    sess.tap_code = DS_CODES.get(ds, DS_CODES[1])
    with LOCK:
        STATE["tap_pending"] = sess
        STATE["session"] = sess
    STATE["taps"] += 1

    ch = None
    for attempt in range(6):
        ch = AMI.originate_tap()
        if ch:
            break
        time.sleep(3 + attempt * 3)   # monitor rejects calls while tearing down
                                            # the previous session; back off
    if not ch:
        log("tap: originate failed")
        sess.closed = True
        with LOCK:
            if STATE["session"] is sess:
                STATE["session"] = None
        return None
    sess.call_channel = ch
    log(f"tap: channel {ch} up, waiting for monitor proxy login")
    with LOCK:
        _clear_pending(sess)
    deadline = time.time() + LOGIN_WAIT
    while time.time() < deadline and not sess.closed:
        if sess.ctrl:
            _send_code(sess, ds, ptt)
            return sess
        time.sleep(0.3)
    log("tap: monitor never logged in")
    end_session(sess)

def _clear_pending(sess):
    if STATE.get("tap_pending") is sess:
        STATE["tap_pending"] = None
    return None


def _send_code(sess, ds, ptt=True):
    code = DS_CODES.get(ds, DS_CODES[1])
    if sess.ctrl:
        try:
            send_ctrl_frames(sess.ctrl, code, ptt)
            log(f"sent relay code {code}" + (" + P2T" if ptt else " (no P2T)"))
            return
        except OSError:
            pass
    log(f"send relay code {code} failed: no ctrl conn")


def session_supervisor(sess):
    """Watch RTP liveness and clean up."""
    while not sess.closed:
        time.sleep(2)
        if sess.last_rtp and time.time() - sess.last_rtp > RTP_TIMEOUT:
            log(f"session {sess.mode}: RTP timed out")
            break
        if sess.mode == "tap" and not sess.last_rtp and not sess.ctrl \
                and time.time() - STATE.get("session_start", time.time()) > LOGIN_WAIT + 10:
            break
    end_session(sess)


def end_session(sess):
    if sess.closed:
        return
    sess.closed = True
    with sess.cond:
        sess.cond.notify_all()
    if sess.ctrl:
        try:
            sess.ctrl.close()
        except OSError:
            pass
    if sess.call_channel:
        AMI.hangup(sess.call_channel)
    with LOCK:
        if STATE["session"] is sess:
            STATE["session"] = None
    if sess.mode == "tap" and sess.consumers <= 0:
        STATE["tap_chain_start"] = None
    log(f"session {sess.mode} ended ({len(sess.h264)} h264 bytes)")


def send_ctrl_frames(conn, tap_code, ptt=True):
    """Open the video relay, then optionally engage press-to-talk like the app's
    "speaking" button — the monitor keeps streaming a blue placeholder until
    the P2T command (ctlcode 4, "9#") starts the real 2-wire session. P2T also
    appears to hold the half-duplex path in the talk direction, which mutes the
    door station mic, so it is switchable."""
    relay = bytes([16, 16, 1, 0, 2, 0]) + tap_code.encode()
    conn.sendall(relay)
    if not ptt:
        return
    time.sleep(0.5)
    conn.sendall(bytes([16, 16, 1, 0, 4, 0]) + b"9#")


# ---------------------------------------------------------------- ctrl plane

def ctrl_server():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_CTRL_IP, CTRL_PORT))
    srv.listen(5)
    log(f"ctrl listening on :{CTRL_PORT}")

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=ctrl_conn, args=(conn, addr), daemon=True).start()


def ctrl_conn(conn, addr):
    global_t0 = time.time()
    try:
        conn.settimeout(10)
        d = b""
        while len(d) < 84:
            chunk = conn.recv(4096)
            if not chunk:
                return
            d += chunk
        if d[0] != 2 or d[1] != 16:
            log(f"ctrl: unknown frame {d[:4].hex()} from {addr}")
            return
        acct = d[4:36].split(b"\0")[0]
        pwd = d[36:52].split(b"\0")[0]
        rel = d[52:84].split(b"\0")[0]
        log(f"ctrl: monitor login from {addr} acct={acct.decode()} rel={rel.decode()}")

        rsp = bytearray(88)
        rsp[0:2] = bytes([3, 16])
        rsp[2] = 1
        rsp[4:4 + len(acct)] = acct
        rsp[36:36 + len(pwd)] = pwd
        rsp[52:52 + len(rel)] = rel
        # ports are little-endian in the V2 response (matches the working
        # manual test and the app's convertShort(hi, lo) parsing)
        rsp[84] = AUDIO_PORT & 0xFF
        rsp[85] = (AUDIO_PORT >> 8) & 0xFF
        rsp[86] = VIDEO_PORT & 0xFF
        rsp[87] = (VIDEO_PORT >> 8) & 0xFF
        conn.sendall(bytes(rsp))

        with LOCK:
            sess = STATE["session"]
        if sess and not sess.closed:
            if sess.ctrl:
                try:
                    sess.ctrl.close()
                except OSError:
                    pass
            sess.ctrl = conn
            sess.ctrl_addr = addr
            with sess.cond:
                sess.cond.notify_all()
            if sess.mode == "tap" and sess.tap_code:
                send_ctrl_frames(conn, sess.tap_code)
                log(f"ctrl: sent tap code {sess.tap_code}")
        elif STATE.get("tap_pending"):
            # A tap is originating right now but its session object hasn't been
            # replaced yet (old one still closing). Attach to the pending tap.
            sess = STATE["tap_pending"]
            sess.ctrl = conn
            sess.ctrl_addr = addr
            with sess.cond:
                sess.cond.notify_all()
            if sess.tap_code:
                send_ctrl_frames(conn, sess.tap_code)
            log("ctrl: attached login to pending tap session")
            threading.Thread(target=session_supervisor, args=(sess,), daemon=True).start()
        else:
            # Login outside a tracked session (e.g. divert before detection):
            # adopt it as a divert session and relay DS1.
            sess = Session("divert")
            sess.ctrl = conn
            sess.ctrl_addr = addr
            sess.last_rtp = 0
            with LOCK:
                STATE["session"] = sess
            send_ctrl_frames(conn, DS_CODES[1])
            log("ctrl: adopted untracked login as divert session, sent DS1 code")
            threading.Thread(target=session_supervisor, args=(sess,), daemon=True).start()

        # keep reading until monitor closes (replies to our ctrl frames)
        conn.settimeout(None)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
        end_session(sess)
    except Exception as e:
        log(f"ctrl: {type(e).__name__} {e} after {time.time()-global_t0:.0f}s")
        sess = current()
        if sess and sess.ctrl is conn:
            end_session(sess)
    finally:
        try:
            conn.close()
        except OSError:
            pass


# ---------------------------------------------------------------- RTP video

def rtp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", VIDEO_PORT))
    log(f"rtp listening on :{VIDEO_PORT}")
    while True:
        d, addr = sock.recvfrom(65535)
        sess = current()
        if not sess or sess.closed or len(d) < 12:
            continue
        if addr[0] != MONITOR_IP:
            continue
        sess.last_rtp = time.time()
        b0 = d[0]
        cc = b0 & 0x0F
        hdr = 12 + 4 * cc
        payload = d[hdr:]
        if not payload:
            continue
        nal = payload[0] & 0x1F
        with sess.cond:
            if nal == 28:  # FU-A
                fu_ind, fu_hdr = payload[0], payload[1]
                if fu_hdr & 0x80:  # start
                    sess.h264 += b"\x00\x00\x00\x01" + bytes([(fu_ind & 0xE0) | (fu_hdr & 0x1F)]) + payload[2:]
                else:
                    sess.h264 += payload[2:]
            elif nal == 24:  # STAP-A
                i = 1
                while i + 2 <= len(payload):
                    sz = struct.unpack(">H", payload[i:i + 2])[0]
                    sess.h264 += b"\x00\x00\x00\x01" + payload[i + 2:i + 2 + sz]
                    i += 2 + sz
            elif 1 <= nal <= 9:
                sess.h264 += b"\x00\x00\x00\x01" + payload
            sess.cond.notify_all()


# ---------------------------------------------------------------- RTP audio

def audio_listener():
    """The monitor streams the door station mic to the audio port advertised in
    the login response. Payload is raw G.711/G.722 frames; PT is logged once per
    session so the go2rtc ffmpeg input format can match it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", AUDIO_PORT))
    STATE["audio_sock"] = sock
    log(f"audio rtp listening on :{AUDIO_PORT}")
    while True:
        d, addr = sock.recvfrom(65535)
        sess = current()
        if not sess or sess.closed or len(d) < 12:
            continue
        if addr[0] != MONITOR_IP:
            continue
        if sess.audio_peer is None:
            sess.audio_peer = addr
            threading.Thread(target=audio_sender, args=(sess,), daemon=True).start()
            log(f"audio peer {addr[0]}:{addr[1]}, starting reverse stream")
        cc = d[0] & 0x0F
        payload = d[12 + 4 * cc:]
        if not payload:
            continue
        if sess.audio_pt is None:
            sess.audio_pt = d[1] & 0x7F
            log(f"audio rtp: pt={sess.audio_pt}, {len(payload)}B frames from {addr[0]}")
        with sess.cond:
            sess.audio += payload
            sess.cond.notify_all()


def audio_sender(sess):
    """Symmetric RTP. The monitor gates the door station mic until it sees a
    reverse stream (the vendor proxy relays both directions), so push µ-law
    silence at its source port. Also the hook for two-way talk later."""
    sock = STATE.get("audio_sock")
    peer = sess.audio_peer
    if not sock or not peer:
        return
    seq = 0
    ts = 0
    ssrc = random.getrandbits(32)
    silence = b"\xff" * AUDIO_FRAME
    next_tick = time.time()
    while not sess.closed:
        hdr = struct.pack(">BBHII", 0x80, AUDIO_PT, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
        try:
            sock.sendto(hdr + silence, peer)
        except OSError:
            return
        sess.audio_tx += 1
        seq += 1
        ts += AUDIO_FRAME
        next_tick += 0.02
        delay = next_tick - time.time()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.time()


# ---------------------------------------------------------------- HTTP API

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/health":
                return self._json({"ok": True, "uptime": int(time.time() - STATE["started"])})
            if u.path == "/status":
                s = current()
                return self._json({
                    "session": s and {
                        "mode": s.mode,
                        "ctrl": bool(s.ctrl),
                        "h264_bytes": len(s.h264),
                        "last_rtp_ago": s.last_rtp and round(time.time() - s.last_rtp, 1),
                        "audio_bytes": len(s.audio),
                        "audio_pt": s.audio_pt,
                        "audio_tx": s.audio_tx,
                    },
                    "taps": STATE["taps"],
                    "doorbells": STATE["doorbells"],
                })
            if u.path == "/tap":
                ds = int(q.get("ds", ["1"])[0])
                ptt = q.get("ptt", ["1"])[0] not in ("0", "false", "off")
                sess = ensure_tap(ds, ptt)
                if sess:
                    return self._json({"ok": True, "mode": sess.mode})
                return self._json({"ok": False, "error": "tap failed"}, 503)
            if u.path == "/ctrl":
                # Control-frame probe for the codes the vendor app sends:
                # 2 = open video relay, 4 = press-to-talk. Unlock (1) is
                # deliberately excluded; it has its own endpoint.
                code = int(q.get("code", ["4"])[0])
                payload = q.get("payload", ["9#"])[0]
                if code in (0, 1):
                    return self._json({"ok": False, "error": "code not allowed"}, 400)
                sess = current()
                if not sess or not sess.ctrl or sess.closed:
                    return self._json({"ok": False, "error": "no active session"}, 409)
                frame = bytes([16, 16, 1, 0, code, 0]) + payload.encode()
                ok = sess.send_ctrl(frame)
                log(f"ctrl probe code={code} payload={payload!r} -> {ok}")
                return self._json({"ok": ok, "code": code, "payload": payload})
            if u.path == "/unlock":
                lock = int(q.get("lock", ["1"])[0])
                sess = current()
                if not sess or not sess.ctrl or sess.closed:
                    return self._json({"ok": False, "error": "no active session"}, 409)
                frame = bytes([16, 16, 1, 0, 1, 0, lock, 0])
                ok = sess.send_ctrl(frame)
                log(f"unlock lock={lock} -> {ok}")
                return self._json({"ok": ok})
            if u.path == "/stop":
                sess = current()
                if sess:
                    end_session(sess)
                return self._json({"ok": True})
            if u.path == "/video":
                ds = int(q.get("ds", ["1"])[0])
                return self.stream_video(ds)
            if u.path == "/audio":
                ds = int(q.get("ds", ["1"])[0])
                return self.stream_audio(ds)
            return self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._json({"ok": False, "error": str(e)}, 500)
            except Exception:
                pass

    def stream_video(self, ds):
        sess = current()
        if not sess or not sess.ctrl or sess.closed:
            sess = ensure_tap(ds)
        if not sess:
            self._json({"ok": False, "error": "no session"}, 503)
            return
        sess.consumers += 1
        # start at an IDR boundary if we can find one
        pos = 0
        start = 0
        with sess.cond:
            data = bytes(sess.h264)
        while pos < len(data):
            if data[pos:pos + 4] == b"\x00\x00\x00\x01":
                if len(data) > pos + 4 and (data[pos + 4] & 0x1F) in (5, 7):
                    start = pos
                    break
                pos += 4
            else:
                pos += 1
        self.send_response(200)
        self.send_header("Content-Type", "video/h264")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        sent = start
        idle = 0
        try:
            while True:
                sess = current()
                if not sess or sess.closed:
                    # monitor BYEd the channel-holder call (firmware tap-session
                    # limit); refresh the tap while a consumer is still attached
                    sess = ensure_tap(ds)
                    if not sess:
                        break
                    sess.consumers += 1
                    with sess.cond:
                        data = bytes(sess.h264)
                    sent = 0
                    pos = 0
                    while pos < len(data):
                        if data[pos:pos + 4] == b"\x00\x00\x00\x01":
                            if len(data) > pos + 4 and (data[pos + 4] & 0x1F) in (5, 7):
                                sent = pos
                                break
                            pos += 4
                        else:
                            pos += 1
                with sess.cond:
                    sess.cond.wait(1.0)
                    data = bytes(sess.h264)
                if len(data) > sent:
                    self.wfile.write(data[sent:])
                    sent = len(data)
                    idle = 0
                else:
                    idle += 1
                    if sess.last_rtp and time.time() - sess.last_rtp > RTP_TIMEOUT:
                        break
                    if idle > 30:
                        break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if sess:
                sess.consumers -= 1
                if sess.mode == "tap" and sess.call_channel and sess.consumers <= 0:
                    end_session(sess)

    def stream_audio(self, ds):
        """Raw door station mic payload; go2rtc muxes it alongside /video."""
        sess = current()
        if not sess or not sess.ctrl or sess.closed:
            sess = ensure_tap(ds)
        if not sess:
            self._json({"ok": False, "error": "no session"}, 503)
            return
        sess.consumers += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with sess.cond:
            sent = len(sess.audio)   # live only, skip the backlog
        idle = 0
        try:
            while True:
                s = current()
                if not s or s.closed:
                    break
                if s is not sess:      # tap refreshed under us
                    sess.consumers -= 1
                    sess = s
                    sess.consumers += 1
                    sent = 0
                with sess.cond:
                    sess.cond.wait(1.0)
                    data = bytes(sess.audio)
                if len(data) > sent:
                    self.wfile.write(data[sent:])
                    sent = len(data)
                    idle = 0
                else:
                    idle += 1
                    if idle > 30:
                        break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            sess.consumers -= 1


def main():
    threading.Thread(target=ctrl_server, daemon=True).start()
    threading.Thread(target=rtp_listener, daemon=True).start()
    threading.Thread(target=audio_listener, daemon=True).start()
    AMI.start()
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    log(f"http listening on :{HTTP_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
