"""The OTA flasher's retry policies (FLASH-SPEC O11, O13), each upload faked
(ota.flash_once replaced), so no network, no device, no Wi-Fi.

  dual-slot  gen-inhibit's suite_gen_inhibit.ota() at gen-inhibit/main
             40ecfa5e, through make_flasher(identify=..., images=...): the
             checks of its test_suite_gen_inhibit.py test_flashers "ota: ..."
             part, ported, and every "ota: ..." mutant of its
             test_suite_gen_inhibit_mutants.py, the same edit strings, applied
             to ota.py. Plus O13's new can-fail: a second failed attempt stops
             the run, shown through the real chain runner (a mutant that lets
             the run go on must be killed).
  none       one upload; a failed load is the flasher's failure, recoverable
             only when nothing was sent or the verdict left the device on its
             old image (user, 2026-11-11), with its mutants.

    py -3.14 -B shared/bench/tests/test_ota_policy.py             exit 0 only if every check passes
    py -3.14 -B shared/bench/tests/test_ota_policy.py --can-fail  the control, then every mutant
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import _ota_mutants                                  # noqa: E402
import _scratch                                      # noqa: E402
import _canmain_fixture                              # noqa: E402
sys.path.insert(0, _ota_mutants.package_root())
from vtrux_bench.run import ota as OTA               # noqa: E402
from vtrux_bench.run import engine as E              # noqa: E402
from vtrux_bench.run.model import Check, Group, Image, Suite  # noqa: E402

FAILS = []
SCRATCH = _scratch.run_dir("ota_policy")
CANMAIN = _canmain_fixture.install(SCRATCH)  # the runner's canmain check (SPEC 8), no hardware
SSID = "WiCAN_test_000000"
ROLE = "tester (gen-inhibit)"
SSID_ENV = "OTA_POLICY_TEST_SSID"     # O2: the SSID only through an environment variable
os.environ[SSID_ENV] = SSID
SETTINGS = {"ota": {"host": "192.0.2.1", "iface": "Wi-Fi", "profile": "wican-hotspot-test", "ssid_env": SSID_ENV,
                    "capture": False}}
TOOK, A1 = "...\nVERDICT: (c) TOOK -- page reads the image\n", "VERDICT: (a1) did NOT take: reset at 131072\n"


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL (CRASH)."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not ok:
        FAILS.append(label)


def make_image(name, elf):
    path = os.path.join(SCRATCH, "%s.bin" % name)
    blob = bytearray(0x400)
    blob[0] = 0xE9
    blob[0x20:0x24] = (0xABCD5432).to_bytes(4, "little")
    blob[0x30:0x38] = b"v-test-1"
    blob[0xB0:0xD0] = bytes.fromhex(elf)
    with open(path, "wb") as f:
        f.write(bytes(blob))
    return Image(name, "ota", {"bin": path, "retry": "dual-slot"}, elf, board="wican")


IMAGES = {im.name: im for im in (make_image("wican_man", "11" * 32), make_image("wican_rxq", "22" * 32),
                                 make_image("wican_prod", "33" * 32))}


class Uploads:
    """Stands in for ota.flash_once: each upload's (console, rc), in order; then
    ("", 1). Records each bin; `board` follows a load (rc 0 and TOOK)."""

    def __init__(self, outs, board=None):
        self.outs, self.bins, self.board = list(outs), [], board if board is not None else {}

    def __call__(self, args, hold=None):
        self.bins.append(args.bin)
        console, rc = self.outs.pop(0) if self.outs else ("", 1)
        m = re.search(r"VERDICT: \(([^)]*)\)", console)
        if rc == 0 and "VERDICT: (c) TOOK" in console:
            self.board["sha"] = args.expect
        return {"rc": rc, "console": console, "verdict": m.group(1) if m else None,
                "sent": bool(m), "stop": None}


def with_uploads(up, fn):
    saved = OTA.flash_once
    OTA.flash_once = up
    try:
        return fn()
    finally:
        OTA.flash_once = saved


def logdir():
    d = os.path.join(SCRATCH, "log_%d" % len(os.listdir(SCRATCH)))
    os.makedirs(d)
    return d


def dual_slot():
    wm, m2 = IMAGES["wican_man"], IMAGES["wican_rxq"]
    target, cur = wm.params["bin"], m2.params["bin"]

    def ota(outs, page=m2.expect):
        """outs: each upload's (stdout, rc), in order; page: what the WiCAN reads after
        the first attempt fails (a sha, or an exception to raise)."""
        def identify():
            if isinstance(page, Exception):
                raise page
            return page
        fl = OTA.make_flasher(identify=identify, images=IMAGES)
        up = Uploads(outs)
        d = logdir()

        def go():
            try:            # the suite's own flasher, which knows its images; it must never raise
                return fl(wm, wm.params, SETTINGS, d)
            except Exception as e:      # noqa: BLE001
                return {"ok": None, "recoverable": None, "phase": None, "detail": "RAISED %r" % e}
        r = with_uploads(up, go)
        return r, up.bins, sorted(os.listdir(d))

    r, bins, files = ota([(TOOK, 0)])
    check("ota: (c) TOOK with exit 0 is ok, one upload", lambda: r["ok"] and bins == [target], (r, bins))
    check("ota: ... phase ota, recoverable", lambda: r["phase"] == "ota" and r["recoverable"] is True, r)
    r, bins, files = ota([(A1, 1), (TOOK, 0), (TOOK, 0)])
    check("ota: a failed upload, the running build restored over itself, then the image again",
          lambda: r["ok"] and bins == [target, cur, target], (r, bins))
    check("ota: the first attempt's failure line is kept", lambda: "attempt1 wican_man: VERDICT: (a1)" in r["detail"], r)
    check("ota: every attempt's console is kept in the log folder",
          lambda: [f.split("_")[1] for f in files if f.endswith(".txt")] == ["attempt1", "attempt2", "restore"], files)
    r, bins, files = ota([(A1, 1), (TOOK, 0), (A1, 1)])
    check("ota: a second failure in a row is not ok (the core stops the run), the board restorable",
          lambda: not r["ok"] and r["recoverable"] is True and bins == [target, cur, target]
          and r["detail"].count("VERDICT: (a1)") == 2, (r, bins))
    check("ota: ... phase ota-twice", lambda: r["phase"] == "ota-twice", r)
    r, bins, files = ota([(A1, 1), (A1, 1)])
    check("ota: a failed restore is not ok, no third upload, no more WiCAN flashes",
          lambda: not r["ok"] and r["recoverable"] is False and bins == [target, cur] and r["phase"] == "restore",
          (r, bins))
    r, bins, files = ota([(A1, 1)], page="ee" * 32)
    check("ota: the WiCAN on a build this suite does not have: no restore, no more flashes",
          lambda: not r["ok"] and r["recoverable"] is False and bins == [target], (r, bins))
    r, bins, files = ota([(A1, 1)], page=OSError("no route"))
    check("ota: the page unreadable after a failure: no restore on an unknown state",
          lambda: not r["ok"] and r["recoverable"] is False and bins == [target] and "no route" in r["detail"],
          (r, bins))
    r, bins, files = ota([("VERDICT: (c) TOOK\n", 2), (TOOK, 0), ("VERDICT: (c) TOOK\n", 2)])
    check("ota: TOOK with a non-zero exit is not ok", lambda: not r["ok"] and len(bins) == 3, (r, bins))
    r, bins, files = ota([("", 0), (TOOK, 0), ("", 0)])
    check("ota: no VERDICT line is not ok", lambda: not r["ok"] and "no VERDICT" in r["detail"], r)
    r, bins, files = ota([(A1, 1), (TOOK, 0), (TOOK, 0)], page=wm.expect)
    check("ota: the board already on the image after a failure: that image restored, then tried again",
          lambda: r["ok"] and bins == [target, target, target], (r, bins))
    r, bins, files = ota([("VERDICT: (a2+c) NO STATUS, RESTARTED + TOOK -- x\n", 0), (TOOK, 0), (TOOK, 0)])
    check("ota: a2+c is not a load (O10): the restore and the retry follow",
          lambda: r["ok"] and bins == [target, cur, target], (r, bins))


def none_policy():
    im = Image("wican_man", "ota", dict(IMAGES["wican_man"].params, retry="none"), IMAGES["wican_man"].expect,
               board="wican")

    def run(outs):
        up = Uploads(outs)
        d = logdir()

        def go():
            try:
                return OTA.ota_flash(im, im.params, SETTINGS, d)
            except Exception as e:      # noqa: BLE001
                return {"ok": None, "recoverable": None, "phase": None, "detail": "RAISED %r" % e}
        return with_uploads(up, go), up.bins

    r, bins = run([(TOOK, 0)])
    check("none: (c) TOOK and a strict read is ok, one upload", lambda: r["ok"] is True and len(bins) == 1
          and r["phase"] == "ota", (r, bins))
    for v, want in (("a1", True), ("b", True), ("d", True), ("d'", True), ("e", True),
                    ("other", False), ("undecided", False), ("a2+c", False), ("a2+d", True), ("a2+e", True)):
        r, bins = run([("VERDICT: (%s) x -- y\n" % v, 1)])
        check("none: (%s) is a failure, one upload, recoverable %s" % (v, want),
              lambda: r["ok"] is False and len(bins) == 1 and r["recoverable"] is want and r["phase"] == "ota",
              (r, bins))
    r, bins = run([("VERDICT: (c) TOOK -- x\nFAIL ] status page parses\n", 1)])
    check("none: a (c) whose strict read failed is a failure, not recoverable (the image runs)",
          lambda: r["ok"] is False and r["recoverable"] is False, r)
    r, bins = run([("PREFLIGHT FAILED: joined the device's AP\n", 1)])
    check("none: a failure before anything was sent is recoverable, phase preflight",
          lambda: r["ok"] is False and r["recoverable"] is True and r["phase"] == "preflight" and len(bins) == 1, r)


def second_failure_stops_the_run():
    """O13's new can-fail: through the real chain runner, a dual-slot load that
    fails twice (attempt 1, the restore, attempt 2) stops the run, and nothing
    more is uploaded."""
    root = os.path.join(SCRATCH, "engine")
    os.makedirs(root)
    holder = os.path.join(root, "bench-holder.md")
    with open(holder, "w", encoding="utf-8") as f:
        f.write("# Bench holder\n\n## Lock\n\n**Holder:** %s (test)\n**Loaned to:** --\n\n## Board state\n" % ROLE)
    board = {"sha": IMAGES["wican_rxq"].expect}
    up = Uploads([(A1, 1), (TOOK, 0), (A1, 1), (TOOK, 0), (TOOK, 0)], board)
    fl = OTA.make_flasher(identify=lambda: board["sha"], images=IMAGES)
    s = Suite(name="t", role=ROLE, groups=[
        Group("g1", [Check("c1", ["-c", "pass"], python=True)], needs_lock=True, image="wican_man"),
        Group("g2", [Check("c2", ["-c", "pass"], python=True)], needs_lock=True, image="wican_prod")],
        images=dict(IMAGES), identify=lambda image, ctx, logdir_: board["sha"], flashers={"ota": fl})
    settings = dict(SETTINGS, runs_root=os.path.join(root, "runs"), bench_holder=holder,
                    hosts={"local": {"python": [sys.executable]}}, pause_poll_s=0.2,
                    canmain_check=dict(CANMAIN))

    def go():
        try:
            return E.Engine(s, settings, out=lambda *a: None).run()
        except Exception as e:      # noqa: BLE001
            return {"results": [], "error": repr(e)}
    rec = with_uploads(up, go)
    v = {r["id"]: r["verdict"] for r in rec.get("results", [])}
    check("O13: two failed attempts in a row stop the run: every bench case NOT-RUN",
          lambda: v == {"g1/c1": "NOT-RUN", "g2/c2": "NOT-RUN"}, (v, rec.get("error")))
    check("O13: ... and nothing is uploaded after the second attempt",
          lambda: [os.path.basename(b) for b in up.bins] == ["wican_man.bin", "wican_rxq.bin", "wican_man.bin"],
          [os.path.basename(b) for b in up.bins])


def main():
    dual_slot()
    none_policy()
    second_failure_stops_the_run()
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


OTA_PY = "run/ota.py"
MUTANTS = [(n, OTA_PY, o, w) for n, o, w in [
    # gen-inhibit's test_suite_gen_inhibit_mutants.py at 40ecfa5e, its "ota: ..." mutants unchanged
    ("ota: the exit is not looked at", 'ok = rc == 0 and "VERDICT: (c) TOOK" in t', 'ok = "VERDICT: (c) TOOK" in t'),
    ("ota: the verdict is not looked at", 'ok = rc == 0 and "VERDICT: (c) TOOK" in t', 'ok = rc == 0'),
    ("ota: no dual-slot restore before the retry",
     'ok, d2 = upload("restore", cur[0].name, cur[0].params["bin"], settings_, logdir)', 'ok, d2 = True, "skipped"'),
    ("ota: the restore uploads the wanted image, not the running build",
     'ok, d2 = upload("restore", cur[0].name, cur[0].params["bin"], settings_, logdir)',
     'ok, d2 = upload("restore", cur[0].name, params["bin"], settings_, logdir)'),
    ("ota: no second attempt", 'ok, d3 = upload("attempt2", image.name, params["bin"], settings_, logdir)',
     'ok, d3 = False, "no retry"'),
    ("ota: a failed restore is not looked at",
     '        if not ok:\n            return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=False)',
     '        pass'),
    ("ota: a failed restore allows more WiCAN flashes",
     'return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=False)',
     'return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=True)'),
    ("ota: any WiCAN image is taken as the running build",
     'cur = [im for im in images.values() if im.board == "wican" and im.expect == sha]',
     'cur = [im for im in images.values() if im.board == "wican"]'),
    ("ota: an unreadable page after a failure is not caught",
     '            sha = wican_sha()\n        except IdentifyError as e:', '            sha = wican_sha()\n        except ZeroDivisionError as e:'),
    ("ota: the first attempt's failure line is dropped",
     'detail="%s; %s; %s" % (d1, d2, d3)', 'detail="%s; %s" % (d2, d3)'),
    # O13's new can-fail: a second failure lets the run go on
    ("O13: a second failure is tried a third time",
     '        ok, d3 = upload("attempt2", image.name, params["bin"], settings_, logdir)\n',
     '        ok, d3 = upload("attempt2", image.name, params["bin"], settings_, logdir)\n'
     '        if not ok:\n            ok, d3 = upload("attempt3", image.name, params["bin"], settings_, logdir)\n'),
    ("O13: a second failure is reported as a load",
     'return dict(ok=ok, phase="ota" if ok else "ota-twice"', 'return dict(ok=True, phase="ota" if ok else "ota-twice"'),
    ("dual-slot: the identify's failure not wrapped", "            raise IdentifyError(str(e) or repr(e)) from e",
     "            raise"),
    # the none policy (O11)
    ("none: other leaves the old image", 'OLD_IMAGE = ("a1", "b", "d", "d\'", "e")',
     'OLD_IMAGE = ("a1", "b", "d", "d\'", "e", "other")'),
    ("none: e is not the old image", 'OLD_IMAGE = ("a1", "b", "d", "d\'", "e")', 'OLD_IMAGE = ("a1", "b", "d", "d\'")'),
    ("none: undecided leaves the old image", 'return (res.get("verdict") or "").split("+")[-1] in OLD_IMAGE',
     'return (res.get("verdict") or "").split("+")[-1] in OLD_IMAGE + ("undecided",)'),
    ("none: an a2+ verdict judged whole", 'return (res.get("verdict") or "").split("+")[-1] in OLD_IMAGE',
     'return (res.get("verdict") or "") in OLD_IMAGE'),
    ("none: nothing sent is not recoverable", '    if not res.get("sent"):\n        return True',
     '    if not res.get("sent"):\n        return False'),
    ("none: a failed load is retried", '        last = call.get("last") or {}\n',
     '        ok, d1 = upload("attempt2", image.name, params["bin"], settings_, logdir)\n'
     '        if ok:\n            return dict(ok=True, phase="ota", detail=d1, recoverable=True)\n'
     '        last = call.get("last") or {}\n'),
    ("none: runs the dual-slot policy", '(none if policy == "none" else ota)', '(ota if policy == "none" else ota)'),
    ("none: no preflight phase", 'phase="ota" if last.get("sent") else "preflight"', 'phase="ota"'),
]]


if __name__ == "__main__":
    if "--can-fail" in sys.argv[1:]:
        sys.exit(_ota_mutants.run_mutants(os.path.abspath(__file__), MUTANTS, "ota_policy"))
    sys.exit(main())
