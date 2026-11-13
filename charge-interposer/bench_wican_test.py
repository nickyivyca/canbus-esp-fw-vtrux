"""Tests the HARNESS: bench_wican.py, the WiCAN as a board of the bench
suite (tester, 2026-10-23; the USB load 2026-11-12; identify and the hooks
over USB 2026-11-13). No hardware: the serial-device lookup is the pinned
core's own (vtrux_bench.serial_dev) on its made-up /dev/serial/by-id/ and
sysfs tree (the core's tests/_serial_fixture.py), holding the WiCAN and the
X2, both Espressif USB-Serial-JTAG with the same 303a:1001 and their own
made-up serials; esptool is a fake that answers each port as that board,
its chip with that board's MAC and read_flash and write_flash on that
board's own flash file, laid out as ESP-IDF lays it out (partition table at
0x8000, otadata, app images with their descriptors). The pin is the
committed wican_pin.json with its files made up in the real formats (the
image hash kept). Linux only, as the bench host is (the fixture needs
symbolic links).

  source    the identity is the pin's image hash; wican_image_dir unset or
            not a folder refuses; gen-inhibit's tools are not needed
  params    the load's params: the pinned files at their offsets with their
            md5s, the pin's chip with the WiCAN's MAC, and the WiCAN's own
            device (Linux) or port (Windows) -- never the X2's, and refused
            without one; the core's device_port takes the image's device
            over settings.esptool.device (the precedence)
  layout    the partition table, the otadata entries (CRC, state) and the
            boot slot as ESP-IDF's bootloader picks it, and the descriptor's
            elf_sha256; each malformed part raises
  identify  through the real lookup and the fake esptool: the boot slot's
            hash (ota_0, and ota_1 after an OTA, whatever 0x10000 holds),
            every call on the WiCAN's port and none on the X2's, the record;
            a chip with another MAC, a failed chip_id or read, a short read,
            no WiCAN device setting and a WiCAN not plugged in each RAISE,
            with nothing read past the chip check. The control: the same
            calls given the X2's IDs and MAC read the X2's own image, so the
            fake tells the boards apart
  flasher   through the core's esptool_flash: every call on the WiCAN's
            port, write_flash of the four pinned files at their offsets, and
            identify then reads the pinned image; a changed pinned file, no
            WiCAN device or a chip with another MAC each write nothing
  hooks     one `--after hard_reset chip_id` on the WiCAN's port per step,
            nothing read; a chip with another MAC or no device fails the
            step; no step for the X2
  engine    the real chain runner, a fake X2 beside the WiCAN: on wican_man
            the run passes with no load; on another image identify reads it,
            the pinned image is loaded over USB, identify reads it back and
            the run passes; with no WiCAN device, or the WiCAN's port
            answering with another MAC, nothing is written and the bench
            stops

    python3.13 -B charge-interposer/bench_wican_test.py [--can-fail]

--can-fail runs the tests against each of MUTANTS, a copy of bench_wican.py
with one source edit, and every one must make them fail.
"""

import copy
import hashlib
import json
import os
import struct
import subprocess
import sys
import traceback
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shared_pin                                         # noqa: E402
shared_pin.use_bench()      # the pinned shared/bench (port step 2a)
import scratch_dir                                        # noqa: E402
import wican_pin                                          # noqa: E402

_MOD = os.environ.get("BENCH_WICAN_MODULE")
if _MOD:                    # --can-fail: a mutant stands in for the module
    import importlib.util
    _spec = importlib.util.spec_from_file_location("bench_wican", _MOD)
    W = importlib.util.module_from_spec(_spec)
    sys.modules["bench_wican"] = W
    _spec.loader.exec_module(W)
else:
    import bench_wican as W                               # noqa: E402

from vtrux_bench.run import flash as F                    # noqa: E402
from vtrux_bench import serial_dev as SD                  # noqa: E402

sys.path.insert(0, os.path.join(shared_pin.folder("shared/bench"), "tests"))
from _serial_fixture import FakeSerial                    # noqa: E402

# this run's scratch_dir folder, made by run_tests() and removed in its
# finally (2026-10-31: before, a fixed scratch/ folder never removed)
TMP = None
# the pinned image's identity: the committed pin's image hash
MAN_SHA = wican_pin.load()["image"]["image_hash"]
OTHER_SHA = "b2" * 32
X2_SHA = "c3" * 32
# made-up USB identities and MACs (the repo is public); both boards are
# Espressif USB-Serial-JTAG, the same IDs, three interfaces
ESP = ("303a", "1001")
WICAN_DEV = {"vid": "303a", "pid": "1001", "serial": "TESTWICAN0A", "interface": 0}
X2_DEV = {"vid": "303a", "pid": "1001", "serial": "TESTX2SERIAL0B", "interface": 0}
WICAN_MAC = "02:00:00:00:00:0a"
X2_MAC = "02:00:00:00:00:0b"

