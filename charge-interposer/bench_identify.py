"""Which image is the X2 running? The interposer suite's identify hook, also
usable by gen-inhibit's suite for the X2 (its app and slcan images).

Read off the board, never taken from what was last flashed (bench-holder
rule 4). Published identities, tried in this order:

  1. 0x7F7 (the diag images: bridge, witness, truck). One ACKed boot through
     bench_powerup_round.py --mode ack (PCAN normal, ACKing; the board reset
     over USB only); the 0x7F7 the witness captured is decoded with cantools
     against the git copy of the diag DBC, paths.diag_dbc() (the
     reverse-it copy is not read). Identity: the build id as 8
     lowercase hex digits, e.g. "756d6714".
  2. @HELLO (the standalone test images c12_bench and vport_bench, which
     send no status frames): "HELLO?" on the serial port, answered by
     "@HELLO <name> version=.. elf_sha256=<64 hex> ...". Identity:
     "<name>:<elf sha256>".
  3. SLCAN (the interposer's slcan image, which sends nothing on CAN until
     a host opens a channel): a bare CR to clear its line buffer, then "V"
     and "N", answered "V0101" and "NF001". Identity: "slcan:V0101:NF001".
     SLCAN reports no build id, so this names the protocol, not the build;
     the build is the verified write of its manifest-matched bin.

2 and 3 are tried only when the ACKed boot ran, saw no 0x7F7 at all, and
the board's serial output during it carried none of the product's lines,
so a diag image is never sent HELLO? or V. If the boot capture itself
cannot run, identify RAISES: the runner then stops rather than flash a
board whose state nobody read. Returns None when nothing answers.

Settings used: interposer_artifacts (holding bench_powerup_round.py),
serial_port, wican_state, hosts.local.python and hosts.local.env (the boot
capture imports the clone's run_scenario). The key interposer_dbc is
retired (2026-10-24): settings that still carry it are refused, before
anything runs, so a value nobody reads cannot look like it is in force.
"""

import glob
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

HELLO_RE = re.compile(r"^@HELLO (\S+) .*?elf_sha256=([0-9a-f]{64})")
# The product's serial lines as seen on the bench, matched on any line
# (re.M): the 1 Hz diag line, its fw/schema tail, the mode lines and the
# loss summary. 2026-10-18: without re.M, "^diag st=" matched only at the
# very start of a reply, and the loss summary matched nothing (the 00:30
# trial's two HELLO? replies, now in bench_suite_test).
PRODUCT_RE = re.compile(r"serial>.*\b(MODE|spec 2\.3)|^diag st=|\bfw=\d+ schema=\d+"
                        r"|\bLOSS veh\(|bridged frames that missed the vehicle",
                        re.M)


class IdentifyError(RuntimeError):
    pass


def _python(settings):
    py = ((settings.get("hosts") or {}).get("local") or {}).get("python")
    return list(py) if py else [sys.executable]


def _env(settings):
    env = dict(os.environ)
    env.update(((settings.get("hosts") or {}).get("local") or {}).get("env") or {})
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONDONTWRITEBYTECODE="1")
    return env


