"""The OTA flasher's preflight and its load (FLASH-SPEC O3, O5, O6, O7, O10,
O12), with the device, the network, the Wi-Fi and the clock faked. No
network, no device, no Wi-Fi.

Part one is gen-inhibit's test_bench_flash_preflight.py at gen-inhibit/main
40ecfa5e, ported (gen-inhibit tester, 2026-10-20, reviewer's ruling):
the preflight waits on /gen_inhibit for a page with elf_sha256 and uptime_ms,
and never asks for /check_status, whose answer carries credentials; the fake
/check_status answers with a made-up value so that a request for it shows.
Its mutants (test_bench_flash_preflight_mutants.py) are in MUTANTS, the same
edit strings, applied to ota.py. Its check "an armed device still fails the
preflight" is two here (O7): refused without a hold, disarmed with one.

Part two is the port's own: the image checks (O3; each refusal sends
nothing), the second-route preflight (O5), the disarm under the bench's hold
(O7: the POST alone trusted for nothing, a page that is not clean a stop with
recoverable false, mode=0 sent alone), the runner handing its hold to the
flasher, what counts as a load (O10: a2+c is not, nor a (c) whose strict read
fails), the rejoin during the watch and the upload's record, the SSID masked
(O12), and the by-hand command's hold.

    py -3.14 -B shared/bench/tests/test_ota_preflight.py             exit 0 only if every check passes
    py -3.14 -B shared/bench/tests/test_ota_preflight.py --can-fail  the control, then every mutant
"""
import contextlib
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
import _ota_mutants                                  # noqa: E402
import _scratch                                      # noqa: E402
sys.path.insert(0, _ota_mutants.package_root())
from vtrux_bench.run import ota as BF                # noqa: E402
from vtrux_bench.run import wican_http as G          # noqa: E402
from vtrux_bench.run import engine as E              # noqa: E402
from vtrux_bench.run.model import Check, Group, Image, Suite  # noqa: E402

FAILS = []
MADE_UP = "made-up-value-not-a-credential"
SHA = "ab" * 32
OLD, NEW = "aa" * 32, "bb" * 32
HOST = "192.0.2.1"               # RFC 5737 documentation addresses, as gen-inhibit's tests use
ROUTE = "198.51.100.7"
SSID = "WiCAN_test_000000"       # made up; the masking checks look for it
OTHER = "home_test_net"
PROFILE = "wican-hotspot-test"
IFACE = "Wi-Fi"
SCRATCH = _scratch.run_dir("ota_preflight")
ROLE = "tester (charge-interposer)"


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL (CRASH)."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "CRASH %s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, " | ".join(str(detail).splitlines())[:150]))
    if not ok:
        FAILS.append(label)


# ---- part one's fakes (the reference's) -----------------------------------

class Device:
    """Answers /gen_inhibit from a list of (status, body), the last one repeating,
    and /check_status with a config-shaped page. Records every path asked for."""

    def __init__(self, pages):
        self.pages, self.paths = list(pages), []

    def http(self, host, path, method="GET", body=None, ctype=None, timeout=10.0):
        self.paths.append(path.split("?")[0])
        if path.startswith("/check_status"):
            return 200, '{"wifi_mode":"AP","sta_pass":"%s","git_version":"v9"}' % MADE_UP
        if path.startswith("/gen_inhibit_set"):
            return 200, ""
        if path.startswith("/gen_inhibit"):
            return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        return 404, ""


def page(**kw):
    d = {"mode": 0, "elf_sha256": SHA, "uptime_ms": 4321, "reset_reason": "SW", "git_version": "v9"}
    d.update(kw)
    return 200, "{%s}" % ",".join('"%s":%s' % (k, "null" if v is None else
                                              ('"%s"' % v if isinstance(v, str) else v))
                                  for k, v in d.items())


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeTime:
    """For flash_once(): its own clock, so a wait that runs out costs no real time."""

    def __init__(self, clk=None):
        self.clk = clk or Clock()
        self.time, self.sleep = self.clk.now, self.clk.sleep

    @staticmethod
    def strftime(fmt):
        return "20261111T000000"


class FakeWifi:
    """wifi.py's calls, recorded. before: what current() reads."""

    def __init__(self, before=(OTHER, "connected"), join_ok=True, on=True, rejoin_ok=False):
        self.before, self.join_ok, self.on, self.rejoin_ok = before, join_ok, on, rejoin_ok
        self.calls = []

    def current(self, iface):
        self.calls.append(("current", iface))
        return self.before

    def join(self, profile, iface, ssid, timeout=40.0):
        self.calls.append(("join", profile, iface, ssid, timeout))
        return (True, "associated with %s" % ssid) if self.join_ok else \
            (False, "did not associate within 40 s (now %r / %r)" % (OTHER, "connected"))

    def on_ssid(self, iface, ssid):
        self.calls.append(("on_ssid", iface, ssid))
        return self.on

    def rejoin(self, profile, iface, ssid, timeout=20.0):
        self.calls.append(("rejoin", profile, iface, ssid, timeout))
        return (self.rejoin_ok, "rejoin %s" % ("ok" if self.rejoin_ok else "failed"))

    def named(self, what):
        return [c for c in self.calls if c[0] == what]


@contextlib.contextmanager
def faked(dev, wifi=None, routes=None, clk=None, post=None):
    saved = (G.http, BF.http, BF.have_external_route, BF.wifi, BF.time, BF.post_ota)
    G.http = BF.http = dev.http
    BF.time = FakeTime(clk)
    seq = list(routes or [(True, ROUTE)])
    BF.have_external_route = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    BF.wifi = wifi or FakeWifi()
    BF.post_ota = post or (lambda host, path: (_ for _ in ()).throw(AssertionError("posted")))
    try:
        yield
    finally:
        G.http, BF.http, BF.have_external_route, BF.wifi, BF.time, BF.post_ota = saved


