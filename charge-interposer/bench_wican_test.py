"""Tests the HARNESS: bench_wican.py, the WiCAN as a board of the bench
suite (tester, 2026-10-23). No hardware, no network: gen-inhibit's
wican_reboot.py and page_state.py are stood in for by fakes in a temp
gi_tools, with the WiCAN's page a JSON file.

  source    the identity from gen-inhibit's chain settings, read-only, and
            each refusal: the settings key, the file, its JSON, gi_images,
            the folder, 0 or 2+ wican-fw_obd_*.bin, a bin with no app
            descriptor, gi_tools and each of its two files
  hooks     the start step is --first wican_man with wican_man the only
            --known, and fails on any other image; the release step is
            --want wican_man, or --want-unknown; no step for the X2
  identify  the hash off the page; an unreadable page or one with no hash
            RAISES; the flasher always refuses, unrecoverable, naming what
            identify read
  engine    the real chain runner, a fake X2 beside the WiCAN: on wican_man
            the run passes with no WiCAN flash; on another image the start
            hook stops the bench before any identify or flash; a WiCAN that
            reads another image after the start hook stops the bench at the
            refused flash

    py -3.14 -B charge-interposer/bench_wican_test.py [--can-fail]

--can-fail runs the tests against each of MUTANTS, a copy of bench_wican.py
with one source edit, and every one must make them fail.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "shared", "bench"))

_MOD = os.environ.get("BENCH_WICAN_MODULE")
if _MOD:                    # --can-fail: a mutant stands in for the module
    import importlib.util
    _spec = importlib.util.spec_from_file_location("bench_wican", _MOD)
    W = importlib.util.module_from_spec(_spec)
    sys.modules["bench_wican"] = W
    _spec.loader.exec_module(W)
else:
    import bench_wican as W                               # noqa: E402

import build_lookup                                       # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "interposer-tester",
                   "bench-wican-test")
MAN_SHA = "a1" * 32
OTHER_SHA = "b2" * 32

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
if after:
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


def app_bin(path, sha):
    """A file elf_sha256_of_bin reads as an app image carrying sha."""
    at = build_lookup._ELF_SHA_AT
    head = bytearray(at + 32 + 16)
    head[0] = 0xE9
    head[32:36] = struct.pack("<I", build_lookup.APP_DESC_MAGIC)
    head[at:at + 32] = bytes.fromhex(sha)
    with open(path, "wb") as f:
        f.write(bytes(head))


def fixture(name):
    """A temp gi_tools (fake wican_reboot.py and page_state.py) and a
    gen-inhibit chain settings JSON whose gi_images holds build-tester-man/
    with the app bin (MAN_SHA) and ota_data_initial.bin beside it, as the
    real folder does. -> (settings, root). Also usable by
    bench_suite_test."""
    root = os.path.join(TMP, name)
    shutil.rmtree(root, ignore_errors=True)
    bench = os.path.join(root, "gi_tools", "bench")
    os.makedirs(bench)
    with open(os.path.join(bench, "page_state.py"), "w") as f:
        f.write(FAKE_PAGE_STATE)
    with open(os.path.join(bench, "wican_reboot.py"), "w") as f:
        f.write(FAKE_REBOOT)
    man = os.path.join(root, "gi_images", W.BUILD_DIR)
    os.makedirs(man)
    app_bin(os.path.join(man, "wican-fw_obd_e3d9437_man.bin"), MAN_SHA)
    with open(os.path.join(man, "ota_data_initial.bin"), "wb") as f:
        f.write(b"\xff" * 64)
    cs = os.path.join(root, "chain_settings.json")
    with open(cs, "w") as f:
        json.dump({"gi_images": os.path.join(root, "gi_images")}, f)
    page(root, MAN_SHA)
    os.environ["FAKE_WICAN_ARGV"] = os.path.join(root, "argv.jsonl")
    for k in ("FAKE_WICAN_DOWN", "FAKE_WICAN_AFTER", "FAKE_WICAN_NO_RECORD"):
        os.environ.pop(k, None)
    return {"gi_tools": os.path.join(root, "gi_tools"),
            "gi_chain_settings": cs,
            "hosts": {"local": {"python": [sys.executable]}}}, root


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


def refused(settings, *frags):
    try:
        W.make(settings)
    except W.WicanSourceError as e:
        for fr in frags:
            assert fr in str(e), (frags, str(e))
        return
    raise AssertionError("not refused: %s" % (frags,))


# ---- source ---------------------------------------------------------------
def test_source():
    s, root = fixture("source")
    w = W.make(s)
    im = w["image"]
    assert (im.name, im.board, im.flasher, im.expect) == (
        "wican_man", "wican", "wican-never", MAN_SHA), im
    assert W.standing_bin(s).endswith("wican-fw_obd_e3d9437_man.bin")
    man = os.path.join(root, "gi_images", W.BUILD_DIR)
    refused(dict(s, gi_chain_settings=""),
            "settings.gi_chain_settings is not set")
    refused(dict(s, gi_chain_settings=os.path.join(root, "nope.json")),
            "is missing")
    bad = os.path.join(root, "bad.json")
    with open(bad, "w") as f:
        f.write("{")
    refused(dict(s, gi_chain_settings=bad), "not JSON")
    with open(bad, "w") as f:
        json.dump({"gi_commit": "x"}, f)
    refused(dict(s, gi_chain_settings=bad), "has no gi_images")
    with open(bad, "w") as f:
        json.dump({"gi_images": os.path.join(root, "elsewhere")}, f)
    refused(dict(s, gi_chain_settings=bad), "is not a folder")
    app_bin(os.path.join(man, "wican-fw_obd_e3d9437_man2.bin"), OTHER_SHA)
    refused(s, "2 files match", "want exactly 1")
    for fn in os.listdir(man):
        if fn.startswith("wican-fw_obd_"):
            os.remove(os.path.join(man, fn))
    refused(s, "0 files match")
    with open(os.path.join(man, "wican-fw_obd_e3d9437_man.bin"), "wb") as f:
        f.write(b"\xe9" + b"\x00" * 300)
    refused(s, "no app descriptor")
    s, root = fixture("source_tools")
    refused(dict(s, gi_tools=""), "settings.gi_tools is not set")
    for gone in ("wican_reboot.py", "page_state.py"):
        s, root = fixture("source_" + gone[:-3])
        os.remove(os.path.join(root, "gi_tools", "bench", gone))
        refused(s, gone, "settings.gi_tools")


# ---- hooks ----------------------------------------------------------------
def _hook(w, which, board, image, root):
    d = os.path.join(root, "hook_%s_%s" % (which, len(calls(root))))
    os.makedirs(d, exist_ok=True)
    return w[which](board, image, None, d)


def test_hooks():
    s, root = fixture("hooks")
    w = W.make(s)
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


# ---- identify and the flasher -------------------------------------------
def test_identify_flasher():
    s, root = fixture("identify")
    w = W.make(s)
    im = w["image"]
    assert w["identify"](im, None, root) == MAN_SHA
    page(root, OTHER_SHA)
    assert w["identify"](im, None, root) == OTHER_SHA
    res = w["flasher"](im, {}, s, root)
    assert res["ok"] is False and res["recoverable"] is False, res
    assert OTHER_SHA in res["detail"] and "never flashes" in res["detail"], res
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
def _engine_run(name, wican_sha, after=None):
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
    w = W.make(s)
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
        identify=identify, on_start=w["on_start"], on_end=w["on_end"],
        standing_images={"x2": "x2app", W.BOARD: W.IMAGE})
    settings = dict(s, runs_root=os.path.join(root, "runs"),
                    bench_holder=holder, pause_poll_s=0.2)
    eng = E.Engine(suite, settings, out=lambda *a: None)
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
    assert x2f == [], x2f
    # a clean start, then the WiCAN reads another image: identify reads it,
    # the flash is refused, the bench stops
    rec, root, x2f, ev = _engine_run("changed", MAN_SHA, after=OTHER_SHA)
    v = {r["id"]: r["verdict"] for r in rec["results"]}
    assert v == {"g/c": "NOT-RUN"}, v
    d = rec["results"][0]["detail"]
    assert "never flashes the WiCAN" in d and OTHER_SHA in d, d
    fl = [e for e in ev if e.get("kind") == "flash"]
    assert [(e.get("image"), e.get("ok")) for e in fl] == [
        ("wican_man", False)], fl


TESTS = [test_source, test_hooks, test_identify_flasher, test_engine]

# --can-fail: one source edit of bench_wican.py each; every one must make
# a test fail
MUTANTS = [
    ("the glob takes any .bin (the real folder holds two)",
     'BIN_GLOB = "wican-fw_obd_*.bin"', 'BIN_GLOB = "*.bin"'),
    ("two matching bins accepted",
     "    if len(got) != 1:\n", "    if not got:\n"),
    ("no matching bin accepted",
     "    if len(got) != 1:\n", "    if len(got) > 1:\n"),
    ("a bin with no app descriptor accepted",
     "    if not sha:\n        raise WicanSourceError(\"%s carries",
     "    if False:\n        raise WicanSourceError(\"%s carries"),
    ("a missing chain settings file not named",
     "    if not os.path.isfile(path):\n",
     "    if False:\n"),
    ("chain settings that are not JSON not named",
     "    except ValueError as e:\n        raise WicanSourceError(\"gen-inhibit's",
     "    except KeyError as e:\n        raise WicanSourceError(\"gen-inhibit's"),
    ("no gi_images accepted", "    if not gi:\n", "    if False:\n"),
    ("a missing build folder not named",
     "    if not os.path.isdir(folder):\n", "    if False:\n"),
    ("a missing gen-inhibit tool accepted",
     "        if not os.path.isfile(p):\n", "        if False:\n"),
    ("an unset settings key accepted",
     "    if not v:\n        raise WicanSourceError(",
     "    if False:\n        raise WicanSourceError("),
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
    ("the flasher reports a flash",
     '        return dict(ok=False, phase="refused", recoverable=False,',
     '        return dict(ok=True, phase="refused", recoverable=False,'),
    ("the refusal is recoverable",
     '        return dict(ok=False, phase="refused", recoverable=False,',
     '        return dict(ok=False, phase="refused", recoverable=True,'),
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
    os.makedirs(TMP, exist_ok=True)
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
    src_path = os.path.join(HERE, "bench_wican.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    d = os.path.join(TMP, "mutants")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
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
    shutil.rmtree(d, ignore_errors=True)
    print("\n%d of %d mutants caught" % (len(MUTANTS) - bad, len(MUTANTS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(can_fail() if "--can-fail" in sys.argv[1:] else run_tests())
