#!/usr/bin/env python3
"""Test wican_reboot.py against a local fake WiCAN. With --mutants, run its mutants.

    py -3.14 -B gen-inhibit/tools/bench/test_wican_reboot.py [--mutants]

The fake keeps a boot time: GET /gen_inhibit answers uptime from it, POST /system_reboot
(when it behaves) answers 200, keeps serving the old boot for a moment, refuses for a
moment, then serves a new boot. Cases, each from the reviewer's 2026-10-20 conditions:
a clean start; the board a killed run left armed and latched (reboot clears it, the record
says what was found); an auto-arm image (comes up armed, disarmed, PASS) and one whose
disarm does nothing (FAIL); a reboot POST that does nothing, or answers 403 (FAIL: the
POST is never trusted); a PANIC reset, a latch or a disable that survives (FAIL); at the
start an unknown hash (PASS, recorded as unknown with its hash and as not the first:
reviewer, 2026-11-18), a page that names no elf_sha256 (FAIL) and a suite image other
than the first (PASS, marked); at a release the wanted image, another one, and a run that does not know (only
the first PASSes); no page at all (FAIL, nothing sent). Every case checks that only
/gen_inhibit, /system_reboot and /gen_inhibit_set?mode=0 were asked for and that no other
page field (a fake SSID and password) reaches the record or the output. The boot-time
rule is also tested directly. WICAN_REBOOT overrides the script under test. Scratch in
the worktree's scratch/. Gen-inhibit tester, 2026-10-20.

The hotspot's rejoin (2026-11-18): a fake WiCAN that stays unreachable after its reboot
until the hotspot is selected again, and the script run from a folder of its own beside a
stand-in gi_wifi (FAKE_GI_WIFI, whose select asks the fake for /__select). With
GI_OTA_SETTINGS it selects and PASSes, the record saying how and when; without it nothing
is selected and the new boot FAILs; a refused select and one that raises are kept as
failed, at most one per SELECT_EVERY_S; a set but unusable block FAILs before anything is
asked for or sent.
"""
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("WICAN_REBOOT") or os.path.join(HERE, "wican_reboot.py")
MAN, RT, ODD = "aa" * 32, "bb" * 32, "cd" * 32
KNOWN = ["--known", "wican_man=" + MAN, "--known", "wican_rt=" + RT]
FAST = ["--settle", "0.2", "--poll", "0.2", "--wait", "3", "--gap", "0.2", "--timeout", "2"]
fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


class Dev:
    """The fake WiCAN's state; one per case."""
    def __init__(self, sha=MAN, mode=0, latched=False, reboot="ok", reset="SW", boot_mode=0,
                 disarm="ok", after_latched=False, after_disabled=False, down=False, rejoin=False):
        # rejoin: after a reboot the page is unreachable until the hotspot is selected again
        # (the fake gi_wifi's select asks for /__select), as the phone is never left to
        # rejoin by itself (2026-11-18)
        self.rejoin, self.selected, self.selects = rejoin, True, []
        self.boot_t = time.time() - 500.0
        self.sha, self.mode, self.latched, self.reset = sha, mode, latched, "SW"
        self.disabled = False
        self.reboot, self.next_reset, self.boot_mode = reboot, reset, boot_mode
        self.disarm, self.after_latched, self.after_disabled = disarm, after_latched, after_disabled
        self.down = down
        self.pending = None
        self.paths = []

    def page(self):
        now = time.time()
        if self.pending is not None and now >= self.pending:
            if now < self.pending + 0.3:
                return None                     # rebooting: no answer
            self.boot_t, self.pending = self.pending + 0.3, None
            self.reset, self.mode = self.next_reset, self.boot_mode
            self.latched, self.disabled = self.after_latched, self.after_disabled
        return {"mode": self.mode, "uptime_ms": int((now - self.boot_t) * 1000),
                "reset_reason": self.reset, "elf_sha256": self.sha, "inhibit_live": False,
                "disabled": self.disabled, "self_off": "",
                "arm_block": "x", "abort_reason": "bus lost" if self.latched else "",
                "abort_latched": self.latched, "disable_reason": "low_soc" if self.disabled else "",
                "err_n": 0, "err_age_ms": None, "worker": "idle",
                "ssid": "FAKE_SSID_VALUE", "pass": "FAKE_PASS_VALUE"}


