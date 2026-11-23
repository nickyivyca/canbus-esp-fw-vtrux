#!/usr/bin/env python3
"""Tests wican_fw_version_check.py (shared/wican W15, the mDNS half). No device: a local UDP
responder on 127.0.0.1 answers DNS-SD queries from a planted zone, with compressed names,
and a synthetic app image carries the descriptor version. Gen-inhibit tester, 2026-11-23.

Cases (the interposer reviewer asked for the whole string, so only exact equality passes):
  exact value                         PASS
  a prefix (8 of the version's chars) FAIL, named a prefix
  "0.00", "" and no '='               FAIL
  the key spelt fw_version only       FAIL (W15's key is the stock `fimrware`)
  two instances, different values     FAIL
  the value in a second instance only PASS (every instance is read)
  TXT only by its own query           PASS (not only from the additional section)
  no responder                        NO ANSWER, never a pass
  main() end to end, --no-page        exit 0 on exact, 1 on a prefix, 2 on a non-image
  a multicast or broadcast host       refused before a socket is made; main exits 2
--mutants: copies with the judge taking a prefix, taking any key containing "firmware" or
"version", taking NO ANSWER as a pass, reading only the first instance, dropping
compression, and not refusing a multicast host; each must fail a case.

    python3.13 -B test_wican_fw_version_check.py [--mutants]
"""
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TARGET = os.environ.get("W15_TARGET") or os.path.join(HERE, "wican_fw_version_check.py")
import importlib.util   # noqa: E402
_spec = importlib.util.spec_from_file_location("w15", TARGET)
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

FAILS = []
VERSION = "0a740ce6"


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:180]))
    if not ok:
        FAILS.append(label)


# ------------------------------------------------------------- the responder --

def name_ptr(off):
    return struct.pack(">H", 0xC000 | off)


class Zone:
    """instances: [(instance label, [txt strings])] under _wican._tcp.local; txt_in_additional:
    whether a PTR reply for the type carries the TXT records too."""
    def __init__(self, instances, txt_in_additional=True):
        self.instances = instances
        self.add = txt_in_additional

    def reply(self, query):
        qid = struct.unpack(">H", query[:2])[0]
        qname, off = W.read_name(query, 12)
        qtype = struct.unpack(">H", query[off:off + 2])[0]
        qname = qname.lower()
        # The question, then records whose names point back into it (compression).
        q = W.encode_name(qname) + struct.pack(">HH", qtype, 1)
        body = b""
        an = ar = 0
        rr = lambda name, t, data: name + struct.pack(">HHIH", t, 1, 120, len(data)) + data
        if qname == W.SERVICES and qtype == W.T_PTR:
            # target "_wican._tcp" + pointer to "local" inside the question's name
            local_off = 12 + len(W.encode_name(qname)) - len(W.encode_name("local"))
            body += rr(name_ptr(12), W.T_PTR, b"\x06_wican\x04_tcp" + name_ptr(local_off))
            an = 1
        elif qname == "_wican._tcp.local" and qtype == W.T_PTR:
            for label, txt in self.instances:
                body += rr(name_ptr(12), W.T_PTR, bytes([len(label)]) + label.encode() + name_ptr(12))
                an += 1
            if self.add:
                for label, txt in self.instances:
                    body += rr(W.encode_name(label + "._wican._tcp.local"), W.T_TXT, txt_data(txt))
                    ar += 1
        elif qtype == W.T_TXT:
            for label, txt in self.instances:
                if qname == (label + "._wican._tcp.local").lower():
                    body += rr(name_ptr(12), W.T_TXT, txt_data(txt))
                    an += 1
        hdr = struct.pack(">HHHHHH", qid, 0x8400, 1, an, 0, ar)
        return hdr + q + body


def txt_data(strings):
    return b"".join(bytes([len(s.encode())]) + s.encode() for s in strings)


def serve(zone):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.2)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                data, peer = s.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                s.sendto(zone.reply(data), peer)
            except Exception:        # noqa: BLE001  a responder bug shows as no answer
                pass
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return s.getsockname()[1], lambda: (stop.set(), t.join(), s.close())


def found_for(zone, wait=0.6):
    port, close = serve(zone)
    try:
        return W.discover("127.0.0.1", port, wait=wait)
    finally:
        close()


def image(version):
    d = bytearray(0x200)
    d[0] = 0xE9
    d[0x20:0x24] = (0xABCD5432).to_bytes(4, "little")
    v = version.encode()[:31]
    d[0x30:0x30 + len(v)] = v
    return bytes(d)