def wait(pages, timeout=10.0):
    dev, clk = Device(pages), Clock()
    with faked(dev):
        got, err = BF.wait_for_device(HOST, timeout=timeout, now=clk.now, sleep=clk.sleep)
    return got, err, dev, clk


def args(**kw):
    a = dict(bin=None, host=HOST, ssid=SSID, profile=PROFILE, iface=IFACE, expect=NEW, pin={},
             flash=True, status=False, record=None, no_capture=True, capture_iface=IFACE, echo=False)
    a.update(kw)
    return types.SimpleNamespace(**a)


def run_status(pages, hold=None):
    """flash_once() as --status on the faked device: (exit code, console, paths asked)."""
    dev = Device(pages)
    try:
        with faked(dev):
            res = BF.flash_once(args(status=True, flash=False), hold)
    except Exception as e:      # noqa: BLE001
        return None, "  CRASH %s: %s" % (type(e).__name__, e), dev.paths
    return res["rc"], res["console"], dev.paths


# ---- part two's fakes -------------------------------------------------------

def make_image(path, elf, version=b"v-test-1", first=0xE9, magic=0xABCD5432):
    blob = bytearray(0x400)
    blob[0] = first
    blob[0x20:0x24] = magic.to_bytes(4, "little")
    blob[0x30:0x30 + len(version)] = version
    blob[0xB0:0xD0] = bytes.fromhex(elf)
    with open(path, "wb") as f:
        f.write(bytes(blob))
    return path


IMG = make_image(os.path.join(SCRATCH, "app_new.bin"), NEW)


class Bench:
    """A WiCAN on the fake clock: boot 0 runs OLD; after the post, the boots in
    `after` [(s after the upload's end, sha, reason)]. A page answers from 12 s
    of uptime. mode: the boot's mode; POST /gen_inhibit_set?mode=0 sets it to 0
    when `obeys`. flags: page fields laid over the page after a disarm."""

    def __init__(self, clk, after=(), code=303, mode=0, obeys=True, flags=None, junk_after=False,
                 page_after_disarm=True, junk_disarm=False):
        self.clk, self.after, self.code, self.mode, self.obeys = clk, list(after), code, mode, obeys
        self.flags, self.junk_after, self.page_after_disarm = flags or {}, junk_after, page_after_disarm
        self.junk_disarm = junk_disarm
        self.calls, self.posts, self.disarmed, self.t_end = [], [], False, None

    def boot(self):
        t = self.clk.t
        if self.t_end is None:
            return self.clk_start - 1000.0, OLD, "SW"
        cur = (self.clk_start - 1000.0, OLD, "SW")
        for dt, sha, why in self.after:
            if self.t_end + dt <= t:
                cur = (self.t_end + dt, sha, why)
        return cur

    def http(self, host, path, method="GET", body=None, ctype=None, timeout=10.0):
        self.calls.append((method, path, body, timeout))
        self.clk.t += 0.05
        if path.startswith("/check_status"):
            return 200, '{"wifi_mode":"AP","sta_pass":"%s","git_version":"v9"}' % MADE_UP
        if path.startswith("/gen_inhibit_set"):
            if self.obeys:
                self.mode = 0
            self.disarmed = True
            return 200, ""
        if path != "/gen_inhibit":
            return 404, ""
        if self.disarmed and not self.page_after_disarm:
            return 503, ""
        t0, sha, why = self.boot()
        up = self.clk.t - t0
        if up < 12.0:
            return None, "timed out"
        p = {"mode": self.mode, "elf_sha256": sha, "uptime_ms": int(up * 1000), "reset_reason": why,
             "git_version": "v9", "abort_latched": False, "abort_reason": "", "disabled": False,
             "disable_reason": "", "self_off": False}
        if self.disarmed:
            p.update(self.flags)
        text = json.dumps(p)
        if (self.junk_after and self.t_end is not None) or (self.junk_disarm and self.disarmed):
            text += " junk"
        return 200, text

    def post(self, host, path):
        self.posts.append(path)
        self.clk.t += 15.0
        self.t_end = self.clk.t
        return {"http_code": self.code, "size_upload": 1024, "time_total": 15.0, "body": "",
                "curl_error": "" if self.code else "curl: (56) Recv failure: Connection was reset",
                "curl_rc": 0 if self.code else 56}

    def paths(self):
        return [c[1].split("?")[0] for c in self.calls]


def flash(bench_kw=None, wifi=None, routes=None, hold=None, **akw):
    """flash_once() with --flash on a Bench: (result, bench, wifi)."""
    clk = Clock()
    b = Bench(clk, **(bench_kw or {}))
    b.clk_start = clk.t
    w = wifi or FakeWifi()
    try:
        with faked(b, w, routes, clk, b.post):
            res = BF.flash_once(args(**dict({"bin": IMG}, **akw)), hold)
    except Exception as e:      # noqa: BLE001 -- a crash fails the checks that read it
        res = {"rc": None, "console": "RAISED %r" % e, "verdict": None, "stop": None, "disarmed": None,
               "came_up_armed": None, "same_path": None}
    return res, b, w


def flasher(params, settings=None, bench_kw=None, wifi=None, routes=None, hold=None, image=None, logdir=None):
    """The built-in flasher on a Bench: (result, bench, wifi, logdir)."""
    clk = Clock()
    b = Bench(clk, **(bench_kw or {}))
    b.clk_start = clk.t
    w = wifi or FakeWifi()
    logdir = logdir or os.path.join(SCRATCH, "log_%d" % len(os.listdir(SCRATCH)))
    os.makedirs(logdir, exist_ok=True)
    settings = settings if settings is not None else {
        "ota": {"host": HOST, "iface": IFACE, "profile": PROFILE, "ssid": SSID, "capture": False}}
    image = image or Image("app_new", "ota", params, NEW, board="wican")
    try:
        with faked(b, w, routes, clk, b.post):
            r = BF.ota_flash(image, params, settings, logdir, hold=hold)
    except Exception as e:      # noqa: BLE001 -- a flasher must never raise
        r = {"ok": None, "phase": "RAISED", "recoverable": None, "detail": "RAISED %r" % e}
    return r, b, w, logdir