# the WiCAN's layout as ESP-IDF writes it (made up, the real formats)
OTADATA_AT, OTA0_AT, OTA1_AT = 0xD000, 0x10000, 0x190000
PARTS = [("nvs", 1, 0x02, 0x9000, 0x4000), ("otadata", 1, 0x00, OTADATA_AT, 0x2000),
         ("phy_init", 1, 0x01, 0xF000, 0x1000),
         ("ota_0", 0, 0x10, OTA0_AT, 0x180000), ("ota_1", 0, 0x11, OTA1_AT, 0x180000)]


def ptable(parts=PARTS, magic=0x50AA):
    out = b""
    for label, typ, sub, off, size in parts:
        out += struct.pack("<HBBII16sI", magic, typ, sub, off, size,
                           label.encode(), 0)
    return out + b"\xff" * (0xC00 - len(out))


def app_image(sha, size=0x400, img_magic=0xE9, desc_magic=0xABCD5432):
    """An app image's start: its header, a segment header, the descriptor
    with this elf_sha256, then filler."""
    head = bytes([img_magic]) + b"\x03\x02\x2f" + b"\x00" * 20
    seg = struct.pack("<II", 0x3C000020, 0x100)
    desc = struct.pack("<II8x", desc_magic, 0) + b"0.0.0".ljust(32, b"\0") + \
        b"made_up".ljust(32, b"\0") + b"\0" * 32 + b"v5.4.2".ljust(32, b"\0") + \
        bytes.fromhex(sha)
    data = head + seg + desc
    return data + b"\x5a" * (size - len(data))


def ota_entry(seq, state=2, crc=None):
    if crc is None:
        crc = zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)
    return struct.pack("<I20sII", seq, b"\xff" * 20, state, crc)


def otadata(*entries):
    """Two sectors; each entry (seq, state[, crc]) or None (erased)."""
    out = b""
    for e in entries:
        ent = b"\xff" * 32 if e is None else ota_entry(*e)
        out += ent + b"\xff" * (0x1000 - 32)
    return out


# esptool v4 as the core flasher and bench_wican read it, per port: the
# board whose by-id link is the --port answers chip_id with its MAC, and
# read_flash / write_flash work on its own flash file. FAKE_ESPTOOL_FAIL
# names a command to fail after doing its work (chip_id's lines printed, a
# full-size read file written), so only its exit says so;
# FAKE_ESPTOOL_SHORT makes a read one byte short.
FAKE_ESPTOOL = '''
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_ESPTOOL_LOG"], "a") as f:
    f.write(json.dumps(a) + "\\n")
with open(os.environ["FAKE_BOARDS"]) as f:
    boards = json.load(f)
port = a[a.index("--port") + 1]
if port not in boards:
    print("A fatal error occurred: Could not open %s" % port)
    sys.exit(2)
b = boards[port]
fail = os.environ.get("FAKE_ESPTOOL_FAIL", "")
print("esptool.py v4.13.0")
if "chip_id" in a:
    print("Chip is %s (QFN32) (revision v0.4)" % b["chip"])
    print("MAC: " + b["mac"])
    if fail == "chip_id":
        sys.exit(2)
elif "read_flash" in a:
    i = a.index("read_flash")
    off, size, out = int(a[i + 1], 16), int(a[i + 2], 16), a[i + 3]
    with open(b["flash"], "rb") as f:
        f.seek(off)
        data = f.read(size)
    data += b"\\xff" * (size - len(data))
    if os.environ.get("FAKE_ESPTOOL_SHORT"):
        data = data[:-1]
    with open(out, "wb") as f:
        f.write(data)
    if fail == "read_flash":
        sys.exit(2)
elif "write_flash" in a:
    files = a[a.index("write_flash") + 1:]
    with open(b["flash"], "r+b") as fl:
        for k in range(0, len(files), 2):
            with open(files[k + 1], "rb") as f:
                data = f.read()
            fl.seek(int(files[k], 16))
            fl.write(data)
            print("Hash of data verified.")
'''


def fake_pin(folder):
    """The committed pin with its four files made up in folder, in the real
    formats (the partition table, an erased otadata, an app image whose
    descriptor holds the pin's image hash), their sizes and hashes the made-up
    bytes'; the image hash and offsets kept."""
    pin = copy.deepcopy(wican_pin.load())
    contents = {"bootloader/bootloader.bin": b"\xe9boot" * 300,
                "partition_table/partition-table.bin": ptable(),
                "ota_data_initial.bin": b"\xff" * 0x2000}
    for f in pin["image"]["files"]:
        data = contents.get(f["name"]) or app_image(MAN_SHA, 0x800)
        path = os.path.join(folder, *f["name"].split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        f.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                 md5=hashlib.md5(data).hexdigest())
    return pin