def read_7f7(settings, logdir, tag):
    """One ACKed boot. Returns (build id or None, product serial seen)."""
    import cantools
    art = settings["interposer_artifacts"]
    out = os.path.join(logdir, "ack_%s" % tag)
    log = out + "_run.txt"
    cmd = _python(settings) + [os.path.join(art, "bench_powerup_round.py"),
                               "--port", settings["serial_port"], "--mode", "ack",
                               "--boots", "1", "--wican-state",
                               settings.get("wican_state", "not recorded"),
                               "--out", out]
    with open(log, "w", encoding="utf-8") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                       env=_env(settings), timeout=180,
                       cwd=settings.get("reverse_it") or art)
    with open(log, encoding="utf-8", errors="replace") as f:
        text = f.read()
    witness = sorted(glob.glob(os.path.join(out, "witness_ack_0*.txt")))
    if not witness or "Traceback (most recent call last)" in text:
        raise IdentifyError("the ACKed-boot capture did not run (%s): see %s"
                            % (text.strip().splitlines()[-1][:160] if text.strip()
                               else "no output", log))
    db = cantools.database.load_file(paths.diag_dbc())
    m = db.get_message_by_frame_id(0x7F7)
    ids = []
    for wf in witness:
        with open(wf, encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.split()
                if len(p) >= 5 and p[2] == "7F7":
                    d = m.decode(bytes.fromhex(p[4]))
                    ids.append("%08x" % d["intp_build_id"])
    product = bool(PRODUCT_RE.search(text))
    with open(os.path.join(logdir, "identify_%s.txt" % tag), "w") as f:
        f.write("0x7F7 build ids read: %s; product serial lines: %s\n"
                % (ids or "none", product))
    if not ids:
        return None, product
    if len(set(ids)) != 1:
        return "inconsistent:" + ",".join(sorted(set(ids))), product
    return ids[0], product


def _serial(settings):
    import serial
    s = serial.Serial()
    s.port = settings["serial_port"]
    s.baudrate = 115200
    s.timeout = 0.2
    s.dtr = False
    s.rts = False
    s.open()
    time.sleep(0.5)
    s.reset_input_buffer()
    return s


def read_hello(settings, logdir, tag, wait_s=5.0):
    """Returns (identity or None, product lines seen in the reply)."""
    s = _serial(settings)
    buf = b""
    try:
        s.write(b"HELLO?\n")
        s.flush()
        t_end = time.monotonic() + wait_s
        while time.monotonic() < t_end:
            buf += s.read(4096)
            for ln in buf.decode("utf-8", "replace").splitlines():
                mm = HELLO_RE.match(ln.strip())
                if mm:
                    with open(os.path.join(logdir, "identify_%s_hello.txt"
                                           % tag), "w") as f:
                        f.write(ln.strip() + "\n")
                    return "%s:%s" % (mm.group(1), mm.group(2)), False
    finally:
        s.close()
    text = buf.decode("utf-8", "replace")
    with open(os.path.join(logdir, "identify_%s_hello.txt" % tag), "w") as f:
        f.write("no @HELLO; read %r\n" % buf[-300:])
    return None, bool(PRODUCT_RE.search(text))


def read_slcan(settings, logdir, tag):
    s = _serial(settings)
    got = {}
    try:
        s.write(b"\r")
        s.flush()
        time.sleep(0.3)
        s.read(4096)
        for cmd in (b"V", b"N"):
            s.write(cmd + b"\r")
            s.flush()
            time.sleep(0.4)
            got[cmd.decode()] = s.read(4096)
    finally:
        s.close()
    with open(os.path.join(logdir, "identify_%s_slcan.txt" % tag), "w") as f:
        f.write("V -> %r\nN -> %r\n" % (got.get("V"), got.get("N")))
    v = re.search(rb"V(\d{4})\r", got.get("V") or b"")
    n = re.search(rb"N(\w{4})\r", got.get("N") or b"")
    if v and n:
        return "slcan:V%s:N%s" % (v.group(1).decode(), n.group(1).decode())
    return None


_n = [0]


def identify(image, ctx, logdir, settings):
    if "interposer_dbc" in settings:
        raise IdentifyError(
            "settings carry interposer_dbc, retired on 2026-10-24: the diag "
            "DBC is the git copy beside this file (paths.diag_dbc()). Delete "
            "the key")
    _n[0] += 1
    tag = "%02d_%s" % (_n[0], image.name)
    got, product = read_7f7(settings, logdir, tag)
    if got is not None:
        return got
    if product:
        return "product-without-0x7F7"
    got, product = read_hello(settings, logdir, tag)
    if got is not None:
        return got
    if product:
        return "product-without-0x7F7"
    return read_slcan(settings, logdir, tag)
