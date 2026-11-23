#!/usr/bin/env python3
"""shared/wican W15 on the bench: the firmware version the device shows is the app
descriptor's version string as it is. Gen-inhibit tester, 2026-11-23, stage (b) batch 1.

W15 (shared/main 8e16cd74): "The status page's fw_version and the mDNS firmware-version
entry (stock TXT key `fimrware`, spelt as stock and kept) show the app descriptor's
version string as it is ... Nothing is parsed out of it." The interposer reviewer asked
for the whole string, not a prefix, so the judge is exact equality with the descriptor's
version read from the flashed image file by image_elf_sha.py (our own parser): a prefix,
a truncation, "0.00", an empty string or a missing entry each FAIL.

  mDNS half   a unicast DNS query to the device's port 5353 from an ephemeral port (RFC
              6762 6.7, a legacy unicast query, answered by unicast), over the host's route
              to the device. It asks for the DNS-SD service list, each type's instances, and
              each instance's TXT record, and takes every TXT entry whose key is `fimrware`.
              Exactly one value, equal to the descriptor's version: PASS. No answer at all is
              NO ANSWER, never a pass.
  page half   NOT RUN: the status page is /check_status, which also returns stored
              passwords and has been barred since 2026-10-20 until the WiCAN's factory reset
              (asked of the gen-inhibit reviewer, 2026-11-23). Nothing here requests it.

Before either, the device must run the image given: /gen_inhibit's elf_sha256 equals the
image file's (else refused, exit 2). /gen_inhibit's git_version is recorded beside.

    python3.13 -B wican_fw_version_check.py --image <flashed .bin> [--host 192.168.80.1]
        [--out <record.json>]
Exit 0 PASS, 1 FAIL or NO ANSWER, 2 refused. Prints and writes no other field of the page.
Tested by test_wican_fw_version_check.py (a local responder; no device).
"""
import argparse
import json
import os
import random
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import image_elf_sha as IMG   # noqa: E402

HOST = "192.168.80.1"
MDNS_PORT = 5353
KEY = "fimrware"                      # W15: the stock TXT key, spelt as stock
SERVICES = "_services._dns-sd._udp.local"
T_PTR, T_TXT = 12, 16
QU = 0x8000                           # ask for a unicast answer as well


class Refused(Exception):
    pass


# ------------------------------------------------------------------ DNS wire --

