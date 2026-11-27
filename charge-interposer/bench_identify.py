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
  3. SLCAN (shared/x2_test_images' slcan_can1, and gen-inhibit's X2 SLCAN,
     which send nothing on CAN until a host opens a channel): a bare CR to
     clear its line buffer, then "V" and "N", answered "V0101" and "NF001".
     Identity: "slcan:V0101:NF001". These report no build id, so this names
     the protocol, not the build; the build is the verified write of its
     bin, whose own application descriptor names it. slcan_can1 moved there
     from the interposer's slcan variant, which answered the same and is
     gone since 2026-11-13 (spec 8.2, tracker H).
     The same reads name shared/x2_test_images' slcan_spi by its build
     (slcan_spi/README.md, "The protocol"): "V" and "N" answered
     "V0100 <identity>" and "N<identity>", the identity
     "<name> <version> elf=<64 hex>" with a name ending _NOT_FOR_VEHICLE
     (slcan_spi_NOT_FOR_VEHICLE, or a test variant's). Identity:
     "<name>:<version>:<elf sha256>", only when V and N give the same one.
     It is test equipment, never put on the truck (SPEC T3a, T14), so it is
     never read as the product or as an image of the bench suite: the
     runner flashes over it.

2 and 3 are tried only when the ACKed boot ran, saw no 0x7F7 at all, and
the board's serial output during it carried none of the product's lines,
so a diag image is never sent HELLO? or V. If the boot capture itself
cannot run, identify RAISES: the runner then stops rather than flash a
board whose state nobody read. Returns None when nothing answers.

Settings used: serial_port, wican_state, hosts.local.python and hosts.local.env (the boot
capture imports the clone's run_scenario). bench_powerup_round.py is the
one in this tree's bench_drivers/ (port step 0, 2026-10-26; it was in
reverse-it's interposer-firmware/ folder before). The key interposer_dbc is
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
# slcan_spi's identity, the whole answer before its CR (docstring, 3)
_SPI_ID = rb"(slcan_spi\w*_NOT_FOR_VEHICLE) (\S+) elf=([0-9a-f]{64})\r"
SPI_V_RE = re.compile(rb"(?:^|[\r\n\x07])V0100 " + _SPI_ID)
SPI_N_RE = re.compile(rb"(?:^|[\r\n\x07])N" + _SPI_ID)
# The product's serial lines as seen on the bench, matched on any line
# (re.M): the 1 Hz diag line, its fw/schema tail, the mode lines and the
# loss summary. 2026-10-18: without re.M, "^diag st=" matched only at the
# very start of a reply, and the loss summary matched nothing (the 00:30
# trial's two HELLO? replies, now in bench_suite_test). The diag line's tail
# is `build=<8 hex> schema=<n>` from schema 5 (spec 8.3, user 2026-11-23)
# and was `fw=<n> schema=<n>` through schema 4; both are the product, since
# this names what a board runs before anything is flashed.
PRODUCT_RE = re.compile(r"serial>.*\b(MODE|spec 2\.3)|^diag st="
                        r"|\b(?:build=[0-9a-f]{8}|fw=\d+) schema=\d+"
                        r"|\bLOSS veh\(|bridged frames that missed the vehicle",
                        re.M)


# Spec 8.3 at schema 5 (user, 2026-11-23): the serial diag line's
# build=<8 hex> is the build id "as 0x7F7 carries it, printed as the hash's
# first four bytes in order"
SERIAL_BUILD_RE = re.compile(r"^diag st=\w+ .*\bbuild=([0-9a-f]{8}) "
                             r"schema=(\d+)\s*$", re.M)
SERIAL_DIAG_RE = re.compile(r"^diag st=\w+ .* schema=\d+\s*$", re.M)


def serial_build_check(ids, schemas, serial):
    """-> ([serial build ids], why), why starting "MISMATCH" when the boot's
    serial and its 0x7F7 disagree (spec 8.3, schema 5): a whole diag line's
    build= that is not the 0x7F7 build id, or, with 0x7F7 at schema 5 or
    later, whole diag lines on serial and none with build=. A board that
    prints no diag line (the truck image, spec 8.3) is not judged here;
    identify's expected id (the image's ELF hash) is judged by the runner.
    ids and schemas: the boot's 0x7F7 build ids (8 hex) and schema bytes."""
    sids = [m.group(1) for m in SERIAL_BUILD_RE.finditer(serial or "")]
    if not ids:
        return sids, "no 0x7F7 to compare"
    if any(s != ids[0] for s in sids):
        return sids, ("MISMATCH: serial build= %s, 0x7F7 %s"
                      % (",".join(sorted(set(sids))), ids[0]))
    diag_lines = len(SERIAL_DIAG_RE.findall(serial or ""))
    if not sids and diag_lines and schemas and min(schemas) >= 5:
        return sids, ("MISMATCH: %d serial diag line(s) and no build= in any, "
                      "0x7F7 at schema %d" % (diag_lines, min(schemas)))
    if not sids:
        return sids, "no serial diag line with build="
    return sids, "%d serial build= equal the 0x7F7 id" % len(sids)


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
    drivers = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "bench_drivers")
    out = os.path.join(logdir, "ack_%s" % tag)
    log = out + "_run.txt"
    cmd = _python(settings) + [os.path.join(drivers, "bench_powerup_round.py"),
                               "--port", settings["serial_port"], "--mode", "ack",
                               "--boots", "1", "--wican-state",
                               settings.get("wican_state", "not recorded"),
                               "--out", out]
    with open(log, "w", encoding="utf-8") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                       env=_env(settings), timeout=180,
                       cwd=drivers)
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
    schemas = []
    for wf in witness:
        with open(wf, encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.split()
                if len(p) >= 5 and p[2] == "7F7":
                    d = m.decode(bytes.fromhex(p[4]), decode_choices=False)
                    ids.append("%08x" % d["intp_build_id"])
                    schemas.append(int(d["intp_schema_ver_build"]))
    product = bool(PRODUCT_RE.search(text))
    # the same boot's serial (spec 8.3, schema 5: build= is the 0x7F7 id)
    serial = ""
    for sf in sorted(glob.glob(os.path.join(out, "serial_ack_0*.log"))):
        with open(sf, encoding="utf-8", errors="replace") as f:
            serial += f.read()
    sids, why = serial_build_check(ids, schemas, serial)
    with open(os.path.join(logdir, "identify_%s.txt" % tag), "w") as f:
        f.write("0x7F7 build ids read: %s; product serial lines: %s; serial "
                "build= ids: %s (%s)\n" % (ids or "none", product,
                                            sids or "none", why))
    if not ids:
        return None, product
    if len(set(ids)) != 1:
        return "inconsistent:" + ",".join(sorted(set(ids))), product
    if why.startswith("MISMATCH"):
        return "inconsistent:0x7F7 %s, %s" % (ids[0], why), product
    return ids[0], product


def _serial(settings):
    import serial_open
    s = serial_open.open_quiet(settings["serial_port"], 115200, 0.2)
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
    v = SPI_V_RE.search(got.get("V") or b"")
    n = SPI_N_RE.search(got.get("N") or b"")
    if v and n:
        if v.groups() == n.groups():
            return ":".join(g.decode() for g in v.groups())
        return None
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