HELD = (lambda: (True, "held by %s, pid 1, since now" % ROLE))
TOOK = [(2.5, NEW, "SW")]


def part_one():
    got, err, dev, clk = wait([page()])
    check("a page with elf_sha256 and uptime_ms is the device", lambda: got and got["elf_sha256"] == SHA
          and got["uptime_ms"] == 4321 and err is None, (got, err))
    check("waiting asks only for /gen_inhibit", lambda: dev.paths and set(dev.paths) == {"/gen_inhibit"}, dev.paths)

    got, err, dev, clk = wait([(503, ""), (200, "not json"), page()])
    check("a 503 and a non-JSON answer are waited through", lambda: got and got["elf_sha256"] == SHA
          and len(clk.sleeps) == 2, (got, err, clk.sleeps))

    st, body = page()
    got, err, dev, clk = wait([(st, body + " junk")])
    check("a page with trailing bytes is read leniently (the image about to be replaced)",
          lambda: got and got["elf_sha256"] == SHA and "_trailing" in got, (got, err))

    got, err, dev, clk = wait([page(elf_sha256=None)])
    check("a page without elf_sha256 is not the device, and the error names it",
          lambda: got is None and "elf_sha256" in (err or "") and "/check_status" not in dev.paths, err)
    got, err, dev, clk = wait([page(uptime_ms=None)])
    check("a page without uptime_ms is not the device, and the error names it",
          lambda: got is None and "uptime_ms" in (err or ""), err)
    check("it gives up at the timeout", lambda: clk.t - 1000.0 <= 12.0 and len(clk.sleeps) >= 4, (clk.t, clk.sleeps))
    got, err, dev, clk = wait([(503, "")], timeout=60.0)
    check("O6: every 2 s for up to 60 s", lambda: got is None and set(clk.sleeps) == {2.0}
          and 59.0 <= clk.t - 1000.0 <= 62.0, (clk.t - 1000.0, set(clk.sleeps)))

    rc, out, paths = run_status([page()])
    check("--status on an OFF device passes with no config read", lambda: rc == 0, out[-300:])
    check("--status never asks for /check_status", lambda: "/check_status" not in paths and paths, paths)
    check("nothing from the config reaches the output",
          lambda: MADE_UP not in out and "fw_version" not in out and "hw=" not in out, out[-300:])
    check("the output names the image and the boot from /gen_inhibit",
          lambda: SHA[:16] in out and "4321" in out, out[-300:])
    rc, out, paths = run_status([page(mode=1)])
    check("O7: an armed device fails the preflight without a hold (spec 9.3), and nothing is disarmed",
          lambda: rc == 1 and "FAIL ] device is OFF" in out and "/gen_inhibit_set" not in paths, out[-300:])
    rc, out, paths = run_status([page(elf_sha256=None)])
    check("--status fails when /gen_inhibit names no image", lambda: rc == 1 and "device answers" in out
          and "/check_status" not in paths, out[-300:])


def o3():
    good = {"bin": IMG, "retry": "none"}
    bad = [
        ("no file", dict(good, bin=os.path.join(SCRATCH, "missing.bin")), NEW),
        ("byte 0 is not 0xE9", dict(good, bin=make_image(os.path.join(SCRATCH, "e8.bin"), NEW, first=0xE8)), NEW),
        ("no descriptor magic", dict(good, bin=make_image(os.path.join(SCRATCH, "nomagic.bin"), NEW, magic=1)), NEW),
        ("app_elf_sha256 is not Image.expect", good, OLD),
        ("Image.expect empty", good, ""),
        ("a pinned size that differs", dict(good, size=0x401), NEW),
        ("a pinned sha256 that differs", dict(good, sha256="00" * 32), NEW),
        ("a pinned md5 that differs", dict(good, md5="00" * 16), NEW),
    ]
    for what, params, expect in bad:
        r, b, w, _ = flasher(params, image=Image("app_new", "ota", params, expect, board="wican"))
        check("O3: %s: refused, nothing sent" % what,
              lambda: r["ok"] is False and r["phase"] == "image" and r["recoverable"] is True
              and not b.calls and not b.posts and not w.calls and "nothing sent" in r["detail"], (r, b.calls, w.calls))
    import hashlib
    data = open(IMG, "rb").read()
    pinned = dict(good, size=len(data), sha256=hashlib.sha256(data).hexdigest(), md5=hashlib.md5(data).hexdigest())
    r, b, w, _ = flasher(pinned, bench_kw={"after": TOOK})
    check("O3: a pinned image matching its pin and identity is loaded", lambda: r["ok"] is True and b.posts == [IMG],
          (r, b.posts))
    res, b, w = flash(expect=OLD)
    check("O3: flash_once itself refuses an image that is not its identity, before the network",
          lambda: res["rc"] == 1 and "PREFLIGHT FAILED before touching the network" in res["console"]
          and not b.calls and not w.calls, res["console"][-300:])
    res, b, w = flash(pin={"md5": "00" * 16})
    check("O3: flash_once itself refuses a pin that differs", lambda: res["rc"] == 1 and not b.calls,
          res["console"][-300:])
    res, b, w = flash(bin=make_image(os.path.join(SCRATCH, "e8_once.bin"), NEW, first=0xE8))
    check("O3: flash_once itself refuses an image whose byte 0 is not 0xE9", lambda: res["rc"] == 1
          and "FAIL ] image starts with 0xE9" in res["console"] and not b.calls, res["console"][-300:])


