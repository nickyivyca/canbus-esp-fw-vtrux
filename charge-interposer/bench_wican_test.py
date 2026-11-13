"""Tests the HARNESS: bench_wican.py, the WiCAN as a board of the bench
suite (tester, 2026-10-23; the USB load 2026-11-12). No hardware, no
network: gen-inhibit's wican_reboot.py and page_state.py are stood in for by
fakes in a temp gi_tools, with the WiCAN's page a JSON file; the pin is the
committed wican_pin.json with its files made up (same image hash); esptool
is a fake script and the serial-device lookup a stand-in.

  source    the identity is the pin's image hash; each refusal: gi_tools and
            each of its two files, wican_image_dir unset or not a folder
  params    the esptool flasher's params: the pinned files at their offsets
            with their md5s, the pin's chip with the WiCAN's MAC, and the
            WiCAN's own device (Linux) or port (Windows) -- never the X2's,
            and refused without one; and the core's device_port takes the
            image's device over settings.esptool.device (the precedence)
  flasher   through the core's esptool_flash with a fake esptool: every
            esptool call goes to the WiCAN's port, write_flash carries the
            four pinned files at their offsets, and it reports ok; a changed
            pinned file, a missing WiCAN device or a chip that answers with
            another MAC each run no write
  hooks     the start step is --first wican_man with wican_man the only
            --known, and fails on any other image; the release step is
            --want wican_man, or --want-unknown; no step for the X2
  identify  the hash off the page; an unreadable page or one with no hash
            RAISES
  engine    the real chain runner, a fake X2 beside the WiCAN: on wican_man
            the run passes with no WiCAN flash; on another image the start
            hook stops the bench before any identify or flash; a WiCAN that
            reads another image after the start hook is loaded (the fake
            esptool puts the pinned image on its page) and the run passes;
            with no WiCAN device the load runs nothing and the bench stops

    py -3.14 -B charge-interposer/bench_wican_test.py [--can-fail]

--can-fail runs the tests against each of MUTANTS, a copy of bench_wican.py
with one source edit, and every one must make them fail.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import traceback

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

# this run's scratch_dir folder, made by run_tests() and removed in its
# finally (2026-10-31: before, a fixed scratch/ folder never removed)
TMP = None
# the pinned image's identity: the committed pin's image hash
MAN_SHA = wican_pin.load()["image"]["image_hash"]
OTHER_SHA = "b2" * 32
# made-up USB identities and MACs (the repo is public)
WICAN_DEV = {"vid": "303a", "pid": "1001", "serial": "02:00:00:00:00:0A", "interface": 0}
X2_DEV = {"vid": "303a", "pid": "1001", "serial": "02:00:00:00:00:0B", "interface": 0}
WICAN_MAC = "02:00:00:00:00:0A"

FAKE_PAGE_STATE = '''
import json, os
HOST = "fake"
def read_page(timeout):
    p = os.environ["FAKE_WICAN_PAGE"]
    if os.environ.get("FAKE_WICAN_DOWN"):
        raise OSError("fake: no page")
    with open(p) as f:
        return json.load(f)
def state(p):
    return p
'''

# gen-inhibit's wican_reboot.py as its docstring states the contract: PASS
# with --first only on an image named by --known, with --want only on that
# image, never with --want-unknown. FAKE_WICAN_AFTER puts another image on
# the page after the reboot (the board changed under the run).
FAKE_REBOOT = '''
import argparse, json, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("--label"); ap.add_argument("--out")
ap.add_argument("--known", action="append", default=[])
ap.add_argument("--first"); ap.add_argument("--want")
ap.add_argument("--want-unknown", action="store_true")
a = ap.parse_args()
with open(os.environ["FAKE_WICAN_ARGV"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.environ["FAKE_WICAN_PAGE"]) as f:
    sha = json.load(f)["elf_sha256"]
known = dict(k.split("=", 1) for k in a.known)
names = [n for n, s in known.items() if s == sha]
if a.first:
    ok = bool(names)
elif a.want:
    ok = known.get(a.want) == sha
else:
    ok = False
after = os.environ.get("FAKE_WICAN_AFTER")
if after and a.first:
    with open(os.environ["FAKE_WICAN_PAGE"], "w") as f:
        json.dump({"elf_sha256": after}, f)
if os.environ.get("FAKE_WICAN_NO_RECORD"):
    sys.exit(1)
with open(a.out, "w") as f:
    json.dump({"verdict": "PASS" if ok else "FAIL",
               "detail": "fake: found %s" % (names[0] if names else
                                             "unknown " + sha[:8]),
               "found_image": names[0] if names else None,
               "found_sha": sha}, f)
sys.exit(0 if ok else 1)
'''

# esptool v4 as the core flasher reads it: chip_id prints the chip and MAC
# (FAKE_ESPTOOL_MAC), write_flash one "Hash of data verified" per file and
# then puts FAKE_ESPTOOL_FLASHES on the WiCAN's page (the image loaded).
FAKE_ESPTOOL = '''
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_ESPTOOL_LOG"], "a") as f:
    f.write(json.dumps(a) + "\\n")
if "chip_id" in a:
    print("esptool.py v4.13.0")
    print("Chip is ESP32-C3 (QFN32) (revision v0.4)")
    print("MAC: " + os.environ["FAKE_ESPTOOL_MAC"])
elif "write_flash" in a:
    files = a[a.index("write_flash") + 1:]
    for _i in range(len(files) // 2):
        print("Hash of data verified.")
    with open(os.environ["FAKE_WICAN_PAGE"], "w") as f:
        json.dump({"elf_sha256": os.environ["FAKE_ESPTOOL_FLASHES"]}, f)
'''


def fake_pin(folder):
    """The committed pin with its four files made up in folder (sizes and
    hashes of the made-up bytes; the image hash and offsets kept)."""
    pin = copy.deepcopy(wican_pin.load())
    for k, f in enumerate(pin["image"]["files"]):
        data = (b"%d:%s:" % (k, f["name"].encode())) * (40 + 9 * k)
        path = os.path.join(folder, *f["name"].split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        f.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                 md5=hashlib.md5(data).hexdigest())
    return pin


def fixture(name, base=None):
    """A temp gi_tools (fake wican_reboot.py and page_state.py), a
    wican_image_dir holding made-up files of the pin (pin.json beside it:
    pin_of(root)), a fake esptool, and the settings naming them. -> (settings,
    root), under base (default TMP). Also usable by bench_suite_test, with
    its own folder as base."""
    root = os.path.join(base or TMP, name)
    scratch_dir.remove(root)
    bench = os.path.join(root, "gi_tools", "bench")
    os.makedirs(bench)
    with open(os.path.join(bench, "page_state.py"), "w") as f:
        f.write(FAKE_PAGE_STATE)
    with open(os.path.join(bench, "wican_reboot.py"), "w") as f:
        f.write(FAKE_REBOOT)
    images = os.path.join(root, "image")
    pin = fake_pin(images)
    with open(os.path.join(root, "pin.json"), "w") as f:
        json.dump(pin, f)
    esptool = os.path.join(root, "fake_esptool.py")
    with open(esptool, "w") as f:
        f.write(FAKE_ESPTOOL)
    page(root, MAN_SHA)
    os.environ["FAKE_WICAN_ARGV"] = os.path.join(root, "argv.jsonl")
    os.environ["FAKE_ESPTOOL_LOG"] = os.path.join(root, "esptool.jsonl")
    os.environ["FAKE_ESPTOOL_MAC"] = WICAN_MAC.lower()
    os.environ["FAKE_ESPTOOL_FLASHES"] = MAN_SHA
    for k in ("FAKE_WICAN_DOWN", "FAKE_WICAN_AFTER", "FAKE_WICAN_NO_RECORD"):
        os.environ.pop(k, None)
    return {"gi_tools": os.path.join(root, "gi_tools"),
            "wican_image_dir": images,
            "wican_esptool_device": dict(WICAN_DEV),
            "wican_mac": WICAN_MAC,
            "esptool": {"argv": [sys.executable, "-B", esptool],
                        "device": dict(X2_DEV), "baud": 921600},
            "hosts": {"local": {"python": [sys.executable]}}}, root


def pin_of(root):
    with open(os.path.join(root, "pin.json")) as f:
        return json.load(f)


def page(root, sha):
    p = os.path.join(root, "page.json")
    with open(p, "w") as f:
        json.dump({"elf_sha256": sha}, f)
    os.environ["FAKE_WICAN_PAGE"] = p


def calls(root):
    p = os.path.join(root, "argv.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def esptool_calls(root):
    p = os.path.join(root, "esptool.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def refused(settings, *frags, pin=None):
    try:
        W.make(settings, pin)
    except W.WicanSourceError as e:
        for fr in frags:
            assert fr in str(e), (frags, str(e))
        return
    raise AssertionError("not refused: %s" % (frags,))


class _FakeLinux:
    """The core flasher on Linux with a stand-in serial-device lookup: a
    device resolves to /dev/fake-by-id/<serial>."""

    def __enter__(self):
        self.env, self.resolve = F.this_env, F.SD.resolve
        F.this_env = lambda: "linux"
        F.SD.resolve = lambda dev, what="device", env=None, **k: dict(
            dev, port="/dev/fake-by-id/" + dev["serial"])
        return self

    def __exit__(self, *a):
        F.this_env, F.SD.resolve = self.env, self.resolve


# ---- source ---------------------------------------------------------------
def test_source():
    s, root = fixture("source")
    pin = pin_of(root)
    w = W.make(s, pin)
    im = w["image"]
    assert (im.name, im.board, im.flasher, im.expect) == (
        "wican_man", "wican", "wican-esptool", MAN_SHA), im
    assert im.params == {"pin": pin["pin"]}, im.params
    assert W.make(s)["image"].expect == MAN_SHA     # the committed pin
    refused(dict(s, wican_image_dir=""), "settings.wican_image_dir is not set", pin=pin)
    refused(dict(s, wican_image_dir=os.path.join(root, "nope")), "is not a folder",
            pin=pin)
    refused(dict(s, gi_tools=""), "settings.gi_tools is not set", pin=pin)
    for gone in ("wican_reboot.py", "page_state.py"):
        s, root = fixture("source_" + gone[:-3])
        os.remove(os.path.join(root, "gi_tools", "bench", gone))
        refused(s, gone, "settings.gi_tools", pin=pin)


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
    assert p["chip"] == {"type": "ESP32-C3", "mac": WICAN_MAC.lower()}, p["chip"]
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
    # the core: the image's device over settings.esptool.device (the X2's)
    with _FakeLinux():
        port, dev = F.device_port(p, s["esptool"], env="linux")
        assert port == "/dev/fake-by-id/" + WICAN_DEV["serial"], port
        port2, _d = F.device_port({}, s["esptool"], env="linux")
        assert port2 == "/dev/fake-by-id/" + X2_DEV["serial"], \
            ("(control: with no image device the core takes the X2's)", port2)


# ---- the flasher, through the core ---------------------------------------
def _flash(w, s, root, tag):
    d = os.path.join(root, "flash_" + tag)
    os.makedirs(d)
    n = len(esptool_calls(root))
    with _FakeLinux():
        res = w["flasher"](w["image"], dict(w["image"].params), s, d)
    return res, esptool_calls(root)[n:]


def test_flasher():
    s, root = fixture("flasher")
    pin = pin_of(root)
    w = W.make(s, pin)
    page(root, OTHER_SHA)
    res, ec = _flash(w, s, root, "ok")
    assert res["ok"] is True and res["phase"] == "done", res
    assert res["port"] == "/dev/fake-by-id/" + WICAN_DEV["serial"], res
    ports = [a[a.index("--port") + 1] for a in ec]
    assert ec and set(ports) == {res["port"]}, ports
    wf = [a for a in ec if "write_flash" in a]
    assert len(wf) == 1, ec
    args = wf[0][wf[0].index("write_flash") + 1:]
    assert args[0::2] == ["0x0", "0x8000", "0xd000", "0x10000"], args
    assert [os.path.basename(x) for x in args[1::2]] == [
        os.path.basename(f["name"]) for f in pin["image"]["files"]], args
    with open(os.environ["FAKE_WICAN_PAGE"]) as f:
        assert json.load(f)["elf_sha256"] == MAN_SHA
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
    # the chip answers with another MAC: the core writes nothing
    os.environ["FAKE_ESPTOOL_MAC"] = "02:00:00:00:00:0b"
    res, ec = _flash(w, s, root, "othermac")
    os.environ["FAKE_ESPTOOL_MAC"] = WICAN_MAC.lower()
    assert res["ok"] is False and res["phase"] == "chip-mismatch", res
    assert not [a for a in ec if "write_flash" in a], ec


# ---- hooks ----------------------------------------------------------------
def _hook(w, which, board, image, root):
    d = os.path.join(root, "hook_%s_%s" % (which, len(calls(root))))
    os.makedirs(d, exist_ok=True)
    return w[which](board, image, None, d)


def test_hooks():
    s, root = fixture("hooks")
    w = W.make(s, pin_of(root))
    im = w["image"]
    out = _hook(w, "on_start", "wican", im, root)
    a = calls(root)[-1]
    assert out["ok"] is True, out
    assert a.count("--known") == 1 and a[a.index("--known") + 1] == \
        "wican_man=" + MAN_SHA, a
    assert a[a.index("--first") + 1] == "wican_man" and "--want" not in a, a
    out = _hook(w, "on_end", "wican", im, root)
    a = calls(root)[-1]
    assert out["ok"] is True and a[a.index("--want") + 1] == "wican_man" \
        and "--first" not in a, (out, a)
    out = _hook(w, "on_end", "wican", None, root)
    assert out["ok"] is False and "--want-unknown" in calls(root)[-1], out
    n = len(calls(root))
    out = _hook(w, "on_start", "x2", None, root)
    assert out["ok"] is True and len(calls(root)) == n, (out, calls(root))
    page(root, OTHER_SHA)
    out = _hook(w, "on_start", "wican", im, root)
    assert out["ok"] is False and OTHER_SHA[:8] in out["detail"], out
    page(root, MAN_SHA)
    os.environ["FAKE_WICAN_NO_RECORD"] = "1"
    out = _hook(w, "on_start", "wican", im, root)
    os.environ.pop("FAKE_WICAN_NO_RECORD")
    assert out["ok"] is False and "no record" in out["detail"], out


# ---- identify -------------------------------------------------------------
def test_identify():
    s, root = fixture("identify")
    w = W.make(s, pin_of(root))
    im = w["image"]
    assert w["identify"](im, None, root) == MAN_SHA
    page(root, OTHER_SHA)
    assert w["identify"](im, None, root) == OTHER_SHA
    os.environ["FAKE_WICAN_DOWN"] = "1"
    try:
        w["identify"](im, None, root)
        raise AssertionError("an unreadable page did not raise")
    except W.IdentifyError as e:
        assert "could not be read" in str(e), e
    finally:
        os.environ.pop("FAKE_WICAN_DOWN")
    with open(os.environ["FAKE_WICAN_PAGE"], "w") as f:
        json.dump({"mode": 0}, f)
    try:
        w["identify"](im, None, root)
        raise AssertionError("a page with no hash did not raise")
    except W.IdentifyError as e:
        assert "no elf_sha256" in str(e), e


# ---- the real runner ------------------------------------------------------
def _engine_run(name, wican_sha, after=None, drop=()):
    from vtrux_bench.run import engine as E
    from vtrux_bench.run.model import Check, Group, Image, Suite
    s, root = fixture("engine_" + name)
    page(root, wican_sha)
    if after:
        os.environ["FAKE_WICAN_AFTER"] = after
    role = "tester (charge-interposer)"
    holder = os.path.join(root, "bench-holder.md")
    with open(holder, "w") as f:
        f.write("# Bench holder\n\n## Lock\n\n**Holder:** %s (test)\n"
                "**Since:** x\n" % role)
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
    sys.path.insert(0, os.path.join(shared_pin.folder("shared/bench"), "tests"))
    import _canmain_fixture
    canmain = _canmain_fixture.install(os.path.join(root, "canmain"))
    settings = dict(s, runs_root=os.path.join(root, "runs"),
                    bench_holder=holder, pause_poll_s=0.2, canmain_check=canmain)
    for k in drop:
        settings.pop(k)
    eng = E.Engine(suite, settings, out=lambda *a: None)
    with _FakeLinux():
        rec = eng.run()
    os.environ.pop("FAKE_WICAN_AFTER", None)
    from vtrux_bench.run import record as R
    prog = R.load_progress(eng.run_dir)
    events = [e for e in prog if e.get("type") == "event"]
    return rec, root, x2_flashes, events


def test_engine():
    rec, root, x2f, ev = _engine_run("good", MAN_SHA)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "PASS"}, v
    assert not [e for e in ev if e.get("kind") == "flash-start"], ev
    assert esptool_calls(root) == [], esptool_calls(root)
    a = calls(root)
    assert [x[x.index("--label") + 1] for x in a] == [
        "start wican", "release wican"], a
    assert a[-1][a[-1].index("--want") + 1] == "wican_man", a
    # another image at the start: the start hook stops the bench before
    # anything is identified or flashed
    rec, root, x2f, ev = _engine_run("other", OTHER_SHA)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "NOT-RUN"}, v
    assert "on_start for wican failed" in rec["results"][0]["detail"], \
        rec["results"][0]
    kinds = [e.get("kind") for e in ev]
    assert "identify" not in kinds and "flash-start" not in kinds, kinds
    assert x2f == [] and esptool_calls(root) == [], (x2f, esptool_calls(root))
    # a clean start, then the WiCAN reads another image: identify reads it,
    # the pinned image is loaded over USB, identify reads it back, the run
    # passes
    rec, root, x2f, ev = _engine_run("changed", MAN_SHA, after=OTHER_SHA)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "PASS"}, (v, rec["results"])
    fl = [e for e in ev if e.get("kind") == "flash"]
    assert [(e.get("image"), e.get("ok")) for e in fl] == [("wican_man", True)], fl
    ports = {x[x.index("--port") + 1] for x in esptool_calls(root)}
    assert ports == {"/dev/fake-by-id/" + WICAN_DEV["serial"]}, ports
    # the same with no WiCAN device: the load runs nothing, the bench stops
    rec, root, x2f, ev = _engine_run("nodev", MAN_SHA, after=OTHER_SHA,
                                     drop=("wican_esptool_device",))
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "NOT-RUN"}, v
    fl = [e for e in ev if e.get("kind") == "flash"]
    assert [(e.get("image"), e.get("ok")) for e in fl] == [("wican_man", False)], fl
    assert esptool_calls(root) == [], esptool_calls(root)


TESTS = [test_source, test_params, test_flasher, test_hooks, test_identify,
         test_engine]

# --can-fail: one source edit of bench_wican.py each; every one must make
# a test fail
MUTANTS = [
    ("the identity is not the pin's image hash",
     'expect = pin["image"]["image_hash"]', 'expect = pin["image"]["files"][3]["sha256"]'),
    ("a missing gen-inhibit tool accepted",
     "        if not os.path.isfile(p):\n", "        if False:\n"),
    ("an unset settings key accepted",
     "    if not v:\n        raise WicanSourceError(",
     "    if False:\n        raise WicanSourceError("),
    ("an image dir that is not a folder accepted",
     "    if not os.path.isdir(d):\n", "    if False:\n"),
    ("the pinned files not checked before the load",
     "        bad = wican_pin.check(folder, pin)", "        bad = []"),
    ("the WiCAN's device not passed (the core takes the X2's)",
     '        out["device"] = dict(dev)\n', "        pass\n"),
    ("no WiCAN device accepted",
     '        if not (isinstance(dev, dict) and dev.get("serial")):\n',
     "        if False:\n"),
    ("no WiCAN port accepted on Windows",
     "        if not port:\n", "        if False:\n"),
    ("the WiCAN's MAC not passed",
     '"chip": {"type": pin["image"]["chip"], "mac": mac.lower()}',
     '"chip": {"type": pin["image"]["chip"]}'),
    ("a malformed MAC accepted",
     "    if not (isinstance(mac, str) and MAC_RX.match(mac)):\n", "    if False:\n"),
    ("the md5s not passed", '        md5[path] = f["md5"]\n', ""),
    ("the WiCAN's params not given to the core",
     "core.esptool_flash(img, dict(params, **p), settings_, logdir)",
     "core.esptool_flash(img, dict(params), settings_, logdir)"),
    ("the start step passes whatever wican_reboot says",
     "            return dict(ok=rc == 0 and rec.get(\"verdict\") == \"PASS\",",
     "            return dict(ok=True,"),
    ("the start step knows another image",
     '"--known", "%s=%s" % (IMAGE, expect)]',
     '"--known", "%s=%s" % (IMAGE, expect), "--known", "x=" + "b2" * 32]'),
    ("the release step is the start step",
     'on_end=board_step("release")', 'on_end=board_step("start")'),
    ("the X2 gets a WiCAN step", "            if board != BOARD:\n",
     "            if False:\n"),
    ("an unreadable page reads as no image",
     "        except Exception as e:                             # noqa: BLE001\n"
     "            raise IdentifyError(",
     "        except Exception as e:                             # noqa: BLE001\n"
     "            return None\n            raise IdentifyError("),
    ("a page with no hash accepted",
     "        if not (isinstance(sha, str) and SHA_RX.match(sha)):\n",
     "        if False:\n"),
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
