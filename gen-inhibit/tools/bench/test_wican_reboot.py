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
start an unknown hash (FAIL, hash named) and a suite image other than the first (PASS,
marked); at a release the wanted image, another one, and a run that does not know (only
the first PASSes); no page at all (FAIL, nothing sent). Every case checks that only
/gen_inhibit, /system_reboot and /gen_inhibit_set?mode=0 were asked for and that no other
page field (a fake SSID and password) reaches the record or the output. The boot-time
rule is also tested directly. WICAN_REBOOT overrides the script under test. Scratch in
the worktree's scratch/. Gen-inhibit tester, 2026-10-20.
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
                 disarm="ok", after_latched=False, after_disabled=False, down=False):
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
        d.paths.append("GET " + self.path)
        p = None if d.down or self.path != "/gen_inhibit" else d.page()
        self._send(200, json.dumps(p)) if p is not None else self._send(503)

    def do_POST(self):
        d = Fake.dev
        d.paths.append("POST " + self.path)
        if self.path == "/system_reboot":
            if d.reboot == "403":
                return self._send(403)
            if d.reboot == "ok":
                d.pending = time.time() + 0.3
            return self._send(200, "rebooting")
        if self.path == "/gen_inhibit_set?mode=0":
            if d.disarm == "ok":
                d.mode = 0
            return self._send(200, "ok")
        self._send(404)

    def log_message(self, *a):
        pass


def run(srv, tmp, name, dev, args):
    Fake.dev = dev
    out = os.path.join(tmp, name + ".json")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-B", SCRIPT, "--label", name, "--out", out] + KNOWN + FAST + args,
                       capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GI_WICAN_HOST="127.0.0.1:%d" % srv.server_address[1]))
    rec = json.load(open(out)) if os.path.exists(out) else {}
    text = (open(out).read() if os.path.exists(out) else "") + r.stdout + r.stderr
    return r.returncode, rec, text, list(dev.paths), time.time() - t0


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
    shutil.rmtree(tmp, ignore_errors=True)
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

        rc, rec, text, paths, _ = run(srv, tmp, "unknown", Dev(sha=ODD), ["--first", "wican_man"])
        check("start on an unknown image: FAIL, the hash named",
              rc == 1 and failed(rec, "a known image") and ODD in rec.get("detail", ""), (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "other", Dev(sha=RT), ["--first", "wican_man"])
        check("start on another suite image: PASS, marked as not the first",
              rc == 0 and rec.get("found_image") == "wican_rt" and rec.get("differs_from_first") is True
              and "not the run's first image wican_man" in rec.get("detail", ""), (rc, rec.get("detail")))

        rc, rec, text, paths, _ = run(srv, tmp, "want", Dev(), ["--want", "wican_man"])
        check("release on the wanted image: PASS", rc == 0 and rec.get("verdict") == "PASS", rec.get("detail"))
        rc, rec, text, paths, _ = run(srv, tmp, "wantother", Dev(sha=RT), ["--want", "wican_man"])
        check("release on another image: FAIL", rc == 1 and failed(rec, "the image the run expects"),
              (rc, rec.get("detail")))
        rc, rec, text, paths, _ = run(srv, tmp, "wantunknown", Dev(), ["--want-unknown"])
        check("release when the run does not know the image: FAIL",
              rc == 1 and failed(rec, "the image the run expects"), (rc, rec.get("detail")))

        rc, rec, text, paths, _ = run(srv, tmp, "down", Dev(down=True), ["--first", "wican_man"])
        check("no page at all: FAIL and nothing sent",
              rc == 1 and not any(p.startswith("POST") for p in paths) and failed(rec, "page before the reboot"),
              (rc, paths, rec.get("detail")))
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
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
    ("start takes any image", 'chk("a known image", bool(names),', 'chk("a known image", True,'),
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
        shutil.rmtree(d, ignore_errors=True)
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
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else tests())