class Fake(http.server.BaseHTTPRequestHandler):
    dev = None

    def _send(self, st, body=""):
        self.send_response(st)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        d = Fake.dev
        if self.path == "/__select":            # the fake gi_wifi's select, not the device's
            d.selected = True
            d.selects.append(time.time())
            return self._send(200, "selected")
        d.paths.append("GET " + self.path)
        p = None if d.down or self.path != "/gen_inhibit" else d.page()
        if d.rejoin and not d.selected and d.pending is None:
            p = None                            # rebooted, and the hotspot not selected since
        self._send(200, json.dumps(p)) if p is not None else self._send(503)

    def do_POST(self):
        d = Fake.dev
        d.paths.append("POST " + self.path)
        if self.path == "/system_reboot":
            if d.reboot == "403":
                return self._send(403)
            if d.reboot == "ok":
                d.pending = time.time() + 0.3
                d.selected = False              # the hotspot goes down with the reboot
            return self._send(200, "rebooting")
        if self.path == "/gen_inhibit_set?mode=0":
            if d.disarm == "ok":
                d.mode = 0
            return self._send(200, "ok")
        self._send(404)

    def log_message(self, *a):
        pass


FAKE_GI_WIFI = '''"""The rejoin cases' stand-in for gi_wifi (test_wican_reboot.py): from_env, backend and
select as gi_wifi's; select asks the fake WiCAN for /__select. Settings {"fail": true} make
select refuse, {"crash": true} raise."""
import json
import os
import urllib.request

ENV = "GI_OTA_SETTINGS"


class Refused(Exception):
    pass


def from_env(environ=None):
    environ = os.environ if environ is None else environ
    try:
        cfg = json.loads(environ.get(ENV) or "")
    except ValueError:
        raise Refused("%s is not JSON" % ENV)
    if not isinstance(cfg, dict) or not cfg.get("profile"):
        raise Refused("%s names no profile" % ENV)
    return cfg


def backend(cfg):
    return "fake"


def select(cfg, be=None):
    if cfg.get("crash"):
        raise RuntimeError("fake select crashed")
    if cfg.get("fail"):
        return False, "fake select refused"
    urllib.request.urlopen("http://%s/__select" % os.environ["GI_WICAN_HOST"], timeout=2).read()
    return True, "fake select of profile %s" % cfg["profile"]


def join(cfg, timeout=None, be=None):
    """As select, then joined: {"joinfail": true} makes it not join."""
    if cfg.get("joinfail"):
        return False, "fake join: not joined"
    return select(cfg, be)
'''