def flash(root, board, ota0=None, ota1=None, od=(None, None), table=None):
    """Lay out one board's flash file: the table, otadata and the app slots'
    starts (0xff elsewhere in what is read)."""
    path = os.path.join(root, "flash_%s.bin" % board)
    data = bytearray(b"\xff" * (OTA1_AT + 0x1000))
    data[0x8000:0x8C00] = table if table is not None else ptable()
    data[OTADATA_AT:OTADATA_AT + 0x2000] = otadata(*od)
    for at, sha in ((OTA0_AT, ota0), (OTA1_AT, ota1)):
        if sha:
            img = app_image(sha)
            data[at:at + len(img)] = img
    with open(path, "wb") as f:
        f.write(bytes(data))
    return path


def set_board(root, board, **kw):
    """Change one board's entry in the fake's board map (e.g. its mac)."""
    p = os.environ["FAKE_BOARDS"]
    with open(p) as f:
        boards = json.load(f)
    port = root_ports(root)[board]
    boards[port].update(kw)
    with open(p, "w") as f:
        json.dump(boards, f)


def root_ports(root):
    with open(os.path.join(root, "ports.json")) as f:
        return json.load(f)


def fixture(name, base=None, wican_ota0=None):
    """A made-up by-id and sysfs tree with the WiCAN and the X2, each
    board's flash (the WiCAN on wican_ota0, default the pinned image; the X2
    on its own image), a wican_image_dir holding made-up files of the pin
    (pin.json beside it: pin_of(root)), a fake esptool, and the settings
    naming them. -> (settings, root), under base (default TMP). Also used by
    bench_suite_test, with its own folder as base. Install the tree with
    Linux(root) around anything that resolves a port."""
    root = os.path.join(base or TMP, name)
    scratch_dir.remove(root)
    os.makedirs(root)
    fs = FakeSerial(os.path.join(root, "serial"))
    ports = {"wican": fs.add("ttyACM2", *ESP, serial=WICAN_DEV["serial"],
                             interface=0, interfaces=3),
             "x2": fs.add("ttyACM1", *ESP, serial=X2_DEV["serial"],
                          interface=0, interfaces=3)}
    with open(os.path.join(root, "ports.json"), "w") as f:
        json.dump(ports, f)
    boards = {ports["wican"]: {"chip": "ESP32-C3", "mac": WICAN_MAC,
                               "flash": flash(root, "wican", ota0=wican_ota0 or MAN_SHA)},
              ports["x2"]: {"chip": "ESP32-C3", "mac": X2_MAC,
                            "flash": flash(root, "x2", ota0=X2_SHA)}}
    os.environ["FAKE_BOARDS"] = os.path.join(root, "boards.json")
    with open(os.environ["FAKE_BOARDS"], "w") as f:
        json.dump(boards, f)
    images = os.path.join(root, "image")
    pin = fake_pin(images)
    with open(os.path.join(root, "pin.json"), "w") as f:
        json.dump(pin, f)
    esptool = os.path.join(root, "fake_esptool.py")
    with open(esptool, "w") as f:
        f.write(FAKE_ESPTOOL)
    os.environ["FAKE_ESPTOOL_LOG"] = os.path.join(root, "esptool.jsonl")
    for k in ("FAKE_ESPTOOL_FAIL", "FAKE_ESPTOOL_SHORT"):
        os.environ.pop(k, None)
    return {"wican_image_dir": images,
            "wican_esptool_device": dict(WICAN_DEV),
            "wican_mac": WICAN_MAC,
            "esptool": {"argv": [sys.executable, "-B", esptool],
                        "device": dict(X2_DEV), "baud": 921600},
            "hosts": {"local": {"python": [sys.executable]}}}, root


class Linux:
    """The core on Linux, its serial-device lookup on the fixture's tree."""

    def __init__(self, root):
        self.fs = FakeSerial(os.path.join(root, "serial"))

    def __enter__(self):
        self.env = F.this_env
        F.this_env = lambda: "linux"
        self.fs.install(SD)
        return self

    def __exit__(self, *a):
        F.this_env = self.env
        self.fs.uninstall()


def pin_of(root):
    with open(os.path.join(root, "pin.json")) as f:
        return json.load(f)