def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError("bad label %r" % label)
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def build_query(name, qtype, qid=None):
    qid = random.randrange(1, 0xFFFF) if qid is None else qid
    return struct.pack(">HHHHHH", qid, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack(">HH", qtype, 1 | QU)


def read_name(msg, off, depth=0):
    """(name, offset after it), following compression pointers."""
    if depth > 20:
        raise ValueError("compression loop")
    labels = []
    while True:
        if off >= len(msg):
            raise ValueError("name runs past the message")
        n = msg[off]
        if n == 0:
            return ".".join(labels), off + 1
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(msg):
                raise ValueError("pointer runs past the message")
            ptr = ((n & 0x3F) << 8) | msg[off + 1]
            rest, _ = read_name(msg, ptr, depth + 1)
            labels.append(rest)
            return ".".join(x for x in labels if x), off + 2
        labels.append(msg[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n


def parse(msg):
    """[{name, type, data, ptr}] of every answer, authority and additional record; ptr is a
    PTR record's target (read from the whole message, for compression), else absent."""
    rrs = parse_records(msg)
    for r in rrs:
        if r["type"] == T_PTR:
            try:
                r["ptr"], _ = read_name(msg, r["rdata_off"])
            except ValueError:
                r["ptr"] = None
    return rrs


def parse_records(msg):
    """[{name, type, data, rdata_off}] of every answer, authority and additional record."""
    if len(msg) < 12:
        raise ValueError("short message")
    _, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", msg[:12])
    off = 12
    for _ in range(qd):
        _, off = read_name(msg, off)
        off += 4
    rrs = []
    for _ in range(an + ns + ar):
        name, off = read_name(msg, off)
        rtype, _, _, rdlen = struct.unpack(">HHIH", msg[off:off + 10])
        off += 10
        if off + rdlen > len(msg):
            raise ValueError("record runs past the message")
        rrs.append({"name": name.lower(), "type": rtype, "data": msg[off:off + rdlen], "rdata_off": off})
        off += rdlen
    return rrs


def txt_entries(data):
    """[(key, value or None)] of a TXT record's strings, the key as sent."""
    out, i = [], 0
    while i < len(data):
        n = data[i]
        s = data[i + 1:i + 1 + n].decode("utf-8", "replace")
        i += 1 + n
        if not s:
            continue
        k, eq, v = s.partition("=")
        out.append((k, v if eq else None))
    return out


# ------------------------------------------------------------------ querying --

def ask(host, name, qtype, port=MDNS_PORT, wait=1.5, sock_factory=None):
    """Every record in every reply to one unicast query, within `wait` s."""
    s = (sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)))()
    try:
        s.bind(("0.0.0.0", 0))
        s.settimeout(0.3)
        s.sendto(build_query(name, qtype), (host, port))
        end = time.time() + wait
        msgs = []
        while time.time() < end:
            try:
                data, _ = s.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                msgs.append(parse(data))
            except ValueError:
                continue
        return msgs
    finally:
        s.close()


def discover(host, port=MDNS_PORT, wait=1.5):
    """{replies, types, instances, txt: {instance: [(key, value)]}} from DNS-SD."""
    replies = 0
    rrs = []
    for m in ask(host, SERVICES, T_PTR, port, wait):
        replies += 1
        rrs += m
    types = sorted({read_ptr(r) for r in rrs if r["type"] == T_PTR and r["name"] == SERVICES} - {None})
    instances = set()
    for t in types:
        for m in ask(host, t, T_PTR, port, wait):
            replies += 1
            rrs += m
        instances |= {read_ptr(r) for r in rrs if r["type"] == T_PTR and r["name"] == t.lower()}
    instances.discard(None)
    txt = {}
    for inst in sorted(instances):
        if not any(r["type"] == T_TXT and r["name"] == inst.lower() for r in rrs):
            for m in ask(host, inst, T_TXT, port, wait):
                replies += 1
                rrs += m
        for r in rrs:
            if r["type"] == T_TXT and r["name"] == inst.lower():
                txt.setdefault(inst, [])
                for e in txt_entries(r["data"]):
                    if e not in txt[inst]:
                        txt[inst].append(e)
    return {"replies": replies, "types": types, "instances": sorted(instances), "txt": txt}


def read_ptr(r):
    return r.get("ptr")


# -------------------------------------------------------------------- judging --

def judge(desc_version, found):
    """(verdict, detail). found: discover()'s result, or None if the query never ran."""
    if found is None or found["replies"] == 0:
        return "NO ANSWER", "no mDNS reply to a unicast query"
    vals = [(inst, v) for inst, ents in sorted(found["txt"].items()) for k, v in ents if k == KEY]
    if not vals:
        keys = sorted({k for ents in found["txt"].values() for k, _ in ents})
        return "FAIL", "no TXT entry %r in %d instance(s); keys seen %s" % (KEY, len(found["txt"]), keys)
    distinct = sorted({v for _, v in vals}, key=lambda x: (x is None, x))
    if len(distinct) != 1:
        return "FAIL", "%r has %d different values: %s" % (KEY, len(distinct), distinct)
    v = distinct[0]
    if v is None:
        return "FAIL", "%r is present with no value (no '=')" % KEY
    if v == desc_version:
        return "PASS", "%s=%r equals the descriptor's version, in %d instance(s)" % (KEY, v, len(vals))
    if v and desc_version.startswith(v):
        how = "a prefix of"
    elif v and v in desc_version:
        how = "a part of"
    else:
        how = "not"
    return "FAIL", "%s=%r is %s the descriptor's version %r" % (KEY, v, how, desc_version)


def device_page(host):
    """/gen_inhibit's elf_sha256 and git_version, nothing else kept."""
    import urllib.request
    with urllib.request.urlopen("http://%s/gen_inhibit" % host, timeout=10) as r:
        page = json.loads(r.read().decode("utf-8", "replace"))
    return {"elf_sha256": page.get("elf_sha256"), "git_version": page.get("git_version")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", required=True)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=MDNS_PORT)
    ap.add_argument("--out")
    ap.add_argument("--no-page", action="store_true", help="tests only: skip the /gen_inhibit identity read")
    a = ap.parse_args(argv)
    try:
        desc = IMG.read_file(a.image)
    except (OSError, ValueError) as e:
        print("REFUSED: the image: %s" % e)
        return 2
    rec = {"image": os.path.basename(a.image), "image_elf_sha256": desc["elf_sha256"],
           "descriptor_version": desc["version"], "host": a.host, "key": KEY,
           "page_half": "NOT RUN: /check_status barred since 2026-10-20 until the factory reset (asked 2026-11-23)"}
    if not a.no_page:
        try:
            pg = device_page(a.host)
        except Exception as e:      # noqa: BLE001  any failure to read is a refusal
            print("REFUSED: /gen_inhibit: %s" % e)
            return 2
        rec["page_gen_inhibit"] = pg
        if pg.get("elf_sha256") != desc["elf_sha256"]:
            print("REFUSED: the device runs %s..., the image is %s..."
                  % (str(pg.get("elf_sha256"))[:8], desc["elf_sha256"][:8]))
            return 2
    t0 = time.time()
    found = discover(a.host, a.port)
    rec.update(epoch_query=t0, mdns=found)
    verdict, detail = judge(desc["version"], found)
    rec.update(verdict=verdict, detail=detail)
    print("descriptor version: %r" % desc["version"])
    if "page_gen_inhibit" in rec:
        print("/gen_inhibit git_version: %r (recorded, not judged)" % rec["page_gen_inhibit"].get("git_version"))
    print("mDNS: %d replies, types %s, instances %s" % (found["replies"], found["types"], found["instances"]))
    print("W15 mDNS half: %s -- %s" % (verdict, detail))
    print("W15 page half: %s" % rec["page_half"])
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, indent=1, default=lambda b: b.hex() if isinstance(b, bytes) else str(b))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
