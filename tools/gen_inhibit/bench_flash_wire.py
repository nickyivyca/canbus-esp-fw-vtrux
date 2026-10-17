#!/usr/bin/env python3
"""A Wi-Fi capture around bench_flash.py's OTA upload, and the one line it gives
when the upload fails (reviewer, 2026-10-17, approved by the user; the open
128 KiB upload-reset observation).

MESSAGE ONLY. Nothing here enters bench_flash's verdict or its pass/fail, so a
run with the capture and one without stay comparable. A host with no Wireshark,
an interface that is not there, or a capture that will not start: the flash
goes ahead as before and says the diagnostic was skipped.

What the line says, read the way ota_pcap_stall.sh reads a capture: on the
upload's connection, the highest byte the device ACKed, and which of two
things the device's ACKs did at the end --
  - its last ACK advertised less than one of the laptop's segments: the device
    stopped reading (window closed);
  - its last ACK left the window open: the ACKs stopped with the window open
    (link lost);
plus the longest gap between device ACKs, and when the upload ended. An RST
from either side and the device's first reply data are added when present.

The device's address is bench_flash's --host; none is committed here. dumpcap
and tshark come from PATH or the standard Wireshark install folder.
"""
import os
import shutil
import signal
import subprocess
import time

# The laptop's largest segment on the upload is the measure of "one segment";
# this is used only when the capture holds none (ota_pcap_stall.sh's 1440).
SEGMENT_FALLBACK = 1440
READY_TIMEOUT_S = 10.0
READY_MARGIN_S = 1.5
STOP_TIMEOUT_S = 10.0

FIELDS = ("frame.time_epoch", "ip.src", "tcp.stream", "tcp.flags.syn", "tcp.flags.ack",
          "tcp.flags.fin", "tcp.flags.reset", "tcp.ack", "tcp.window_size", "tcp.len")


def find_tool(name):
    """dumpcap or tshark: PATH first, then the standard install folder."""
    p = shutil.which(name)
    if p:
        return p
    pf = os.environ.get("ProgramFiles")
    if pf:
        p = os.path.join(pf, "Wireshark", name + ".exe")
        if os.path.exists(p):
            return p
    return None


def pick_device(listing, name):
    """The capture device whose `dumpcap -D` line ends in "(<name>)". Pure."""
    for line in listing.splitlines():
        s = line.strip()
        if s.endswith("(%s)" % name) and ". " in s:
            return s.split(". ", 1)[1][:-len(" (%s)" % name)].strip()
    return None


def _flag(v):
    return v.strip() in ("1", "True", "true")


def parse_rows(text):
    """tshark -T fields output, FIELDS in order, into dicts. Pure."""
    rows = []
    for ln in text.splitlines():
        f = ln.split("\t")
        if len(f) < len(FIELDS) or not f[0] or not f[2]:
            continue
        try:
            rows.append({"t": float(f[0]), "src": f[1], "stream": int(f[2]),
                         "syn": _flag(f[3]), "ackf": _flag(f[4]), "fin": _flag(f[5]), "rst": _flag(f[6]),
                         "ack": int(f[7] or 0), "win": int(f[8] or 0), "len": int(f[9] or 0)})
        except ValueError:
            continue
    return rows


def upload_stream(rows, host):
    """The connection the laptop sent the most payload on: the upload. Pure."""
    sent = {}
    for r in rows:
        if r["src"] != host:
            sent[r["stream"]] = sent.get(r["stream"], 0) + r["len"]
    sent = {k: v for k, v in sent.items() if v > 0}
    return max(sent, key=lambda k: (sent[k], -k)) if sent else None


def wire_summary(rows, host, t_start, t_end):
    """What the device's ACKs on the upload did, as numbers. Times are seconds from
    the laptop's SYN (from t_start if the capture holds no SYN). Pure."""
    st = upload_stream(rows, host)
    if st is None:
        return {"stream": None}
    conn = sorted((r for r in rows if r["stream"] == st), key=lambda r: r["t"])
    syn = next((r["t"] for r in conn if r["src"] != host and r["syn"] and not r["ackf"]), None)
    t0 = syn if syn is not None else t_start
    seg = max((r["len"] for r in conn if r["src"] != host), default=0) or SEGMENT_FALLBACK
    acks = [r for r in conn if r["src"] == host and r["ackf"] and r["len"] == 0
            and not (r["syn"] or r["fin"] or r["rst"])]
    data = [r for r in conn if r["src"] == host and r["len"] > 0]
    out = {"stream": st, "from_syn": syn is not None, "segment": seg, "device_acks": len(acks),
           "upload_end_s": round(t_end - t0, 3),
           "rst_from": sorted({"device" if r["src"] == host else "laptop" for r in conn if r["rst"]}),
           "device_data_s": round(data[0]["t"] - t0, 3) if data else None}
    if not acks:
        return out
    hi = max(a["ack"] for a in acks)
    last = acks[-1]
    gap = max(((acks[i + 1]["t"] - acks[i]["t"], acks[i]) for i in range(len(acks) - 1)),
              key=lambda g: g[0], default=None)
    out.update({"highest_ack": hi, "bytes_acked": hi - 1, "last_ack_s": round(last["t"] - t0, 3),
                "last_window": last["win"], "window_closed": last["win"] < seg,
                "longest_gap_s": round(gap[0], 3) if gap else None,
                "gap_after_ack": gap[1]["ack"] if gap else None,
                "gap_at_s": round(gap[1]["t"] - t0, 3) if gap else None})
    return out