def esptool_calls(root):
    p = os.path.join(root, "esptool.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def ports_of(calls):
    return [a[a.index("--port") + 1] for a in calls]


def refused(settings, *frags, pin=None):
    try:
        W.make(settings, pin)
    except W.WicanSourceError as e:
        for fr in frags:
            assert fr in str(e), (frags, str(e))
        return
    raise AssertionError("not refused: %s" % (frags,))


def raises(fn, *frags):
    try:
        fn()
    except W.IdentifyError as e:
        for fr in frags:
            assert fr in str(e), (frags, str(e))
        return str(e)
    raise AssertionError("did not raise IdentifyError: %s" % (frags,))


# ---- source ---------------------------------------------------------------
def test_source():
    s, root = fixture("source")
    pin = pin_of(root)
    w = W.make(s, pin)
    im = w["image"]
    assert (im.name, im.board, im.flasher, im.expect) == (
        "wican_man", "wican", "wican-esptool", MAN_SHA), im
    assert im.params == {"pin": pin["pin"]}, im.params
    assert "gi_tools" not in s
    assert W.make(s)["image"].expect == MAN_SHA     # the committed pin
    refused(dict(s, wican_image_dir=""), "settings.wican_image_dir is not set", pin=pin)
    refused(dict(s, wican_image_dir=os.path.join(root, "nope")), "is not a folder",
            pin=pin)


# ---- params and the device's precedence ---------------------------------
def _params_refused(pin, folder, settings, env, frag):
    try:
        W.wican_params(pin, folder, settings, env)
    except W.WicanSourceError as e:
        assert frag in str(e), (frag, str(e))
        return
    raise AssertionError("params not refused: %s" % frag)


def test_params():
    s, root = fixture("params")
    pin, folder = pin_of(root), s["wican_image_dir"]
    p = W.wican_params(pin, folder, s, "linux")
    want_files = [[f["offset"], os.path.join(folder, *f["name"].split("/"))]
                  for f in pin["image"]["files"]]
    assert p["files"] == want_files, p["files"]
    assert [x[0] for x in p["files"]] == ["0x0", "0x8000", "0xd000", "0x10000"], p
    assert p["md5"] == {path: f["md5"] for (_o, path), f in
                        zip(want_files, pin["image"]["files"])}, p["md5"]
    assert p["chip"] == {"type": "ESP32-C3", "mac": WICAN_MAC}, p["chip"]
    assert p["device"] == WICAN_DEV and "port" not in p, p
    pw = W.wican_params(pin, folder, dict(s, wican_esptool_port="COM9",
                                          esptool=dict(s["esptool"], port="COM3")), "windows")
    assert pw["port"] == "COM9" and "device" not in pw, pw
    _params_refused(pin, folder, dict(s, wican_esptool_device=None), "linux",
                    "wican_esptool_device")
    _params_refused(pin, folder, dict(s, wican_esptool_device={"vid": "303a"}), "linux",
                    "wican_esptool_device")
    _params_refused(pin, folder, dict(s, esptool=dict(s["esptool"], port="COM3")),
                    "windows", "wican_esptool_port")
    for mac in (None, "", "02:00:00:00:00", "the wican"):
        _params_refused(pin, folder, dict(s, wican_mac=mac), "linux", "wican_mac")
    # the core: the image's device over settings.esptool.device (the X2's),
    # through the real lookup on the fixture's tree
    ports = root_ports(root)
    with Linux(root):
        port, dev = F.device_port(p, s["esptool"], env="linux")
        assert port == ports["wican"] and dev["serial"] == WICAN_DEV["serial"], port
        port2, _d = F.device_port({}, s["esptool"], env="linux")
        assert port2 == ports["x2"], \
            ("(control: with no image device the core takes the X2's)", port2)


# ---- the flash layout -----------------------------------------------------
def test_layout():
    parts = W.parse_ptable(ptable())
    assert [(p["label"], p["type"], p["subtype"], p["offset"]) for p in parts] == [
        (lb, t, st, off) for lb, t, st, off, _sz in PARTS], parts
    raises(lambda: W.parse_ptable(ptable(magic=0x1234)), "magic 0x1234")
    raises(lambda: W.parse_ptable(ptable([PARTS[0], PARTS[1]])), "no app partition")
    # a table ended by its MD5 entry
    md5ed = ptable()[:32 * len(PARTS)] + struct.pack("<H", 0xEBEB) + b"\0" * 30
    assert len(W.parse_ptable(md5ed + b"\xff" * 64)) == len(PARTS)

    def slot(od, parts=parts):
        return W.boot_slot(parts, otadata(*od))[0]["label"]
    assert slot((None, None)) == "ota_0"            # erased: ota_0
    assert slot(((1, 2), None)) == "ota_0"
    assert slot(((1, 2), (2, 2))) == "ota_1"        # after one OTA
    assert slot(((3, 2), (2, 2))) == "ota_0"        # (3 - 1) mod 2
    assert slot(((1, 2), (2, 2, 0xDEADBEEF))) == "ota_0", "a bad CRC is skipped"
    for bad_state in (3, 4):                        # invalid, aborted
        assert slot(((1, 2), (2, bad_state))) == "ota_0", bad_state
    e = W.otadata_entry(ota_entry(2, 2, 0xDEADBEEF))
    assert (e["seq"], e["crc_ok"], e["valid"]) == (2, False, False), e
    fact = [("factory", 0, 0x00, 0x10000, 0x100000)] + list(PARTS[:3]) + \
        [("ota_0", 0, 0x10, 0x110000, 0x100000)]
    fparts = W.parse_ptable(ptable(fact))
    assert slot((None, None), fparts) == "factory"
    assert slot(((1, 2), None), fparts) == "ota_0"
    # no otadata partition at all: factory, else ota_0
    assert W.boot_slot(fparts, None)[0]["label"] == "factory"
    test_only = W.parse_ptable(ptable([("test", 0, 0x20, 0x10000, 0x1000)]))
    raises(lambda: W.boot_slot(test_only, None), "no factory or ota_0")
    assert W.descriptor_sha(app_image(MAN_SHA)[:0x100]) == MAN_SHA
    raises(lambda: W.descriptor_sha(app_image(MAN_SHA, img_magic=0xFF)[:0x100]),
           "no app image")
    raises(lambda: W.descriptor_sha(app_image(MAN_SHA, desc_magic=0)[:0x100]),
           "no app descriptor")
    raises(lambda: W.descriptor_sha(app_image(MAN_SHA)[:0x80]), "bytes of the app")


# ---- identify -------------------------------------------------------------
def _identify(s, root, tag):
    d = os.path.join(root, "identify_" + tag)
    os.makedirs(d)
    n = len(esptool_calls(root))
    with Linux(root):
        try:
            sha, rec = W.read_identity(pin_of(root), s, d)
        except W.IdentifyError as e:
            sha, rec = e, None
    return sha, rec, esptool_calls(root)[n:], d


def test_identify():
    s, root = fixture("identify")
    ports = root_ports(root)
    sha, rec, ec, d = _identify(s, root, "ota0")
    assert sha == MAN_SHA, sha
    assert set(ports_of(ec)) == {ports["wican"]}, ports_of(ec)
    cmds = [[x for x in ("chip_id", "read_flash") if x in a] for a in ec]
    assert cmds == [["chip_id"], ["read_flash"], ["read_flash"], ["read_flash"]], ec
    reads = [a[a.index("read_flash") + 1:a.index("read_flash") + 3] for a in ec[1:]]
    assert reads == [["0x8000", "0xc00"], ["0xd000", "0x2000"], ["0x10000", "0x100"]], reads
    assert rec["slot"]["label"] == "ota_0" and rec["chip"] == ["ESP32-C3", WICAN_MAC], rec
    assert rec["port"] == ports["wican"] and rec["device"]["serial"] == WICAN_DEV["serial"]
    with open(os.path.join(d, "identify.json")) as f:
        assert json.load(f)["elf_sha256"] == MAN_SHA
    # after an OTA: ota_1 boots, whatever 0x10000 holds
    s, root = fixture("identify_ota1")
    ports = root_ports(root)
    set_board(root, "wican", flash=flash(root, "wican", ota0=MAN_SHA, ota1=OTHER_SHA,
                                          od=((1, 2), (2, 2))))
    sha, rec, ec, d = _identify(s, root, "ota1")
    assert sha == OTHER_SHA and rec["slot"]["label"] == "ota_1", (sha, rec)
    reads = [a[a.index("read_flash") + 1] for a in ec if "read_flash" in a]
    assert reads[-1] == "0x190000", reads
    # the control: the same calls, given the X2's IDs and MAC, read the X2
    sha, _rec, ec, d = _identify(dict(s, wican_esptool_device=dict(X2_DEV),
                                      wican_mac=X2_MAC), root, "control")
    assert sha == X2_SHA and set(ports_of(ec)) == {ports["x2"]}, (sha, ec)
    # the WiCAN's port answers with another MAC: nothing read
    set_board(root, "wican", mac=X2_MAC)
    err, _rec, ec, d = _identify(s, root, "othermac")
    assert isinstance(err, W.IdentifyError) and "not the WiCAN" in str(err), err
    assert [a for a in ec if "read_flash" in a] == [], ec
    with open(os.path.join(d, "identify.json")) as f:
        assert "not the WiCAN" in json.load(f)["error"]
    set_board(root, "wican", mac=WICAN_MAC)
    for env, frag, n_reads in (("chip_id", "chip_id on", 0),
                               ("chip_id", "failed (rc=2", 0),
                               ("read_flash", "read_flash 0x8000", 1)):
        os.environ["FAKE_ESPTOOL_FAIL"] = env
        err, _rec, ec, d = _identify(s, root, "fail_%s_%d" % (env, len(frag)))
        os.environ.pop("FAKE_ESPTOOL_FAIL")
        assert isinstance(err, W.IdentifyError) and frag in str(err), (env, err)
        assert len([a for a in ec if "read_flash" in a]) == n_reads, ec
    os.environ["FAKE_ESPTOOL_SHORT"] = "1"
    err, _rec, ec, d = _identify(s, root, "short")
    os.environ.pop("FAKE_ESPTOOL_SHORT")
    assert isinstance(err, W.IdentifyError) and "bytes, not" in str(err), err
    # a WiCAN with no image at its boot slot
    set_board(root, "wican", flash=flash(root, "wican"))
    err, _rec, ec, d = _identify(s, root, "noimage")
    assert isinstance(err, W.IdentifyError) and "no app image" in str(err), err
    # no WiCAN device setting, or a WiCAN not plugged in: nothing run
    for tag, ss in (("nodev", dict(s, wican_esptool_device=None)),
                    ("absent", dict(s, wican_esptool_device=dict(WICAN_DEV, serial="TESTGONE")))):
        err, _rec, ec, d = _identify(ss, root, tag)
        assert isinstance(err, W.IdentifyError) and "the WiCAN's port" in str(err), (tag, err)
        assert ec == [], (tag, ec)


# ---- the flasher, through the core ---------------------------------------
def _flash(w, s, root, tag):
    d = os.path.join(root, "flash_" + tag)
    os.makedirs(d)
    n = len(esptool_calls(root))
    with Linux(root):
        res = w["flasher"](w["image"], dict(w["image"].params), s, d)
    return res, esptool_calls(root)[n:]


def test_flasher():
    s, root = fixture("flasher", wican_ota0=OTHER_SHA)
    ports = root_ports(root)
    pin = pin_of(root)
    w = W.make(s, pin)
    res, ec = _flash(w, s, root, "ok")
    assert res["ok"] is True and res["phase"] == "done", res
    assert res["port"] == ports["wican"], res
    assert ec and set(ports_of(ec)) == {res["port"]}, ports_of(ec)
    wf = [a for a in ec if "write_flash" in a]
    assert len(wf) == 1, ec
    args = wf[0][wf[0].index("write_flash") + 1:]
    assert args[0::2] == ["0x0", "0x8000", "0xd000", "0x10000"], args
    assert [os.path.basename(x) for x in args[1::2]] == [
        os.path.basename(f["name"]) for f in pin["image"]["files"]], args
    sha, _rec, _ec, _d = _identify(s, root, "after_load")
    assert sha == MAN_SHA, sha
    # a pinned file changed: nothing run
    victim = os.path.join(s["wican_image_dir"], *pin["image"]["files"][3]["name"].split("/"))
    with open(victim, "ab") as f:
        f.write(b"x")
    res, ec = _flash(w, s, root, "pinbad")
    assert res["ok"] is False and res["phase"] == "pin" and ec == [], (res, ec)
    assert "nothing run" in res["detail"], res
    s, root = fixture("flasher_nodev")
    w = W.make(s, pin_of(root))
    res, ec = _flash(w, dict(s, wican_esptool_device=None), root, "nodev")
    assert res["ok"] is False and res["phase"] == "device" and ec == [], (res, ec)
    # the WiCAN's port answers with another MAC: the core writes nothing
    set_board(root, "wican", mac=X2_MAC)
    res, ec = _flash(w, s, root, "othermac")
    assert res["ok"] is False and res["phase"] == "chip-mismatch", res
    assert not [a for a in ec if "write_flash" in a], ec


# ---- hooks ----------------------------------------------------------------
def _hook(w, which, board, image, root, tag):
    d = os.path.join(root, "hook_%s_%s" % (which, tag))
    os.makedirs(d, exist_ok=True)
    n = len(esptool_calls(root))
    with Linux(root):
        out = w[which](board, image, None, d)
    return out, esptool_calls(root)[n:]


def test_hooks():
    s, root = fixture("hooks")
    ports = root_ports(root)
    w = W.make(s, pin_of(root))
    im = w["image"]
    for which in ("on_start", "on_end"):
        out, ec = _hook(w, which, "wican", im, root, "ok")
        assert out["ok"] is True, (which, out)
        assert ec == [["--port", ports["wican"], "--after",
                                        "hard_reset", "chip_id"]], (which, ec)
        assert out["found"]["port"] == ports["wican"] and \
            out["found"]["mac"] == WICAN_MAC, out
    out, ec = _hook(w, "on_end", "wican", None, root, "unknown")
    assert out["ok"] is True and len(ec) == 1, (out, ec)
    out, ec = _hook(w, "on_start", "x2", None, root, "x2")
    assert out["ok"] is True and ec == [], (out, ec)
    set_board(root, "wican", mac=X2_MAC)
    out, ec = _hook(w, "on_start", "wican", im, root, "othermac")
    assert out["ok"] is False and "not the WiCAN" in out["detail"], out
    set_board(root, "wican", mac=WICAN_MAC)
    w2 = W.make(dict(s, wican_esptool_device=None), pin_of(root))
    out, ec = _hook(w2, "on_end", "wican", im, root, "nodev")
    assert out["ok"] is False and "wican_esptool_device" in out["detail"] and ec == [], \
        (out, ec)


# ---- the real runner ------------------------------------------------------
def _engine_run(name, wican_ota0, drop=(), wican_mac=None):
    from vtrux_bench.run import engine as E
    from vtrux_bench.run.model import Check, Group, Image, Suite
    s, root = fixture("engine_" + name, wican_ota0=wican_ota0)
    if wican_mac:
        set_board(root, "wican", mac=wican_mac)
    role = "tester (charge-interposer)"
    holder = os.path.join(root, "bench-holder.md")
    with open(holder, "w") as f:
        f.write("# Bench holder\n\n## Lock\n\n**Holder:** %s (test)\n"
                "**Since:** x\n" % role)
    for k in drop:
        s.pop(k)
    w = W.make(s, pin_of(root))
    x2_flashes = []

    def x2_flasher(image, params, settings, logdir):
        x2_flashes.append(image.name)
        return dict(ok=True, phase="done", detail="fake", recoverable=True)

    def identify(image, ctx, logdir):
        if image.board == W.BOARD:
            return w["identify"](image, ctx, logdir)
        return "X2-APP"
    ims = {"x2app": Image("x2app", "fake", expect="X2-APP", board="x2"),
           W.IMAGE: w["image"]}
    suite = Suite(name="t-wican", role=role, groups=[Group(
        "g", [Check("c", ["-c", "pass"], python=True, timeout_s=60)],
        needs_lock=True, image=["x2app", W.IMAGE])], images=ims,
        flashers={"fake": x2_flasher, W.FLASHER: w["flasher"]},
        identify=identify, on_start=w["on_start"], on_end=w["on_end"])
    # the runner refuses a bench run with no canmain_check (shared/bench
    # SPEC section 8, from 2026.11.11.2): the core's own hardware-free
    # fixture, from the pinned checkout's tests, as its own tests use it
    import _canmain_fixture
    canmain = _canmain_fixture.install(os.path.join(root, "canmain"))
    settings = dict(s, runs_root=os.path.join(root, "runs"),
                    bench_holder=holder, pause_poll_s=0.2, canmain_check=canmain)
    eng = E.Engine(suite, settings, out=lambda *a: None)
    with Linux(root):
        rec = eng.run()
    from vtrux_bench.run import record as R
    prog = R.load_progress(eng.run_dir)
    events = [e for e in prog if e.get("type") == "event"]
    return rec, root, x2_flashes, events


def test_engine():
    rec, root, x2f, ev = _engine_run("good", MAN_SHA)
    ports = root_ports(root)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "PASS"}, v
    assert not [e for e in ev if e.get("kind") == "flash-start"], ev
    ec = esptool_calls(root)
    assert ec and set(ports_of(ec)) == {ports["wican"]}, ports_of(ec)
    assert not [a for a in ec if "write_flash" in a], ec
    assert len([a for a in ec if "hard_reset" in a]) == 2, ec   # start, release
    # another image: identify reads it, the pinned image is loaded over USB,
    # identify reads it back, the run passes
    rec, root, x2f, ev = _engine_run("other", OTHER_SHA)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "PASS"}, (v, rec["results"])
    fl = [e for e in ev if e.get("kind") == "flash"]
    assert [(e.get("image"), e.get("ok")) for e in fl] == [("wican_man", True)], fl
    ec = esptool_calls(root)
    assert set(ports_of(ec)) == {root_ports(root)["wican"]}, ports_of(ec)
    assert len([a for a in ec if "write_flash" in a]) == 1, ec
    assert x2f == [], x2f
    # no WiCAN device, or its port answering with another MAC: nothing is
    # written and the bench stops
    for tag, kw in (("nodev", {"drop": ("wican_esptool_device",)}),
                    ("othermac", {"wican_mac": X2_MAC})):
        rec, root, x2f, ev = _engine_run(tag, OTHER_SHA, **kw)
        v = {r["id"]: r["verdict"] for r in rec["results"]}
        assert v == {"g/c": "NOT-RUN"}, (tag, v)
        assert not [a for a in esptool_calls(root) if "write_flash" in a], tag
        assert x2f == [], (tag, x2f)


