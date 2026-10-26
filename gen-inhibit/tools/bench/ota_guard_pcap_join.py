#!/usr/bin/env python3
"""B21 addendum (2026-10-16, reviewer): each refused OTA request read from the WIRE.

Joins an ota_guard_raw_probe run (each row records its client port) to a tshark
capture of the laptop's Wi-Fi, and per request says:
  body_len          the request body's length (184 multipart, or 0)
  wire_403          a device->host TCP segment of that connection carried
                    "HTTP/1.1 403" (the 403 reached the wire)
  device_end        how the DEVICE ended the connection: "fin", "rst", or "none"
                    (its first FIN or RST segment; "rst" also if it sent a FIN
                    and then an RST is recorded as "fin+rst")
  client_got_403    from the probe: the client read the 403 status line
The implementor's prediction: RST when the refused request carried body bytes,
FIN when it carried none. Counter-evidence is a body-less refusal ending in RST,
or a FIN close whose 403 is missing from the wire.

    py -3.14 ota_guard_pcap_join.py [--expect=rst-with-body|no-rst] <probe run .json> <capture .pcapng> [...]

--expect=no-rst (2026-10-16, c6fdab5): the fixed build's expectation instead, every
refusal 403 on the wire, read by the client, and closed with FIN whatever the body.
Each row also records first_close (which side sent the first FIN or RST) and
wire_status (the status code of the device's first HTTP response segment).
Prints the table and writes <probe run>_wire.json. Tested by test_ota_guard_probe.py.
"""
import json
import subprocess
import sys

TSHARK = r"C:\Program Files\Wireshark\tshark.exe"
DEVICE = "192.168.80.1"
FIELDS = ("tcp.stream", "ip.src", "tcp.srcport", "tcp.dstport", "tcp.flags.fin",
          "tcp.flags.reset", "tcp.len", "tcp.payload")


def read_pcap(path):
    """[(stream, src, sport, dport, fin, rst, len, payload_hex)] for port-80 traffic."""
    cmd = [TSHARK, "-r", path, "-Y", "tcp.port==80", "-T", "fields"]
    for f in FIELDS:
        cmd += ["-e", f]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        p = line.split("\t")
        p += [""] * (len(FIELDS) - len(p))
        flag = lambda v: v in ("1", "True", "true")
        rows.append((int(p[0]), p[1], int(p[2]), int(p[3]), flag(p[4]), flag(p[5]),
                     int(p[6] or 0), p[7].replace(":", "")))
    return rows


def per_connection(segments, device=DEVICE):
    """client port -> {"wire_403", "device_end", "device_segments"} from the segments."""
    out = {}
    for stream, src, sport, dport, fin, rst, ln, payload in segments:
        if src == device:
            client = dport
        else:
            client = sport
        c = out.setdefault(client, {"wire_403": False, "device_end": "none", "device_segments": 0,
                                    "client_rst": False, "first_close": None, "wire_status": None})
        if (fin or rst) and c["first_close"] is None:
            # who ended the connection first, in capture order (c6fdab5 holds a
            # refused connection open until the client closes)
            c["first_close"] = "device" if src == device else "client"
        if src == device:
            c["device_segments"] += 1
            if payload and bytes.fromhex(payload).startswith(b"HTTP/1.1 403"):
                c["wire_403"] = True
            if payload and c["wire_status"] is None and bytes.fromhex(payload).startswith(b"HTTP/1.1 "):
                c["wire_status"] = bytes.fromhex(payload)[9:12].decode("latin-1")
            if fin and c["device_end"] == "none":
                c["device_end"] = "fin"
            if rst:
                c["device_end"] = "rst" if c["device_end"] == "none" else (
                    "fin+rst" if c["device_end"] == "fin" else c["device_end"])
        elif rst:
            c["client_rst"] = True
    return out


def join(probe_rows, conns):
    rows = []
    for r in probe_rows:
        port = r.get("local_port")
        c = conns.get(port)
        rows.append({"mode": r.get("mode"), "rep": r.get("rep"), "port": port,
                     "body_len": r.get("body_len"),
                     "wire_403": None if c is None else c["wire_403"],
                     "device_end": None if c is None else c["device_end"],
                     "first_close": None if c is None else c["first_close"],
                     "wire_status": None if c is None else c["wire_status"],
                     "client_got_403": "403" in (r.get("status_line") or ""),
                     "client_ending": r.get("ending"),
                     "in_capture": c is not None})
    return rows


def against_prediction(row):
    """'consistent', 'AGAINST: ...', or 'not in capture'."""
    if not row["in_capture"]:
        return "not in capture"
    if row["body_len"] == 0 and row["device_end"] in ("rst", "fin+rst"):
        return "AGAINST: a body-less refusal ended in RST"
    if row["device_end"] == "fin" and not row["wire_403"]:
        return "AGAINST: a FIN close with no 403 on the wire"
    if row["body_len"] and row["device_end"] == "fin":
        return "AGAINST: a refusal with a body ended in FIN"
    return "consistent"


def against_no_rst(row):
    """The fixed build's expectation (c6fdab5: refusals return ESP_OK, the body is
    read off after the reply): the 403 on the wire and read by the client, and the
    device ends with FIN, never RST, whatever the body. 'consistent', 'AGAINST: ...'
    or 'not in capture'."""
    if not row["in_capture"]:
        return "not in capture"
    if row["device_end"] in ("rst", "fin+rst"):
        return "AGAINST: the device ended with RST"
    if row["device_end"] != "fin":
        return "AGAINST: the device never closed (%s)" % row["device_end"]
    if not row["wire_403"]:
        return "AGAINST: no 403 on the wire"
    if not row["client_got_403"]:
        return "AGAINST: the client did not read the 403"
    return "consistent"


EXPECT = {"rst-with-body": against_prediction, "no-rst": against_no_rst}


def main(argv):
    rule = against_prediction
    if argv and argv[0].startswith("--expect="):
        rule = EXPECT[argv[0].split("=", 1)[1]]
        argv = argv[1:]
    probe, pcaps = argv[0], argv[1:]
    d = json.load(open(probe))
    conns = {}
    for p in pcaps:
        conns.update(per_connection(read_pcap(p)))
    rows = join(d["rows"], conns)
    for r in rows:
        r["prediction"] = rule(r)
        print("%-8s rep %s port %-6s body %-7s wire %-4s wire_403 %-5s device_end %-8s first_close %-6s "
              "client_403 %-5s client_end %-22s %s"
              % (r["mode"], r["rep"], r["port"], r["body_len"], r["wire_status"], r["wire_403"], r["device_end"],
                 r["first_close"], r["client_got_403"], r["client_ending"], r["prediction"]))
    out = probe[:-5] + "_wire.json"
    json.dump({"probe": probe, "captures": pcaps, "rows": rows}, open(out, "w"), indent=1)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