def rejoin_tool(tmp):
    """The script under test in a folder of its own beside page_state.py and the fake gi_wifi."""
    d = os.path.join(tmp, "rejoin_tool")
    if not os.path.isdir(d):
        os.makedirs(d)
        shutil.copy(SCRIPT, os.path.join(d, "wican_reboot.py"))
        shutil.copy(os.path.join(os.path.dirname(SCRIPT), "page_state.py"), d)
        with open(os.path.join(d, "gi_wifi.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_GI_WIFI)
    return os.path.join(d, "wican_reboot.py")


def run(srv, tmp, name, dev, args, env=None, script=None):
    Fake.dev = dev
    out = os.path.join(tmp, name + ".json")
    t0 = time.time()
    e = {k: v for k, v in os.environ.items() if k != "GI_OTA_SETTINGS"}
    e.update(env or {}, GI_WICAN_HOST="127.0.0.1:%d" % srv.server_address[1])
    r = subprocess.run([sys.executable, "-B", script or SCRIPT, "--label", name, "--out", out] + KNOWN + FAST + args,
                       capture_output=True, text=True, timeout=120, env=e)
    rec = json.load(open(out)) if os.path.exists(out) else {}
    text = (open(out).read() if os.path.exists(out) else "") + r.stdout + r.stderr
    return r.returncode, rec, text, list(dev.paths), time.time() - t0


IN_CHECK = '''import json
import sys
sys.path.insert(0, sys.argv[1])
import wican_reboot as W
print("RESULT " + json.dumps(W.reboot_in_check(wait=3.0, poll=0.2, settle=0.2, timeout=2.0)))
'''


OTA_ROUTE = '''import json
import sys
sys.path.insert(0, sys.argv[1])
import wican_reboot as W
print("RESULT " + json.dumps(list(W.ota_route(tries=2, gap=0.2, timeout=2.0))))
'''


def in_check(srv, tool, dev, env=None, child=IN_CHECK):
    """reboot_in_check (or `child`) run in a child from the rejoin tool's folder (the script under test
    beside the fake gi_wifi): (its result, the paths the fake was asked for)."""
    Fake.dev = dev
    e = {k: v for k, v in os.environ.items() if k != "GI_OTA_SETTINGS"}
    e.update(env or {}, GI_WICAN_HOST="127.0.0.1:%d" % srv.server_address[1])
    r = subprocess.run([sys.executable, "-B", "-c", child, os.path.dirname(tool)], capture_output=True,
                       text=True, timeout=120, env=e)
    line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
    return (json.loads(line[len("RESULT "):]) if line else {"raised": r.stderr[-300:]}), list(dev.paths)


ALLOWED = {"GET /gen_inhibit", "POST /system_reboot", "POST /gen_inhibit_set?mode=0"}


def common(name, rec, text, paths):
    check("%s: only the page, the reboot and the disarm asked for" % name, set(paths) <= ALLOWED, paths)
    check("%s: no other page field in the record or the output" % name,
          "FAKE_SSID_VALUE" not in text and "FAKE_PASS_VALUE" not in text, text[:200])


def failed(rec, check_name):
    return any(c["check"] == check_name and not c["ok"] for c in rec.get("checks", []))


def direct():
    """new_boot on its own: the boot-time rule."""
    spec = importlib.util.spec_from_file_location("wican_reboot_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    same = m.new_boot(({"uptime_ms": 1000}, 0.0, 2.0), ({"uptime_ms": 11000}, 10.0, 12.0))
    check("boot rule: the same boot read slowly twice is not a new boot", same[0] is False, same)
    lower = m.new_boot(({"uptime_ms": 90000}, 0.0, 0.1), ({"uptime_ms": 5000}, 20.0, 20.1))
    check("boot rule: uptime lower is a new boot", lower[0] is True, lower)
    # a slow read before, then a quick reboot: the boot-time bounds overlap, and only
    # the lower uptime shows the new boot
    quick = m.new_boot(({"uptime_ms": 1000}, 0.0, 3.0), ({"uptime_ms": 500}, 3.0, 3.1))
    check("boot rule: uptime lower is a new boot even where the boot times cannot tell",
          quick[0] is True and "uptime_ms 500 < 1000" in quick[1], quick)
    moved = m.new_boot(({"uptime_ms": 1000}, 0.0, 0.1), ({"uptime_ms": 50000}, 100.0, 100.1))
    check("boot rule: uptime higher but the boot time moved is a new boot", moved[0] is True, moved)


def tests():
    tmp = gi_paths.scratch("wican-reboot-test")
    gi_paths.remove_tree(tmp)
    os.makedirs(tmp)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        direct()
        rc, rec, text, paths, _ = run(srv, tmp, "clean", Dev(), ["--first", "wican_man"])
        check("clean start: PASS, exit 0", rc == 0 and rec.get("verdict") == "PASS", text[-300:])
        check("clean start: one reboot POST, no disarm, found the first image",
              paths.count("POST /system_reboot") == 1 and "POST /gen_inhibit_set?mode=0" not in paths
              and rec.get("found_image") == "wican_man" and rec.get("differs_from_first") is False, (paths, rec))
        check("clean start: the page read before the POST", paths and paths[0] == "GET /gen_inhibit", paths)
        common("clean", rec, text, paths)

        rc, rec, text, paths, _ = run(srv, tmp, "killed", Dev(mode=3, latched=True), ["--first", "wican_man"])
        b = rec.get("before") or {}
        check("left armed and latched by a killed run: the reboot clears it, PASS",
              rc == 0 and rec.get("verdict") == "PASS" and (rec.get("final") or {}).get("mode") == 0, text[-300:])
        check("left armed and latched: the record says what was found",
              b.get("mode") == 3 and b.get("reasons", {}).get("abort_latched") is True
              and "before: mode 3, latched True" in rec.get("detail", ""), (b, rec.get("detail")))
        check("left armed: disarmed BEFORE the reboot, the POST and the page after it recorded",
              "POST /gen_inhibit_set?mode=0" in paths and "POST /system_reboot" in paths
              and paths.index("POST /gen_inhibit_set?mode=0") < paths.index("POST /system_reboot")
              and (rec.get("pre_disarm_post") or {}).get("status") == 200
              and (rec.get("pre_disarm_page") or {}).get("mode") == 0
              and "disarmed before the reboot: mode 0" in rec.get("detail", ""), (paths, rec.get("detail")))
        common("killed", rec, text, paths)

        dev = Dev(mode=3, latched=True, reboot="ignore")
        rc, rec, text, paths, _ = run(srv, tmp, "armednoreboot", dev, ["--first", "wican_man"])
        check("armed, and the reboot never comes: FAIL, yet the device is left in mode 0",
              rc == 1 and failed(rec, "a new boot") and dev.mode == 0
              and paths.index("POST /gen_inhibit_set?mode=0") < paths.index("POST /system_reboot"),
              (rc, dev.mode, paths, rec.get("detail")))
        common("armednoreboot", rec, text, paths)

        rc, rec, text, paths, _ = run(srv, tmp, "autoarm", Dev(boot_mode=3), ["--want", "wican_man"])
        check("auto-arm image: comes up armed, disarmed, PASS",
              rc == 0 and rec.get("came_up_armed") is True and rec.get("disarmed") is True
              and paths.count("POST /gen_inhibit_set?mode=0") == 1, (rc, rec.get("detail"), paths))
        check("auto-arm image: the disarm comes after the new boot",
              paths.index("POST /gen_inhibit_set?mode=0") > paths.index("POST /system_reboot"), paths)
        common("autoarm", rec, text, paths)

        rc, rec, text, paths, _ = run(srv, tmp, "stuckarmed", Dev(boot_mode=3, disarm="ignore"), ["--want", "wican_man"])
        check("auto-arm image that will not disarm: FAIL on mode 0",
              rc == 1 and failed(rec, "mode 0") and rec.get("disarmed") is False, (rc, rec.get("detail")))

        for name, dev in (("ignored", Dev(reboot="ignore")), ("refused", Dev(reboot="403"))):
            rc, rec, text, paths, took = run(srv, tmp, name, dev, ["--first", "wican_man"])
            check("reboot POST %s: FAIL, no new boot, within the wait" % name,
                  rc == 1 and failed(rec, "a new boot") and took < 20, (rc, rec.get("detail"), took))
            common(name, rec, text, paths)

        rc, rec, text, paths, _ = run(srv, tmp, "panic", Dev(reset="PANIC"), ["--first", "wican_man"])
        check("a PANIC boot is not a confirmed reboot: FAIL", rc == 1 and failed(rec, "reset_reason SW"),
              (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "latch", Dev(after_latched=True), ["--first", "wican_man"])
        check("a latch that survives the reboot: FAIL", rc == 1 and failed(rec, "abort not latched"),
              (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "disabled", Dev(after_disabled=True), ["--first", "wican_man"])
        check("a disable that survives the reboot: FAIL", rc == 1 and failed(rec, "not disabled"),
              (rc, rec.get("detail")))

        # an unknown image at the start (reviewer, 2026-11-18): PASS when every other check
        # holds, recorded as unknown with its hash, and as not the first image
        rc, rec, text, paths, _ = run(srv, tmp, "unknown", Dev(sha=ODD), ["--first", "wican_man"])
        check("start on an unknown image: PASS, recorded as unknown with its hash",
              rc == 0 and rec.get("verdict") == "PASS" and rec.get("found_image") is None
              and rec.get("found_sha") == ODD and rec.get("found_unknown") is True
              and ("found unknown image " + ODD) in rec.get("detail", ""), (rc, rec.get("detail")))
        check("start on an unknown image: marked as not the run's first image",
              rec.get("differs_from_first") is True and "not the run's first image wican_man" in rec.get("detail", ""),
              rec.get("detail"))
        common("unknown", rec, text, paths)
        rc, rec, text, paths, _ = run(srv, tmp, "unknownlatch", Dev(sha=ODD, after_latched=True), ["--first", "wican_man"])
        check("start on an unknown image with a latch that survives: FAIL",
              rc == 1 and failed(rec, "abort not latched") and rec.get("found_unknown") is True, (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "noimage", Dev(sha=None), ["--first", "wican_man"])
        check("start on a page that names no image: FAIL",
              rc == 1 and failed(rec, "an image named") and rec.get("found_unknown") is False, (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "other", Dev(sha=RT), ["--first", "wican_man"])
        check("start on another suite image: PASS, marked as not the first",
              rc == 0 and rec.get("found_image") == "wican_rt" and rec.get("differs_from_first") is True
              and rec.get("found_unknown") is False
              and "not the run's first image wican_man" in rec.get("detail", ""), (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "first", Dev(), ["--first", "wican_man"])
        check("start on the first image: PASS, not marked",
              rc == 0 and rec.get("differs_from_first") is False and rec.get("found_unknown") is False
              and "not the run's first image" not in rec.get("detail", ""), (rc, rec.get("detail")))

        rc, rec, text, paths, _ = run(srv, tmp, "want", Dev(), ["--want", "wican_man"])
        check("release on the wanted image: PASS", rc == 0 and rec.get("verdict") == "PASS", rec.get("detail"))
        rc, rec, text, paths, _ = run(srv, tmp, "wantother", Dev(sha=RT), ["--want", "wican_man"])
        check("release on another image: FAIL", rc == 1 and failed(rec, "the image the run expects"),
              (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "wantodd", Dev(sha=ODD), ["--want", "wican_man"])
        check("release on an unknown image: FAIL (--want stays exact)",
              rc == 1 and failed(rec, "the image the run expects") and rec.get("found_unknown") is True,
              (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "wantunknown", Dev(), ["--want-unknown"])
        check("release when the run does not know the image: FAIL",
              rc == 1 and failed(rec, "the image the run expects"), (rc, rec.get("detail")))

        # the hotspot's rejoin (2026-11-18): the phone never rejoins by itself after a reboot
        tool = rejoin_tool(tmp)
        ota = {"GI_OTA_SETTINGS": json.dumps({"profile": "79", "wifi": "adb"})}
        dev = Dev(rejoin=True)
        rc, rec, text, paths, _ = run(srv, tmp, "rejoin", dev, ["--first", "wican_man"], ota, tool)
        rj = rec.get("rejoins") or []
        check("rejoin: with GI_OTA_SETTINGS the hotspot is selected after the reboot, PASS",
              rc == 0 and rec.get("verdict") == "PASS" and len(dev.selects) >= 1
              and rj and rj[0].get("ok") is True and "profile 79" in rj[0].get("why", ""), (rc, rec.get("detail"), rj))
        check("rejoin: the record says how (gi_wifi.select, the settings' backend) and when",
              "gi_wifi.select" in (rec.get("rejoin") or "") and "adb" in (rec.get("rejoin") or "")
              and isinstance(rj[0].get("after_s") if rj else None, float), (rec.get("rejoin"), rj))
        common("rejoin", rec, text, paths)
        dev = Dev(rejoin=True)
        rc, rec, text, paths, _ = run(srv, tmp, "norejoin", dev, ["--first", "wican_man"], None, tool)
        check("no GI_OTA_SETTINGS: no select, so a board that needs one FAILs on the new boot; rejoin None",
              rc == 1 and failed(rec, "a new boot") and not dev.selects and rec.get("rejoin") is None
              and rec.get("rejoins") == [], (rc, rec.get("detail"), rec.get("rejoin"), dev.selects))
        dev = Dev(rejoin=True)
        rc, rec, text, paths, _ = run(srv, tmp, "selectfails", dev, ["--first", "wican_man"],
                                      {"GI_OTA_SETTINGS": json.dumps({"profile": "79", "fail": True})}, tool)
        rj = rec.get("rejoins") or []
        check("a refused select: FAIL on the new boot, the select kept as failed with its reason",
              rc == 1 and failed(rec, "a new boot") and rj and rj[0].get("ok") is False
              and "fake select refused" in rj[0].get("why", ""), (rc, rec.get("detail"), rj))
        check("selects at most once every SELECT_EVERY_S (5 s): one in a 3 s wait", len(rj) == 1, rj)
        dev = Dev(rejoin=True)
        rc, rec, text, paths, _ = run(srv, tmp, "selectcrash", dev, ["--first", "wican_man"],
                                      {"GI_OTA_SETTINGS": json.dumps({"profile": "79", "crash": True})}, tool)
        rj = rec.get("rejoins") or []
        check("a select that raises: kept as failed with the error, FAIL on the new boot, a record left",
              rc == 1 and failed(rec, "a new boot") and rj and rj[0].get("ok") is False
              and "fake select crashed" in rj[0].get("why", ""), (rc, rec.get("detail"), rj))
        dev = Dev(rejoin=True)
        rc, rec, text, paths, _ = run(srv, tmp, "badsettings", dev, ["--first", "wican_man"],
                                      {"GI_OTA_SETTINGS": "not json"}, tool)
        check("GI_OTA_SETTINGS set but unusable: FAIL before anything is asked for or sent",
              rc == 1 and failed(rec, "hotspot rejoin settings") and paths == [] and not dev.selects,
              (rc, rec.get("detail"), paths))

        rc, rec, text, paths, _ = run(srv, tmp, "down", Dev(down=True), ["--first", "wican_man"])
        check("no page at all: FAIL and nothing sent",
              rc == 1 and not any(p.startswith("POST") for p in paths) and failed(rec, "page before the reboot"),
              (rc, paths, rec.get("detail")))

        # reboot_in_check, for a check that reboots the WiCAN inside its run (interlock_bench's before each
        # case, B22's before each arm; gen-inhibit reviewer, 2026-11-19): the page first, the same new-boot
        # rule and the same rejoin as the board step
        ota = {"GI_OTA_SETTINGS": json.dumps({"profile": "79", "wifi": "adb"})}
        dev = Dev(rejoin=True)
        r, paths = in_check(srv, tool, dev, ota)
        check("in check: rejoined through the settings' select, a new boot by uptime, ok with its reason, SW",
              r.get("ok") is True and dev.selects and (r.get("rejoins") or [{}])[0].get("ok") is True
              and "uptime_ms" in (r.get("why") or "") and r.get("reset_reason") == "SW" and r.get("mode") == 0
              and "gi_wifi.select" in (r.get("rejoin") or ""), r)
        check("in check: the page read before the one reboot POST, nothing else asked for",
              paths[:1] == ["GET /gen_inhibit"] and paths.count("POST /system_reboot") == 1
              and set(paths) <= {"GET /gen_inhibit", "POST /system_reboot"}, paths)
        dev = Dev(down=True)
        r, paths = in_check(srv, tool, dev, ota)
        check("in check: no page before: refused, nothing sent, not ok",
              r.get("ok") is False and (r.get("why") or "").startswith("no page before the reboot")
              and not any(p.startswith("POST") for p in paths), (r, paths))
        dev = Dev(reboot="nothing")
        r, paths = in_check(srv, tool, dev, ota)
        check("in check: a reboot POST that does nothing is not ok (the POST is never trusted)",
              r.get("ok") is False and "boot time unchanged" in (r.get("why") or ""), r)
        dev = Dev(rejoin=True)
        r, paths = in_check(srv, tool, dev, None)
        check("in check: no GI_OTA_SETTINGS: no select, so a board that needs one is not ok",
              r.get("ok") is False and not dev.selects and r.get("rejoin") is None, r)
        r, paths = in_check(srv, tool, Dev(), {"GI_OTA_SETTINGS": "not json"})
        check("in check: GI_OTA_SETTINGS set but unusable: not ok, nothing asked for or sent",
              r.get("ok") is False and "GI_OTA_SETTINGS" in (r.get("why") or "") and paths == [], (r, paths))

        # ota_route, B21's gate (topic (E), 2026-11-19): the ota block's backend joins first, always, and
        # the page must answer after it; unset or refused, not joined, or no page: not reached
        dev = Dev()
        r, paths = in_check(srv, tool, dev, ota, OTA_ROUTE)
        check("ota route: joined through the block's backend, then the page -> True, saying so",
              r[:1] == [True] and "joined through the ota block's adb backend" in r[1] and len(dev.selects) == 1
              and paths == ["GET /gen_inhibit"], (r, dev.selects, paths))
        dev = Dev(rejoin=True)
        dev.selected = False                    # the phone off the hotspot: no page until the join
        r, paths = in_check(srv, tool, dev, ota, OTA_ROUTE)
        check("ota route: off the hotspot, the join puts it back and the page answers -> True",
              r[:1] == [True] and len(dev.selects) == 1, (r, dev.selects))
        dev = Dev()
        r, paths = in_check(srv, tool, dev, None, OTA_ROUTE)
        check("ota route: no GI_OTA_SETTINGS -> False, saying why, though the page would answer; nothing asked",
              r[:1] == [False] and "GI_OTA_SETTINGS" in r[1] and not dev.selects and paths == [], (r, paths))
        dev = Dev()
        r, paths = in_check(srv, tool, dev, {"GI_OTA_SETTINGS": "not json"}, OTA_ROUTE)
        check("ota route: GI_OTA_SETTINGS refused -> False, nothing asked for",
              r[:1] == [False] and "GI_OTA_SETTINGS" in r[1] and paths == [], (r, paths))
        dev = Dev()
        r, paths = in_check(srv, tool, dev, {"GI_OTA_SETTINGS": json.dumps({"profile": "79", "wifi": "adb",
                                                                          "joinfail": True})}, OTA_ROUTE)
        check("ota route: the join fails -> False, saying so, and the page is not read",
              r[:1] == [False] and "did not join" in r[1] and paths == [], (r, paths))
        dev = Dev(down=True)
        r, paths = in_check(srv, tool, dev, ota, OTA_ROUTE)
        check("ota route: joined but no page -> False, saying so",
              r[:1] == [False] and "the page does not answer" in r[1] and len(dev.selects) == 1, (r, dev.selects))
        check("ota route: only the page asked for", set(paths) <= {"GET /gen_inhibit"}, paths)
        # baseline_b21.sh's gate on that: the device not reached makes B21 NOT-RUN, its file saying why,
        # and nothing after the gate runs (GI_B21_STEPS none: no guard can reach a real device)
        out = os.path.join(tmp, "b21gate")
        os.makedirs(out)
        Fake.dev = Dev(down=True)
        e = {k: v for k, v in os.environ.items() if k not in ("GI_OTA_SETTINGS", "GI_FW_TOOLS")}
        e.update(GI_WICAN_HOST="127.0.0.1:%d" % srv.server_address[1], REVERSE_IT_DIR=tmp, GI_COMMIT="c0ffee12",
                 GI_OUT=out, GI_WICAN_SSID="WiCAN_testssid", GI_B21_STEPS="none", GI_RUNS_DIR=out,
                 PYTHONDONTWRITEBYTECODE="1")
        b21 = os.path.join(os.path.dirname(tool), "baseline_b21.sh")
        if not os.path.exists(b21):
            shutil.copy(os.path.join(os.path.dirname(SCRIPT), "baseline_b21.sh"), b21)
            shutil.copy(os.path.join(HERE, "bench_host.sh"), os.path.join(os.path.dirname(tool), "bench_host.sh"))
        r = subprocess.run(["bash", b21, "img.bin"], capture_output=True, text=True, timeout=120, env=e)
        nr = os.path.join(out, "B21_not_run.txt")
        got = open(nr).read() if os.path.exists(nr) else ""
        check("B21 gate: not reached through the ota block -> exit 0, B21_not_run.txt saying why, nothing else written",
              r.returncode == 0 and got.startswith("NOT-RUN: the device is not reached through the run's ota block: ")
              and "GI_OTA_SETTINGS" in got and sorted(os.listdir(out)) == ["B21_not_run.txt"],
              (r.returncode, got, sorted(os.listdir(out)), r.stdout[-300:], r.stderr[-300:]))
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        srv.shutdown()
        gi_paths.remove_tree(tmp)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("the reboot POST trusted", "            yes, why = new_boot(before, got)\n",
     "            yes, why = True, \"trusted\"\n"),
    ("loose boot-time bounds", "    latest_before = b1 - ub / 1000.0", "    latest_before = b0 - ub / 1000.0 - 1.0"),
    ("uptime lower not used", "    if ua < ub:\n", "    if False:\n"),
    ("reset reason not checked", 'last.get("reset_reason") == "SW",', 'True,'),
    ("no disarm", '    rec["came_up_armed"] = last.get("mode") != 0\n', '    rec["came_up_armed"] = False\n'),
    ("mode not checked", '("mode 0", p.get("mode") == 0,', '("mode 0", True,'),
    ("latch not checked", 'r.get("abort_latched") is False,', 'True,'),
    ("disable not checked", 'p.get("disabled") is False and r.get("disable_reason") in ("", None),', 'True,'),
    ("start refuses an unknown image", 'chk("an image named", sha is not None,', 'chk("an image named", bool(names),'),
    ("start takes a page with no image", 'chk("an image named", sha is not None,', 'chk("an image named", True,'),
    ("an unknown image not recorded as such", 'rec["found_unknown"] = sha is not None and not names',
     'rec["found_unknown"] = False'),
    ("release takes any image", "chk(\"the image the run expects\", sha == known[a.want],",
     "chk(\"the image the run expects\", True,"),
    ("release passes when the run does not know", 'chk("the image the run expects", False, "the run does not know',
     'chk("the image the run expects", True, "the run does not know'),
    ("a different first image not marked", '    rec["differs_from_first"] = rec["found_image"] != a.first\n',
     '    rec["differs_from_first"] = False\n'),
    ("the before state not reported", '            bits.append("before: mode %s, latched %s"',
     '            bits.append("before: %s %s"'),
    ("POST without a page first", "        chk(\"page before the reboot\", False, \"no page in 4 tries: nothing sent\")\n"
     "        return finish()\n",
     "        chk(\"page before the reboot\", False, \"no page in 4 tries: nothing sent\")\n"
     "        post(\"/system_reboot\", a.timeout)\n        return finish()\n"),
    ("no disarm before the reboot", '    if before[0].get("mode") != 0:\n        # disarm first',
     '    if False:\n        # disarm first'),
    ("the page after the first disarm not kept", 'rec["pre_disarm_page"] = got[0] if got else None',
     'rec["pre_disarm_page"] = None'),
    ("the first disarm not reported", '            bits.append("disarmed before the reboot: %s"',
     '            bits.append("before the reboot: %s"'),
    # the hotspot's rejoin (2026-11-18)
    ("no rejoin", "            rejoin.unreadable(t_post)\n", "            pass\n"),
    ("the rejoin never selects", "        if select is not None and (last_select is None", "        if False and (last_select is None"),
    ("a select every poll", "(last_select is None or time.time() - last_select >= SELECT_EVERY_S)", "True"),
    ("unusable settings ignored",
     '        chk("hotspot rejoin settings", False, "GI_OTA_SETTINGS: %s: nothing sent" % e)\n        return finish()\n',
     "        select = None\n"),
    ("a select that raises not caught", "            try:\n                ok, how = select()\n"
     "            except Exception as e:      # a select that crashes is a select that failed\n"
     "                ok, how = False, repr(e)[:160]\n", "            ok, how = select()\n"),
    ("the select's time not kept, so it selects every poll",
     "            self.last_select = last_select = time.time()\n", "            last_select = time.time()\n"),
    ("a failed select recorded as ok", '"ok": bool(ok), "why": how})', '"ok": True, "why": how})'),
    # reboot_in_check (2026-11-19)
    ("in check: POST without a page first", "    if before is None:\n        out.update(why=\"no page before the reboot",
     "    if before is None and False:\n        out.update(why=\"no page before the reboot"),
    ("in check: no rejoin", "        select, out[\"rejoin\"] = hotspot_select(environ)\n",
     "        select, out[\"rejoin\"] = None, None\n"),
    ("in check: the POST trusted", "    out.update(ok=after is not None, why=why", "    out.update(ok=True, why=why"),
    ("in check: unusable settings ignored",
     "        out.update(why=\"GI_OTA_SETTINGS: %s: nothing sent\" % e, s=round(time.time() - t, 1))\n        return out\n",
     "        select = None\n"),
    # ota_route (B21's gate, topic (E), 2026-11-19)
    ("ota route: no join, the page by whatever route answers", "        ok, why = GW.join(cfg)\n",
     "        ok, why = True, \"not joined\"\n"),
    ("ota route: a failed join ignored", '    if not ok:\n        return False, "the ota block\'s %s backend did not join',
     '    if False:\n        return False, "the ota block\'s %s backend did not join'),
    ("ota route: joined taken as reached, the page not read",
     "    if read_tries(tries, gap, timeout, errors) is None:\n        return False, \"joined through",
     "    if False:\n        return False, \"joined through"),
    ("ota route: no settings, the page read anyway", '        return False, "GI_OTA_SETTINGS: %s" % e\n',
     '        return (read_tries(1, 0, timeout, []) is not None), "GI_OTA_SETTINGS: %s" % e\n'),
    ("the rejoin's how not recorded", "        select, rec[\"rejoin\"] = hotspot_select()\n",
     "        select, _ = hotspot_select()\n"),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    base = gi_paths.scratch("mutants-wican_reboot")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        shutil.copy(os.path.join(HERE, "page_state.py"), d)
        path = os.path.join(d, "wican_reboot.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, WICAN_REBOOT=path), timeout=900)
        fl = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = any("ran without raising" not in f for f in fl)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fl), ("; " + fl[0][:100]) if fl else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else tests())