TESTS = [test_source, test_params, test_layout, test_identify, test_flasher,
         test_hooks, test_engine]

# --can-fail: one source edit of bench_wican.py each; every one must make
# a test fail
MUTANTS = [
    ("the identity is not the pin's image hash",
     'expect = pin["image"]["image_hash"]', 'expect = pin["image"]["files"][3]["sha256"]'),
    ("an unset settings key accepted",
     "    if not v:\n        raise WicanSourceError(",
     "    if False:\n        raise WicanSourceError("),
    ("an image dir that is not a folder accepted",
     "    if not os.path.isdir(d):\n", "    if False:\n"),
    ("the pinned files not checked before the load",
     "        bad = wican_pin.check(folder, pin)", "        bad = []"),
    ("the WiCAN's device not given (the core takes the X2's)",
     '    return {"device": dict(dev)}\n', "    return {}\n"),
    ("no WiCAN device accepted",
     '    if not (isinstance(dev, dict) and dev.get("serial")):\n',
     "    if False:\n"),
    ("no WiCAN port accepted on Windows",
     "        if not port:\n", "        if False:\n"),
    ("the WiCAN's MAC not given",
     '    return {"type": pin["image"]["chip"], "mac": mac.lower()}',
     '    return {"type": pin["image"]["chip"]}'),
    ("a malformed MAC accepted",
     "    if not (isinstance(mac, str) and MAC_RX.match(mac)):\n", "    if False:\n"),
    ("the md5s not passed", '        md5[path] = f["md5"]\n', ""),
    ("the WiCAN's params not given to the core",
     "core.esptool_flash(img, dict(params, **p), settings_, logdir)",
     "core.esptool_flash(img, dict(params), settings_, logdir)"),
    ("identify and the hooks use the X2's settings.esptool",
     "            wican_port_params(settings, core.this_env()), {})",
     '            {}, settings.get("esptool") or {})'),
    ("the chip not checked", "        why = core.chip_mismatch(text, self.chip)",
     "        why = None"),
    ("a failed chip_id accepted",
     "        if rc != 0:\n            raise IdentifyError(\"esptool chip_id",
     "        if False:\n            raise IdentifyError(\"esptool chip_id"),
    ("a failed read accepted",
     "        if rc != 0 or not os.path.isfile(out):",
     "        if not os.path.isfile(out):"),
    ("a short read accepted", "        if len(data) != size:", "        if False:"),
    ("a partition entry with another magic accepted",
     "        if magic != PART_MAGIC:\n", "        if False:\n"),
    ("a table with no app accepted",
     '    if not any(p["type"] == APP for p in parts):\n', "    if False:\n"),
    ("the otadata CRC not checked",
     '    crc_ok = crc == zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)',
     "    crc_ok = True"),
    ("an invalid or aborted entry counts",
     "             and state not in (OTA_STATE_INVALID, OTA_STATE_ABORTED))",
     "             )"),
    ("the slot is seq mod n", "        return otas[(seq - 1) % len(otas)], entries",
     "        return otas[seq % len(otas)], entries"),
    ("the lowest seq wins", '        seq = max(e["seq"] for e in good)',
     '        seq = min(e["seq"] for e in good)'),
    ("otadata ignored", "    if good and otas:\n", "    if False:\n"),
    ("no factory fallback", "    if FACTORY in apps:\n", "    if False:\n"),
    ("the image header not checked", "    if head[0] != IMAGE_MAGIC:\n",
     "    if False:\n"),
    ("the descriptor magic not checked", "    if magic != DESC_MAGIC:\n",
     "    if False:\n"),
    ("the hash read at the wrong offset", "ELF_SHA_AT = 0x90", "ELF_SHA_AT = 0x80"),
    ("the descriptor read at 0x10000, not the boot slot",
     'et.read(slot["offset"], HEAD_READ, "app")', 'et.read(0x10000, HEAD_READ, "app")'),
    ("an identify failure reads as no image",
     '        rec["error"] = str(e)\n        raise\n',
     '        rec["error"] = str(e)\n        return None, rec\n'),
    ("a port failure is not an IdentifyError",
     "            raise IdentifyError(\"the WiCAN's port: %s\" % e)",
     "            raise"),
    ("the hooks pass on failure",
     "            return dict(ok=False, detail=\"the WiCAN's USB hard reset \"",
     "            return dict(ok=True, detail=\"the WiCAN's USB hard reset \""),
    ("the hooks reset nothing", "                typ, mac = et.chip_check(HARD_RESET)\n",
     "                typ, mac = None, None\n"),
    ("the hooks' reset is not a hard reset",
     'HARD_RESET = ("--after", "hard_reset")', "HARD_RESET = ()"),
    ("the X2 gets a WiCAN step", "            if board != BOARD:\n",
     "            if False:\n"),
]


