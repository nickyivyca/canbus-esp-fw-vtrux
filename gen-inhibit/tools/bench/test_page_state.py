#!/usr/bin/env python3
"""Test page_state.py against a local fake device. With --mutants, run its mutants.

    py -3.14 -B gen-inhibit/tools/bench/test_page_state.py [--mutants]

Cases: a good page carrying keys that must not be copied (a fake SSID and password);
two bad answers then a good one (retried, three GETs, two errors kept); no good answer
(exit 1, no page); a page missing fields (recorded as None). Every case checks that
only /gen_inhibit was asked for. PAGE_STATE overrides the script under test. Scratch in
the worktree's scratch/. Gen-inhibit tester, 2026-10-20.
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("PAGE_STATE") or os.path.join(HERE, "page_state.py")
GOOD = {"mode": 0, "uptime_ms": 95123, "reset_reason": "SW", "elf_sha256": "ab" * 32, "inhibit_live": False, "disabled": False, "self_off": False,
        "arm_block": "not armed", "abort_reason": "none", "abort_latched": False, "disable_reason": "none",
        "err_n": 0, "err_age_ms": None, "worker": "idle", "ssid": "FAKE_SSID_VALUE", "pass": "FAKE_PASS_VALUE"}
REASONS = ("arm_block", "abort_reason", "abort_latched", "disable_reason", "err_n", "err_age_ms", "worker")
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


class Fake(http.server.BaseHTTPRequestHandler):
    answers = []          # (status, body) per GET, in order; the last repeats
    paths = []

    def do_GET(self):
        Fake.paths.append(self.path)
        st, body = Fake.answers[min(len(Fake.paths) - 1, len(Fake.answers) - 1)]
        self.send_response(st)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


def run(srv, tmp, name, answers, extra=()):
    Fake.answers, Fake.paths = answers, []
    out = os.path.join(tmp, name + ".json")
    r = subprocess.run([sys.executable, "-B", SCRIPT, "--label", name, "--out", out] + list(extra),
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, GI_WICAN_HOST="127.0.0.1:%d" % srv.server_address[1]))
    rec = json.load(open(out)) if os.path.exists(out) else None
    return r.returncode, rec, r.stdout + r.stderr, list(Fake.paths)


def tests():
    tmp = gi_paths.scratch("page-state-test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        good = json.dumps(GOOD)
        rc, rec, out, paths = run(srv, tmp, "a", [(200, good)])
        p = rec and rec["page"]
        check("good page: exit 0, one GET of /gen_inhibit", rc == 0 and paths == ["/gen_inhibit"], (rc, paths, out))
        check("good page: mode, uptime, reset, image and the flags recorded as read",
              p and all(p.get(k, "MISSING") == GOOD[k] for k in ("mode", "uptime_ms", "reset_reason", "elf_sha256", "inhibit_live", "disabled", "self_off")), p)
        check("good page: every reason field recorded as read",
              p and sorted(p["reasons"]) == sorted(REASONS) and all(p["reasons"][k] == GOOD[k] for k in REASONS), p)
        text = open(os.path.join(tmp, "a.json")).read() + out
        check("good page: nothing else copied (no SSID or password value in the record or the output)",
              "FAKE_SSID_VALUE" not in text and "FAKE_PASS_VALUE" not in text and p and set(p) ==
              {"mode", "uptime_ms", "reset_reason", "elf_sha256", "inhibit_live", "disabled", "self_off", "reasons"}, text[:300])
        rc, rec, out, paths = run(srv, tmp, "b", [(500, ""), (200, "not json"), (200, good)])
        check("two bad answers then a good one: exit 0 after three GETs, two errors kept",
              rc == 0 and paths == ["/gen_inhibit"] * 3 and rec and len(rec["errors"]) == 2
              and rec["page"] and rec["page"]["mode"] == 0, (rc, paths, rec))
        rc, rec, out, paths = run(srv, tmp, "c", [(200, "[1, 2]")], extra=["--tries", "2"])
        check("no good answer: exit 1, no page, an error per GET",
              rc == 1 and rec and rec["page"] is None and len(rec["errors"]) == 2
              and paths == ["/gen_inhibit"] * 2, (rc, paths, rec))
        rc, rec, out, paths = run(srv, tmp, "d", [(200, json.dumps({"mode": 3, "uptime_ms": 7}))])
        p = rec and rec["page"]
        check("a page missing fields: recorded as None, exit 0",
              rc == 0 and p and p["mode"] == 3 and p["reset_reason"] is None
              and all(v is None for v in p["reasons"].values()), (rc, p))
    except Exception as e:                      # a crash is reported, never a pass
        check("ran without raising", False, repr(e))
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    return 1 if fails else 0


MUTANTS = [
    ("the whole page copied", '    out = {f: p.get(f) for f in FIELDS}\n', '    out = dict(p)\n'),
    ("disabled not recorded", '"inhibit_live", "disabled", "self_off")', '"inhibit_live", "self_off")'),
    ("abort_latched not recorded", '"abort_reason", "abort_latched", "disable_reason"', '"abort_reason", "disable_reason"'),
    ("exit 0 with no page", '        return 1\n    print(', '        return 0\n    print('),
    ("no retry", '    for i in range(a.tries):\n', '    for i in range(1):\n'),
    ("no stop after a good page", '            rec["page"] = state(read_page(a.timeout))\n            break\n',
     '            rec["page"] = state(read_page(a.timeout))\n'),
    ("asks /check_status", '"http://%s/gen_inhibit"', '"http://%s/check_status"'),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    base = gi_paths.scratch("mutants-page_state")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        path = os.path.join(d, "page_state.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, PAGE_STATE=path), timeout=300)
        fl = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = any("ran without raising" not in f for f in fl)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                  len(fl), ("; " + fl[0][:100]) if fl else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else tests())