def o5():
    res, b, w = flash(routes=[(False, None)])
    check("O5: no route before the join: refused, nothing joined or sent",
          lambda: res["rc"] == 1 and not w.named("join") and not b.calls and "FAIL ] internet before the join"
          in res["console"], res["console"][-300:])
    res, b, w = flash(routes=[(True, "192.0.2.77")])
    check("O5: a route from the hotspot's own subnet is not internet: refused",
          lambda: res["rc"] == 1 and not w.named("join") and "FAIL ] that internet is not the device" in res["console"],
          res["console"][-300:])
    res, b, w = flash(routes=[(True, ROUTE), (False, None)])
    check("O5: the route lost by the join: refused after the join, nothing sent",
          lambda: res["rc"] == 1 and w.named("join") and not b.posts and "FAIL ] internet after the join"
          in res["console"], res["console"][-300:])
    res, b, w = flash(routes=[(True, ROUTE), (True, "192.0.2.50")])
    check("O5: after the join, a route through the hotspot is refused",
          lambda: res["rc"] == 1 and not b.posts and "FAIL ] internet is still not the device" in res["console"],
          res["console"][-300:])
    res, b, w = flash(bench_kw={"after": TOOK}, routes=[(True, ROUTE), (True, "203.0.113.9")])
    check("O5: another path after the join is recorded, not required",
          lambda: res["rc"] == 0 and res["same_path"] is False and "the same path as before" in res["console"],
          res["console"][-300:])
    res, b, w = flash(wifi=FakeWifi(join_ok=False))
    check("O4: a join that fails is a failure, and nothing is sent",
          lambda: res["rc"] == 1 and not b.calls and "FAIL ] joined the device's AP" in res["console"],
          res["console"][-300:])
    res, b, w = flash(bench_kw={"after": TOOK})
    check("O4: the join is the saved profile on the interface, to the SSID, 40 s",
          lambda: w.named("join") == [("join", PROFILE, IFACE, SSID, 40.0)], w.named("join"))
    check("O6: the whole load asks for /gen_inhibit only (and never a config)",
          lambda: set(b.paths()) == {"/gen_inhibit"}, set(b.paths()))


def o7():
    res, b, w = flash(bench_kw={"mode": 3}, status=True, flash=False, bin=None)
    check("O7: armed, no hold: refused, nothing posted", lambda: res["rc"] == 1 and "/gen_inhibit_set" not in b.paths()
          and res["came_up_armed"] is True and not res["disarmed"] and res["stop"] is None, res["console"][-300:])
    res, b, w = flash(bench_kw={"mode": 3}, status=True, flash=False, bin=None, hold=lambda: (False, "the lock is free"))
    check("O7: armed, a hold that says not held: refused, nothing posted",
          lambda: res["rc"] == 1 and "/gen_inhibit_set" not in b.paths() and "the lock is free" in res["console"],
          res["console"][-300:])

    def boom():
        raise OSError("the log is locked")
    res, b, w = flash(bench_kw={"mode": 3}, status=True, flash=False, bin=None, hold=boom)
    check("O7: armed, a hold that cannot be read: refused, nothing posted",
          lambda: res["rc"] == 1 and "/gen_inhibit_set" not in b.paths(), res["console"][-300:])
    res, b, w = flash(bench_kw={"mode": 3}, status=True, flash=False, bin=None, hold=HELD)
    sets = [c for c in b.calls if c[1].startswith("/gen_inhibit_set")]
    check("O7: armed, held: disarmed, and the preflight passes", lambda: res["rc"] == 0 and res["disarmed"] is True
          and res["came_up_armed"] is True and res["stop"] is None, res["console"][-300:])
    check("O7: the disarm is POST /gen_inhibit_set?mode=0 alone, with an empty body",
          lambda: [(m, p, bd) for m, p, bd, _t in sets] == [("POST", "/gen_inhibit_set?mode=0", b"")], sets)
    after = b.calls[b.calls.index(sets[0]) + 1:] if sets else []
    check("O7: the disarm's POST and reads each have 8 s", lambda: sets and sets[0][3] == 8.0 and after
          and after[0][3] == 8.0, (sets, after[:1]))
    res, b, w = flash(bench_kw={"mode": 3, "junk_disarm": True}, status=True, flash=False, bin=None, hold=HELD)
    check("O7: the page after the disarm is read leniently", lambda: res["rc"] == 0 and res["disarmed"] is True,
          res["console"][-300:])
    clk_sleeps = []
    res, b, w = flash(bench_kw={"mode": 3, "obeys": False}, status=True, flash=False, bin=None, hold=HELD)
    check("O7: the POST alone is not trusted: a page still armed is a stop",
          lambda: res["rc"] == 1 and res["stop"] and "mode" in res["stop"] and not res["disarmed"], res["stop"])
    for name, flags, word in (("latched", {"abort_latched": True, "abort_reason": "made-up"}, "abort not latched"),
                              ("disabled", {"disabled": True, "disable_reason": "made-up"}, "not disabled"),
                              ("a disable reason", {"disable_reason": "made-up"}, "not disabled"),
                              ("self-off", {"self_off": "made-up"}, "not self-off")):
        res, b, w = flash(bench_kw={"mode": 3, "flags": flags}, status=True, flash=False, bin=None, hold=HELD)
        check("O7: a page %s after the disarm is not clean: a stop" % name,
              lambda: res["rc"] == 1 and res["stop"] and word in res["stop"], res["stop"])
    clk = Clock()
    b = Bench(clk, mode=3, page_after_disarm=False)
    b.clk_start = clk.t
    try:
        with faked(b, FakeWifi(), None, clk, b.post):
            res = BF.flash_once(args(status=True, flash=False, bin=None), HELD)
    except Exception as e:      # noqa: BLE001
        res = {"stop": None, "console": "RAISED %r" % e}
    at = [i for i, c in enumerate(b.calls) if c[1].startswith("/gen_inhibit_set")]
    reads = b.calls[at[0] + 1:] if at else []
    check("O7: no page after the disarm: 4 reads, 3 s apart, then a stop",
          lambda: res["stop"] and len(reads) == 4 and clk.sleeps.count(3.0) == 3, (len(reads), clk.sleeps, res["stop"]))
    for what, kw in (("still armed", {"mode": 3, "obeys": False}),
                     ("latched", {"mode": 3, "flags": {"abort_latched": True}})):
        r, b, w, _ = flasher({"bin": IMG, "retry": "none"}, bench_kw=kw, hold=HELD)
        check("O7: a disarm that does not read clean stops the flasher, recoverable false (%s)" % what,
              lambda: r["ok"] is False and r["recoverable"] is False and r["phase"] == "disarm" and not b.posts, r)
    r, b, w, logdir = flasher({"bin": IMG, "retry": "none"}, bench_kw={"mode": 2, "after": TOOK}, hold=HELD)
    recs = [f for f in os.listdir(logdir) if f.endswith(".json")]
    rec = json.load(open(os.path.join(logdir, recs[0]))) if recs else {}
    check("O7: held, an armed device is disarmed and loaded; the record says it came up armed and was disarmed",
          lambda: r["ok"] is True and rec.get("came_up_armed") is True and rec.get("disarmed") is True, (r, rec.get("came_up_armed")))