def run_tests():
    global TMP
    TMP = scratch_dir.make("bench-wican-test")
    try:
        return _run_tests()
    finally:
        scratch_dir.remove(TMP)


def _run_tests():
    bad = 0
    for t in TESTS:
        try:
            t()
            print("ok    %s" % t.__name__)
        except Exception:                                  # noqa: BLE001
            bad += 1
            print("FAIL  %s\n%s" % (t.__name__, traceback.format_exc()[-900:]))
    print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
    return 1 if bad else 0


def can_fail():
    d = scratch_dir.make("bench-wican-mutants")
    try:
        return _can_fail(d)
    finally:
        scratch_dir.remove(d)


def _can_fail(d):
    src_path = os.path.join(HERE, "bench_wican.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    bad = 0
    for what, old, new in MUTANTS:
        n = src.count(old)
        if n != 1:
            print("FAIL  %-52s NOT APPLIED (text found %d times)" % (what, n))
            bad += 1
            continue
        p = os.path.join(d, "bench_wican.py")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                           capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, BENCH_WICAN_MODULE=p,
                                    PYTHONIOENCODING="utf-8"))
        caught = r.returncode != 0
        bad += not caught
        failed = [ln.split()[1] for ln in r.stdout.splitlines()
                  if ln.startswith("FAIL ")]
        print("%s  %-52s %s" % ("ok  " if caught else "FAIL", what,
                                "caught by %s" % ", ".join(failed) if caught
                                else "STILL PASSES"))
    print("\n%d of %d mutants caught" % (len(MUTANTS) - bad, len(MUTANTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(can_fail() if "--can-fail" in sys.argv[1:] else run_tests())