def cases():
    inst = "wican_instance"
    for label, txts, want, why in [
        ("exact", ["fimrware=" + VERSION, "hw=WiCAN"], "PASS", None),
        ("prefix", ["fimrware=" + VERSION[:4]], "FAIL", "prefix"),
        ("0.00", ["fimrware=0.00"], "FAIL", None),
        ("empty", ["fimrware="], "FAIL", None),
        ("no =", ["fimrware"], "FAIL", "no value"),
        ("key fw_version only", ["fw_version=" + VERSION], "FAIL", "no TXT entry"),
    ]:
        f = found_for(Zone([(inst, txts)]))
        v, detail = W.judge(VERSION, f)
        check("%s: %s" % (label, want), v == want, (v, detail))
        if why:
            check("%s: the detail says %s" % (label, why), why in detail, detail)
    f = found_for(Zone([("a1", ["fimrware=" + VERSION]), ("a2", ["fimrware=" + VERSION[:6]])]))
    v, d = W.judge(VERSION, f)
    check("two instances, different values: FAIL", v == "FAIL" and "different" in d, (v, d))
    f = found_for(Zone([("a1", ["other=1"]), ("a2", ["fimrware=" + VERSION])]))
    v, d = W.judge(VERSION, f)
    check("value in the second instance: PASS", v == "PASS", (v, d))
    f = found_for(Zone([(inst, ["fimrware=" + VERSION])], txt_in_additional=False))
    v, d = W.judge(VERSION, f)
    check("TXT only by its own query: PASS", v == "PASS", (v, d))
    # nobody listening: a bound socket we never read
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    try:
        f = W.discover("127.0.0.1", s.getsockname()[1], wait=0.5)
    finally:
        s.close()
    v, d = W.judge(VERSION, f)
    check("no responder: NO ANSWER", v == "NO ANSWER", (v, d))
    check("judge(None): NO ANSWER", W.judge(VERSION, None)[0] == "NO ANSWER")

    # unicast only: a group or broadcast destination is refused before any socket is made
    for dest in ("224.0.0.251", "239.0.2.1", "255.255.255.255"):
        made = []
        try:
            W.ask(dest, W.SERVICES, W.T_PTR, wait=0.1, sock_factory=lambda: made.append(1) or None)
            refused = False
        except W.Refused:
            refused = True
        except Exception:            # noqa: BLE001  a socket call on None: it got past the guard
            refused = False
        check("ask(%s): refused, no socket made" % dest, refused and not made, (refused, made))
    check("ask(127.0.0.1) is not refused", W.unicast_only("127.0.0.1") == "127.0.0.1")

    # main(), end to end, with a synthetic image
    work = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scratch", "w15-test",
                        str(os.getpid()))
    os.makedirs(work, exist_ok=True)
    try:
        for label, v_img, v_dev, want in [("exact", VERSION, VERSION, 0), ("prefix", VERSION, VERSION[:5], 1)]:
            p = os.path.join(work, "img.bin")
            with open(p, "wb") as fh:
                fh.write(image(v_img))
            port, close = serve(Zone([(inst, ["fimrware=" + v_dev])]))
            try:
                rc = W.main(["--image", p, "--host", "127.0.0.1", "--port", str(port), "--no-page",
                             "--out", os.path.join(work, "rec.json")])
            finally:
                close()
            check("main %s: exit %d" % (label, want), rc == want, rc)
        bad = os.path.join(work, "bad.bin")
        with open(bad, "wb") as fh:
            fh.write(b"\0" * 300)
        check("main on a non-image: exit 2", W.main(["--image", bad, "--no-page"]) == 2)
        p = os.path.join(work, "img.bin")
        check("main --host 224.0.0.251: exit 2",
              W.main(["--image", p, "--host", "224.0.0.251", "--no-page"]) == 2)
    finally:
        shutil.rmtree(work)


MUTANTS = [
    ("judge takes a prefix", "    if v == desc_version:", "    if desc_version.startswith(v):"),
    ("any version-ish key", "for k, v in ents if k == KEY]", "for k, v in ents if 'fimrware' in k or 'version' in k]"),
    ("NO ANSWER passes", '        return "NO ANSWER", "no mDNS reply to a unicast query"',
     '        return "PASS", "no mDNS reply to a unicast query"'),
    ("first instance only", "    for inst in sorted(instances):", "    for inst in sorted(instances)[:1]:"),
    ("no compression", "        if n & 0xC0 == 0xC0:", "        if False:"),
    ("multicast not refused", '    if ip.is_multicast or ip == ipaddress.ip_address("255.255.255.255"):',
     '    if ip == ipaddress.ip_address("255.255.255.255"):'),
]


def mutants():
    with open(TARGET, encoding="utf-8") as f:
        text = f.read()
    work = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scratch", "w15-test",
                        "m%d" % os.getpid())
    os.makedirs(work, exist_ok=True)
    killed = 0
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        ctl = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True, env=env)
        check("control: the unmutated probe passes", ctl.returncode == 0, ctl.stdout[-200:])
        for name, old, new in MUTANTS:
            if text.count(old) != 1:
                check("mutant %s: text found once" % name, False, text.count(old))
                continue
            p = os.path.join(work, "w15_%d.py" % MUTANTS.index((name, old, new)))
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text.replace(old, new))
            r = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True,
                               env=dict(env, W15_TARGET=p), timeout=300)
            dead = r.returncode == 1 and "\nFAIL  " in "\n" + r.stdout and "Traceback" not in r.stderr
            killed += dead
            check("mutant %s: killed by a check" % name, dead, (r.stdout[-150:], r.stderr[-150:]))
    finally:
        shutil.rmtree(work)
    print("%d of %d mutants killed" % (killed, len(MUTANTS)))


if __name__ == "__main__":
    if "--mutants" in sys.argv[1:]:
        mutants()
    else:
        cases()
    print("%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    sys.exit(1 if FAILS else 0)