def o7_engine():
    """The chain runner hands its own lock check to a flasher that takes a hold."""
    root = os.path.join(SCRATCH, "engine")
    os.makedirs(root, exist_ok=True)
    holder = os.path.join(root, "bench-holder.md")
    board = {"read": None}

    def set_holder(h):
        with open(holder, "w", encoding="utf-8") as f:
            f.write("# Bench holder\n\n## Lock\n\n**Holder:** %s\n**Loaned to:** --\n\n## Board state\n" % h)
    set_holder(ROLE + " (test)")
    seen = {}

    def probe(image, params, settings, logdir, hold=None):
        seen["held"] = hold() if hold else None
        set_holder("tester (gen-inhibit) (test)")
        seen["lost"] = hold() if hold else None
        set_holder(ROLE + " (test)")
        board["read"] = image.expect
        return dict(ok=True, phase="done", detail="probe", recoverable=True)
    probe.takes_hold = True
    s = Suite(name="t", role=ROLE, groups=[Group("g", [Check("c", ["-c", "pass"], python=True)],
                                                 needs_lock=True, image="w")],
              images={"w": Image("w", "probe", {}, expect="W", board="wican")},
              identify=lambda image, ctx, logdir: board["read"], flashers={"probe": probe})
    settings = dict(runs_root=os.path.join(root, "runs"), bench_holder=holder,
                    hosts={"local": {"python": [sys.executable]}}, pause_poll_s=0.2)
    try:
        E.Engine(s, settings, out=lambda *a: None).run()
    except Exception as e:      # noqa: BLE001
        seen["error"] = repr(e)
    check("O7: the runner gives a flasher that takes a hold its own lock check, held",
          lambda: seen.get("held") and seen["held"][0] is True, seen)
    check("O7: ... asked at the moment, so a lock lost since the case began is not held",
          lambda: seen.get("lost") and seen["lost"][0] is False, seen)


def o10_o12():
    r, b, w, logdir = flasher({"bin": IMG, "retry": "none"}, bench_kw={"after": TOOK})
    check("O10: (c) TOOK and a strict read: a load", lambda: r["ok"] is True and r["phase"] == "ota"
          and "VERDICT: (c) TOOK" in r["detail"], r)
    r, b, w, _ = flasher({"bin": IMG, "retry": "none"}, bench_kw={"after": TOOK, "code": None})
    check("O10: a2+c is not a load", lambda: r["ok"] is False and "(a2+c)" in r["detail"], r)
    res, b, w = flash(bench_kw={"after": TOOK, "junk_after": True})
    check("O10: a (c) whose strict read fails is not a load (rc 1, the page unreadable)",
          lambda: res["verdict"] == "c" and res["rc"] == 1 and "FAIL ] status page parses" in res["console"],
          res["console"][-300:])
    r, b, w, _ = flasher({"bin": IMG, "retry": "none"}, bench_kw={"after": TOOK, "junk_after": True})
    check("O10: ... and the flasher fails it", lambda: r["ok"] is False and r["recoverable"] is False, r)
    res, b, w = flash(bench_kw={"after": [(2.5, OLD, "SW")]})
    check("O10: (e) is not a load, rc 1", lambda: res["verdict"] == "e" and res["rc"] == 1, (res["verdict"], res["rc"]))

    # O12: the rejoin during the watch
    down = [(2.5, NEW, "SW")]
    res, b, w = flash(bench_kw={"after": down}, wifi=FakeWifi(on=True))
    check("O12: a failed read on the WiCAN's SSID does not rejoin", lambda: w.named("on_ssid") and not w.named("rejoin"),
          w.calls)
    res, b, w = flash(bench_kw={"after": down}, wifi=FakeWifi(on=False))
    check("O12: a failed read off the WiCAN's SSID rejoins, with 20 s",
          lambda: w.named("rejoin") and all(c == ("rejoin", PROFILE, IFACE, SSID, 20.0) for c in w.named("rejoin")),
          w.named("rejoin")[:2])
    check("O12: a failed rejoin is a missed reading, and the watch goes on to its verdict",
          lambda: res["verdict"] == "c" and res["rc"] == 0, (res["verdict"], res["rc"]))
    check("O12: the PC stays on the hotspot: every join is the WiCAN's profile",
          lambda: all(c[1] == PROFILE and c[3] == SSID for c in w.named("join") + w.named("rejoin")), w.calls)

    # O12: the record, the SSID masked
    w = FakeWifi(before=(SSID, "connected"))
    r, b, w, logdir = flasher({"bin": IMG, "retry": "none"}, bench_kw={"after": TOOK}, wifi=w)
    names = sorted(os.listdir(logdir))
    texts = {n: open(os.path.join(logdir, n), encoding="utf-8").read() for n in names}
    js = [n for n in names if n.endswith(".json")]
    rec = json.loads(texts[js[0]]) if js else {}
    want = ("image", "image_elf_sha256", "image_expect", "image_version", "image_bytes", "wifi_before", "before",
            "t_start", "t_end", "upload", "readings", "boots", "settle_uptime_ms", "verdict", "verdict_name",
            "detail", "wire", "came_up_armed", "disarmed")
    check("O12: the upload's console and record are in the log folder",
          lambda: len(js) == 1 and any(n.endswith(".txt") for n in names), names)
    check("O12: the record carries what O12 lists", lambda: all(k in rec for k in want) and rec["boots"]
          and rec["readings"] and rec["upload"]["http_code"] == 303 and rec["verdict"] == "c",
          [k for k in want if k not in rec])
    check("O12: the network before the join is recorded", lambda: (rec.get("wifi_before") or {}).get("state") == "connected",
          rec.get("wifi_before"))
    check("O12: the SSID is in no file and not in the result", lambda: all(SSID not in t for t in texts.values())
          and SSID not in r["detail"], [n for n, t in texts.items() if SSID in t])
    check("O12: ... it is there masked", lambda: any(BF.SSID_MASK in t for t in texts.values()), names)
    res, b, w = flash(bench_kw={"after": TOOK})
    check("O12: the console masks the SSID as it is written", lambda: SSID not in res["console"]
          and BF.SSID_MASK in res["console"], res["console"][:200])