def wire_line(s):
    """The one line, from wire_summary(). Pure."""
    if s.get("stream") is None:
        return "wire: no upload connection in the capture"
    since = "the SYN" if s["from_syn"] else "the upload's start"
    if not s["device_acks"]:
        line = "wire: the device sent no ACK on the upload connection"
    else:
        how = ("under one %d-byte segment: the device stopped reading (window closed)" if s["window_closed"]
               else "at least one %d-byte segment: the ACKs stopped with the window open (link lost)") % s["segment"]
        line = ("wire: the device ACKed %d bytes (highest ack %d); its last ACK, %.2f s after %s, advertised "
                "%d bytes, %s; longest gap between device ACKs %s"
                % (s["bytes_acked"], s["highest_ack"], s["last_ack_s"], since, s["last_window"], how,
                   "%.2f s, after ack %d at %.2f s" % (s["longest_gap_s"], s["gap_after_ack"], s["gap_at_s"])
                   if s["longest_gap_s"] is not None else "none (one ACK)"))
    line += "; the upload ended %.2f s after %s" % (s["upload_end_s"], since)
    if s["device_data_s"] is not None:
        line += "; the device's first reply data at %.2f s" % s["device_data_s"]
    if s["rst_from"]:
        line += "; RST from the %s" % " and the ".join(s["rst_from"])
    return line


class Capture:
    """dumpcap on the Wi-Fi interface, host <host> and TCP port 80 only, into <path>.
    Started before the upload and stopped after bench_flash's watch. Every method
    returns instead of raising (bench_flash also guards each call)."""

    def __init__(self, host, path, iface_name, ceiling_s):
        self.host, self.path, self.iface_name, self.ceiling_s = host, path, iface_name, ceiling_s
        self.proc = None

    def start(self):
        dumpcap = find_tool("dumpcap")
        if not dumpcap:
            return False, "no dumpcap (Wireshark not installed)"
        try:
            listing = subprocess.run([dumpcap, "-D"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as e:
            return False, "dumpcap -D failed: %s" % e
        dev = pick_device(listing, self.iface_name)
        if not dev:
            return False, "no capture interface named %r" % self.iface_name
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.exists(self.path):
            os.remove(self.path)
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.proc = subprocess.Popen(
                [dumpcap, "-q", "-i", dev, "-f", "host %s and tcp port 80" % self.host,
                 "-a", "duration:%d" % self.ceiling_s, "-w", self.path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            return False, "dumpcap did not start: %s" % e
        t = time.time()
        while time.time() - t < READY_TIMEOUT_S:
            if self.proc.poll() is not None:
                return False, "dumpcap exited at once (rc %s)" % self.proc.returncode
            if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
                time.sleep(READY_MARGIN_S)
                return True, "capturing"
            time.sleep(0.1)
        self.stop()
        return False, "dumpcap wrote nothing within %.0f s" % READY_TIMEOUT_S

    def stop(self):
        """CTRL_BREAK, so dumpcap closes the file itself; killed only if it does not."""
        p = self.proc
        if p is None:
            return False, "not started"
        if p.poll() is None:
            try:
                p.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT))
                p.wait(STOP_TIMEOUT_S)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                p.kill()
                p.wait(STOP_TIMEOUT_S)
                return True, "killed after %.0f s (the file may end short)" % STOP_TIMEOUT_S
        return True, "stopped (rc %s)" % p.returncode

    def summary(self, t_start, t_end):
        tshark = find_tool("tshark")
        if not tshark:
            return None, "no tshark"
        cmd = [tshark, "-r", self.path, "-o", "tcp.relative_sequence_numbers:TRUE", "-Y", "tcp", "-T", "fields"]
        for f in FIELDS:
            cmd += ["-e", f]
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError) as e:
            return None, "tshark failed: %s" % e
        return wire_summary(parse_rows(text), self.host, t_start, t_end), None