def o11_none_policy_refusals():
    r, b, w, _ = flasher({"bin": IMG})
    check("O11: no retry policy named on the image: refused, nothing sent",
          lambda: r["ok"] is False and r["phase"] == "policy" and not b.calls and not w.calls, r)
    r, b, w, _ = flasher({"bin": IMG, "retry": "dual-slot"})
    check("O11: the built-in alone refuses dual-slot (no identify, no images), nothing sent",
          lambda: r["ok"] is False and r["phase"] == "policy" and not b.calls and not w.calls, r)
    r, b, w, _ = flasher({"bin": IMG, "retry": "none"}, settings={"ota": {"host": HOST, "iface": IFACE}})
    check("O2: settings without a profile or SSID: refused, nothing sent",
          lambda: r["ok"] is False and r["phase"] == "settings" and not b.calls, r)
    from vtrux_bench.run import flash as FL
    builtin = FL.BUILTIN.get("ota")
    check("O1: \"ota\" is a built-in flasher beside esptool, and takes the runner's hold",
          lambda: builtin is not None and "esptool" in FL.BUILTIN and getattr(builtin, "takes_hold", False) is True)
    seen = {}

    def spy(image, params, settings, logdir, hold=None):
        seen["hold"] = hold
        return {"ok": False, "phase": "spy", "detail": "", "recoverable": True}
    saved = BF.ota_flash
    BF.ota_flash = spy
    try:
        builtin(Image("x", "ota", {}, NEW), {}, {}, SCRATCH, hold=HELD)
    except Exception as e:      # noqa: BLE001
        seen["error"] = repr(e)
    finally:
        BF.ota_flash = saved
    check("O1: the built-in passes the hold on to ota.py's flasher", lambda: seen.get("hold") is HELD, seen)

    class Boom(FakeWifi):
        def current(self, iface):
            raise RuntimeError("no Wi-Fi backend")
    r, b, w, logdir = flasher({"bin": IMG, "retry": "none"}, wifi=Boom())
    txt = [open(os.path.join(logdir, n), encoding="utf-8").read() for n in os.listdir(logdir) if n.endswith(".txt")]
    check("a crash inside an upload is a failed load, never a raise: nothing sent, recoverable",
          lambda: r["ok"] is False and r["phase"] == "preflight" and r["recoverable"] is True and not b.posts
          and txt and "CRASHED: RuntimeError" in txt[0], (r, txt[:1]))
    os.environ["OTA_TEST_SSID_ENV"] = SSID
    r, b, w, _ = flasher({"bin": IMG, "retry": "none"}, bench_kw={"after": TOOK},
                         settings={"ota": {"iface": IFACE, "profile": PROFILE, "ssid_env": "OTA_TEST_SSID_ENV",
                                           "capture": False, "host": HOST}})
    check("O2: the SSID from an environment variable the settings name",
          lambda: r["ok"] is True and w.named("join")[0][3] == SSID, (r, w.named("join")))


def cli():
    if os.name != "nt":
        print("ok    the by-hand command's hold: not checked here (bench_lock is native Windows only)")
        return
    from vtrux_bench import bench_lock as BL
    root = os.path.join(SCRATCH, "cli")
    os.makedirs(root, exist_ok=True)
    log = os.path.join(root, "bench-log.jsonl")
    BL.init(log, ROLE)
    st = os.path.join(root, "settings.json")
    with open(st, "w", encoding="utf-8") as f:
        json.dump({"bench_lock_log": log}, f)
    logdir = os.path.join(os.path.dirname(_scratch.worktree_of(HERE)), "not-a-worktree-ota-test")

    def run(*extra):
        out = io.StringIO()
        argv = ["--bin", IMG, "--expect", NEW, "--settings", st, "--who", ROLE,
                "--logdir", logdir] + list(extra)
        try:
            with contextlib.redirect_stdout(out):
                rc = BF.main(argv)
        except SystemExit as e:
            rc = "exit %s" % e.code
        except Exception as e:      # noqa: BLE001
            rc = "raised %r" % e
        return rc, out.getvalue()
    rc, out = run("--holder-pid", str(os.getpid()))
    check("by hand: the lock free, refused before anything", lambda: rc == 2 and "not held" in out, (rc, out))
    BL.take(log, ROLE, os.getpid())
    rc, out = run("--holder-pid", str(os.getpid() + 1))
    check("by hand: the lock held under another pid, refused", lambda: rc == 2 and "not held" in out, (rc, out))
    rc, out = run("--holder-pid", str(os.getpid()))
    check("by hand: held under this pid, the hold is accepted (the flasher then refuses the settings)",
          lambda: rc == 2 and "not held" not in out and "settings.ota has no" in out, (rc, out))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc2 = BF.main(["--bin", IMG, "--expect", NEW, "--settings", st, "--who", ROLE, "--holder-pid",
                       str(os.getpid()), "--logdir", os.path.join(SCRATCH, "inside")])
    check("by hand: a log folder inside a git worktree is refused",
          lambda: rc2 == 2 and "inside the git worktree" in out.getvalue(), out.getvalue())
    check("by hand: nothing was made outside the worktree", lambda: not os.path.exists(logdir), logdir)


def main():
    part_one()
    o3()
    o5()
    o7()
    o7_engine()
    o10_o12()
    o11_none_policy_refusals()
    cli()
    print("\n%s" % ("%d FAIL" % len(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


OTA = "run/ota.py"
WAIT_READ = "        page, err = gi_status(host, strict=False)\n"
MAIN_WAIT = "    st, err = wait_for_device(args.host)\n"
REPORTS = '''        % (str(st.get("elf_sha256"))[:16], st.get("uptime_ms")))\n'''
MUTANTS = [(n, OTA, o, w) for n, o, w in [
    # gen-inhibit's test_bench_flash_preflight_mutants.py at 40ecfa5e, unchanged
    ("/check_status read again while waiting", WAIT_READ,
     '        http(host, "/check_status")\n' + WAIT_READ),
    ("the config line printed again", MAIN_WAIT,
     '    cfg = json.loads(http(args.host, "/check_status")[1])\n'
     '    say(True, "device reports", "git_version=%r fw_version=%r hw=%r"\n'
     '        % (cfg.get("git_version"), cfg.get("fw_version"), cfg.get("hw_version")))\n' + MAIN_WAIT),
    ("the wifi_mode check put back", REPORTS,
     REPORTS + '    need(st.get("wifi_mode") == "AP", "wifi_mode is AP", "wifi_mode=%r" % st.get("wifi_mode"))\n'),
    ("elf_sha256 not required", 'for k in ("elf_sha256", "uptime_ms")', 'for k in ("uptime_ms",)'),
    ("uptime_ms not required", 'for k in ("elf_sha256", "uptime_ms")', 'for k in ("elf_sha256",)'),
    ("a null field counts as present", "if page.get(k) is None]", "if k not in page]"),
    ("the first failed read gives up", '        last = err or "no answer"\n',
     '        return None, err or "no answer"\n'),
    ("the waiting read is strict", WAIT_READ, "        page, err = gi_status(host, strict=True)\n"),
    ("the mode is not checked", 'need(mode == 0, "device is OFF, so OTA is permitted",',
     'need(True, "device is OFF, so OTA is permitted",'),
    ("the report names no image", '''    say(True, "device reports", "elf_sha256 %s... uptime %s ms"''',
     '''    say(True, "device reports", "elf_sha256 %.0s... uptime %s ms"'''),
    # O3
    ("O3: the flasher does not refuse a bad image itself", '        if bad:\n            return refuse("image",',
     '        if False:\n            return refuse("image",'),
    ("O3: any identity will do", "bool(want) and got == want,", "True,"),
    ("O3: no identity is any identity", "bool(want) and got == want,", "got == want or not want,"),
    ("O3: byte 0 not checked", "data[:1] == bytes([ESP_IMAGE_MAGIC]),", "True,"),
    ("O3: the pinned size not checked", 'if pin.get("size") is not None:', "if False:"),
    ("O3: the pinned md5 not checked", 'for algo in ("sha256", "md5"):', 'for algo in ("sha256",):'),
    ("O3: the md5 pin not taken from the image", 'for k in ("size", "sha256", "md5") if params.get(k)',
     'for k in ("size", "sha256") if params.get(k)'),
    ("O3: flash_once does not check the identity", "for what, ok, detail in check_image(args.bin, args.expect, args.pin):",
     "for what, ok, detail in []:"),
    ("O3: flash_once does not check byte 0", "for what, ok, detail in check_image(args.bin, args.expect, args.pin):",
     "for what, ok, detail in check_image(args.bin, args.expect, args.pin)[1:]:"),
    # O5
    ("O5: no route before is no failure", 'need(ok, "internet before the join",', 'need(True, "internet before the join",'),
    ("O5: the hotspot's subnet is internet before", "need(not str(local_before).startswith(ap),", "need(True,"),
    ("O5: no route after is no failure", 'need(ok, "internet after the join",', 'need(True, "internet after the join",'),
    ("O5: the hotspot's subnet is internet after", "need(not str(local_after).startswith(ap),", "need(True,"),
    ("O5: the same path required", 'say(local_after == local_before, "the same path as before",',
     'need(local_after == local_before, "the same path as before",'),
    ("O4: a failed join is not a failure", 'need(ok, "joined the device\'s AP", why)', 'need(True, "joined the device\'s AP", why)'),
    ("O4: the join with the SSID as the profile", "ok, why = wifi.join(args.profile, args.iface, args.ssid)",
     "ok, why = wifi.join(args.ssid, args.iface, args.ssid)"),
    # O7
    ("O7: disarmed without a hold", '    if not held:\n        say(None, "not disarmed"', '    if False:\n        say(None, "not disarmed"'),
    ("O7: the hold's answer not read", "return bool(got[0]), str(got[1])", "return True, str(got[1])"),
    ("O7: a hold that cannot be read is held", 'return False, "the hold could not be read: %s: %s"',
     'return True, "the hold could not be read: %s: %s"'),
    ("O7: the disarm sends an offset", "set_mode(args.host, 0, offset=None, timeout=DISARM_TIMEOUT_S)",
     "set_mode(args.host, 0, offset=500, timeout=DISARM_TIMEOUT_S)"),
    ("O7: the POST trusted", "    page = read_tries(args.host, DISARM_TRIES, DISARM_GAP_S, DISARM_TIMEOUT_S, errors)\n",
     '    page = {"mode": 0, "reasons": {"abort_latched": False}, "disabled": False, "self_off": False}\n'),
    ("O7: the page after the disarm not judged", "    bad = [(n, d) for n, ok, d in clean(page) if not ok]", "    bad = []"),
    ("O7: a latch ignored", 'r.get("abort_latched") is False,', "True,"),
    ("O7: disabled ignored", 'p.get("disabled") is False and r.get("disable_reason") in ("", None),', "True,"),
    ("O7: a disable reason ignored", 'p.get("disabled") is False and r.get("disable_reason") in ("", None),',
     'p.get("disabled") is False,'),
    ("O7: self-off ignored", 'p.get("self_off") in ("", None, False)', "True"),
    ("O7: 2 reads, not 4", "DISARM_TRIES = 4", "DISARM_TRIES = 2"),
    ("O7: 1 s apart, not 3", "DISARM_GAP_S = 3.0", "DISARM_GAP_S = 1.0"),
    ("O7: 10 s each, not 8", "DISARM_TIMEOUT_S = 8.0", "DISARM_TIMEOUT_S = 10.0"),
    ("O7: the disarm's read strict", "p, _ = json.JSONDecoder().raw_decode(body.lstrip())", "p = json.loads(body)"),
    ("O7: a failed disarm is recoverable", "out = dict(ok=False, phase=e.phase, recoverable=False,",
     "out = dict(ok=False, phase=e.phase, recoverable=True,"),
    ("O7: a failed disarm does not stop the flasher", '        if res.get("stop"):\n            raise Stopped(',
     '        if False:\n            raise Stopped('),
    ("O7: the record forgets the disarm", '"came_up_armed": res.get("came_up_armed"), "disarmed": res.get("disarmed"),',
     '"came_up_armed": None, "disarmed": None,'),
    # O10
    ("O10: a2+c is a load", 'ok = rc == 0 and "VERDICT: (c) TOOK" in t', "ok = rc == 0"),
    ("O10: the strict read lenient", "            page = json.loads(body)",
     "            page = json.JSONDecoder().raw_decode(body.lstrip())[0]"),
    ("O10: no strict read", '    st, body = http(args.host, "/gen_inhibit")\n    if st != 200:',
     '    st, body = 200, "{}"\n    if st != 200:'),
    ("O10: any verdict took", 'need(verdict.split("+")[-1] == "c", "the OTA took",', 'need(True, "the OTA took",'),
    # O12
    ("O12: a failed read rejoins on the SSID too", "            if not wifi.on_ssid(args.iface, args.ssid):\n",
     "            if True:\n"),
    ("O12: no rejoin", "                wifi.rejoin(args.profile, args.iface, args.ssid, timeout=20.0)",
     "                pass"),
    ("O12: the rejoin with 40 s", "wifi.rejoin(args.profile, args.iface, args.ssid, timeout=20.0)",
     "wifi.rejoin(args.profile, args.iface, args.ssid, timeout=40.0)"),
    ("O12: the console not masked", "        s = mask(s, self.ssid)\n", ""),
    ("O12: the record not masked", "write_masked(args.record, json.dumps(rec, indent=1), args.ssid)",
     "write_masked(args.record, json.dumps(rec, indent=1), None)"),
    ("O12: the record without its boots", '"boots": boots(before, readings), "settle_uptime_ms"', '"settle_uptime_ms"'),
    ("O12: the network before not recorded", '"wifi_before": res.get("wifi_before"), "route_before"',
     '"wifi_before": None, "route_before"'),
    # O2, O11
    ("O11: no policy is none", "        if policy not in POLICIES:", "        if policy not in POLICIES + (None,):"),
    ("O11: the built-in tries dual-slot without identify", 'if policy == "dual-slot" and identify is None:', "if False:"),
    ("a crash in an upload raises", "    except Exception as e:      # noqa: BLE001 -- bench_flash crashing was a non-zero exit\n",
     "    except ZeroDivisionError as e:\n"),
    ("O2: ssid_env not read", 'ssid = s.get("ssid") or (os.environ.get(s["ssid_env"]) if s.get("ssid_env") else None)',
     'ssid = s.get("ssid")'),
    # the by-hand command
    ("by hand: no hold needed", '    if not held:\n        builtins.print("refused: the bench is not held',
     '    if False:\n        builtins.print("refused: the bench is not held'),
    ("by hand: any pid holds", 'if h["who"] != who or h["pid"] != pid:', 'if h["who"] != who:'),
    ("by hand: a log folder in a worktree", "    if wt:\n", "    if False:\n"),
]] + [
    ("O7: the runner gives no hold", "run/engine.py",
     "res = flasher(im, params, self.settings, logdir, hold=self.lock_ours)",
     "res = flasher(im, params, self.settings, logdir)"),
    ("O7: the runner's hold is a constant", "run/engine.py",
     "hold=self.lock_ours)", 'hold=lambda: (True, "assumed"))'),
    ("O1: ota is not built in", "run/flash.py",
     'BUILTIN = {"esptool": esptool_flash, "ota": ota_flash}', 'BUILTIN = {"esptool": esptool_flash}'),
    ("O1: the built-in takes no hold", "run/flash.py", "ota_flash.takes_hold = True", "ota_flash.takes_hold = False"),
    ("O1: the built-in drops the hold", "run/flash.py",
     "return flasher(image, params, settings, logdir, hold=hold)", "return flasher(image, params, settings, logdir)"),
]


if __name__ == "__main__":
    if "--can-fail" in sys.argv[1:]:
        sys.exit(_ota_mutants.run_mutants(os.path.abspath(__file__), MUTANTS, "ota_preflight"))
    sys.exit(main())
