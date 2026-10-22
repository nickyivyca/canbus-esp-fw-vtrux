#!/usr/bin/env python3
"""Tests mode_change_judge on synthetic runs: test_interlock_judge's Sim for the wire
(a VCM train, a device answering while in INHIBIT, diag pages) and synthetic full
pages read every 0.25 s. No device. --mutants runs its mutants; each must make a
check fail.

    py -3.14 tools/gen_inhibit/bench/test_mode_change_judge.py [--mutants]
Run from the reverse-it root.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mode_change_judge as K      # noqa: E402
import test_interlock_judge as T   # noqa: E402

FAILS = []
SCHED = [(1.0, 3), (3.5, 3), (6.0, 3), (8.5, 4), (11.0, 3), (13.5, 4), (16.0, 3)]
RATE = 50   # VCM 0x051 per second, so tx_ok / would_tx grow at this rate in an arm


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def posts(lat=0.02, http=None, body=None, which=None):
    out = []
    for k, (t, m) in enumerate(SCHED):
        p = {"mode": m, "t_sent": t, "t_ret": t + lat if (which is None or k == which) else t + 0.02,
             "http": 200, "body": None, "initial": k == 0}
        if which == k:
            p["http"] = http if http is not None else 200
            p["body"] = body
        out.append(p)
    return out


def mode_at(ps, t):
    m = 0
    for p in ps:
        if p["t_ret"] <= t:
            m = p["mode"]
    return m


def arm_start(ps, t):
    s = None
    for p in ps:
        if p["t_ret"] <= t:
            s = p["t_ret"]
    return s


def wire(ps, dead_before=None, pages_gap=None):
    s = T.Sim()
    s.vcm(0.5, 19.5)
    live = lambda t: mode_at(ps, t) == 3 and not (dead_before and dead_before - 0.6 < t < dead_before)
    s.device(live)
    gap = pages_gap or (0, 0)
    keep = lambda t: not (gap[0] <= t < gap[1])
    s.pages(0.3, 19.5, lambda t: {"live": mode_at(ps, t) == 3})
    s.f = [x for x in s.f if not (x[1] in (0x7F1, 0x7F2, 0x7F8) and not keep(x[0]))]
    for i, off in ((0x7F3, 0.33), (0x7F9, 0.34)):
        s.train(i, off, 19.5, 1.0, lambda t: "00" * 8)
        s.f = [x for x in s.f if not (x[1] == i and not keep(x[0]))]
    return s.f


def page(ps, t, idn=0, held=0, cls_resid=0, carried=0, cls_held=0, applied=True, no_reset=False):
    m = mode_at(ps, t) if applied else mode_at(ps, t - 0.6)
    st = 0.0 if no_reset else arm_start(ps, t)
    n = int((t - st) * RATE) if st is not None and m in (3, 4) else 0
    tx, would = (n, 0) if m == 3 else ((0, n) if m == 4 else (0, 0))
    q = tx + cls_held
    inh = {"queued": q - carried, "carried_in": carried, "sent": tx - cls_resid, "dropped": 0,
           "withdrawn": 1 + max(idn, 0), "depth": 0, "held": cls_held}
    inh["sent"] -= 1     # the one withdrawn frame left by sent
    # identity: (withdrawn + dropped) - (skipped + standdown) = idn
    return {"mode": m, "tx_ok": tx, "would_tx": would,
            "sched": {"skipped": 0, "standdown": 1 - min(idn, 0) if idn < 0 else 1,
                      "cls": {"inhibit": dict(inh, held=held if held else cls_held),
                              "probe": {"queued": 0, "carried_in": 0, "sent": 0, "dropped": 0,
                                        "withdrawn": 0, "depth": 0, "held": 0},
                              "telemetry": {"queued": 5, "carried_in": 0, "sent": 5, "dropped": 0,
                                            "withdrawn": 0, "depth": 0, "held": 0}}}}


def rows(ps, at=None, **kw):
    """A read every 0.25 s; kw applies to reads whose start is in `at` (lo, hi)."""
    out = []
    t = 0.5
    while t < 19.4:
        k = kw if at and at[0] <= t < at[1] else {}
        out.append({"t_req": round(t, 3), "t_resp": round(t + 0.02, 3), "page": page(ps, t, **k)})
        t += 0.25
    return out


def failed(r, part):
    return [c["name"] for c in r["checks"] if not c["ok"] and part in c["name"]]


def main():
    global SCHED
    ps = posts()
    p0 = page(ps, 2.0)
    check("fixture: the synthetic page balances (class 0, identity 0)",
          K.class_residuals(p0)["inhibit"] == 0 and K.identity(p0) == 0,
          (K.class_residuals(p0), K.identity(p0)))
    r = K.judge(wire(ps), ps, rows(ps))
    check("the right device: AS SPECIFIED", r["verdict"] == "AS SPECIFIED",
          [c for c in r["checks"] + r["preconditions"] if not c["ok"]])
    check("latency distribution recorded", r["facts"]["latency_ms"]["n"] == len(SCHED)
          and r["facts"]["latency_ms"]["max"] == 20.0, r["facts"]["latency_ms"])
    b = "HTTP/1.1 500 Internal Server Error\r\n\r\nmode change not taken by the gen_inhibit worker within 1 s; withdrawn, not applied"
    ps2 = posts(http=500, body=b, which=2)
    r = K.judge(wire(ps2), ps2, rows(ps2))
    check("a 500 refusal fails the reply", failed(r, "POST 2 (3->3): reply"), r["checks"][:4])
    ps3 = posts(lat=1.3, which=3)
    r = K.judge(wire(ps3), ps3, rows(ps3))
    check("a 200 after 1 s passes (taken; in spec since question (a)) and is listed", r["verdict"] == "AS SPECIFIED"
          and r["facts"]["late_200"] and r["facts"]["late_200"][0][0] == 3,
          (r["facts"]["late_200"], [c for c in r["checks"] if not c["ok"]]))
    ps4 = posts(lat=1.3, http=503, body="HTTP/1.1 503 Service Unavailable\r\n\r\n", which=3)
    r = K.judge(wire(ps4), ps4, rows(ps4))
    check("a late reply that is not 200 still fails, refusal text or not", failed(r, "POST 3 (3->4): reply"),
          [c for c in r["checks"] if not c["ok"]][:2])
    check("reply_class: a refusal text inside a 200 fails",
          K.reply_class({"http": 200, "body": "another mode change was still in progress for 1 s; this one "
                         "was not posted, not applied", "t_sent": 0, "t_ret": 0.01}).startswith("FAIL"))
    texts = {"mode change not taken by the gen_inhibit worker within 1 s; withdrawn, not applied": "",
             "bus is down for a firmware update": ""}
    check("reply_class: each refusal text inside a 200 fails, 8.1 row 12's included",
          all(K.reply_class({"http": 200, "body": t, "t_sent": 0, "t_ret": 0.01}).startswith("FAIL")
              for t in texts), texts)
    r = K.judge(wire(ps), ps, rows(ps, at=(8.52, 8.9), applied=False))
    check("the page after still in the old mode fails 'applied'", failed(r, "applied"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(3.5, 6.1), no_reset=True))
    check("tx_ok not restarting at a 3->3 fails 'a new arm'", failed(r, "a new arm"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(5.7, 6.0), cls_resid=1))
    check("a class off by one at the old arm's end fails the class balance", failed(r, "every class balances"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(5.7, 6.0), carried=1))
    check("a frame carried in balances (queued + carried_in)", not failed(r, "every class balances"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(5.7, 6.0), cls_held=1))
    check("a held frame balances (the held term)", not failed(r, "every class balances"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(6.25, 6.6), idn=-1, held=1))
    check("one short inside a change window while held is 1 passes", r["verdict"] == "AS SPECIFIED",
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(6.25, 6.6), idn=-1, held=0))
    check("one short inside a window with nothing held fails", failed(r, "identity on every page"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(6.25, 6.6), idn=1, held=1))
    check("one OVER inside a window fails", failed(r, "identity on every page"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps), ps, rows(ps, at=(4.75, 5.2), idn=-1, held=1))
    check("one short outside any window fails, even while held", failed(r, "identity on every page"),
          [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps, pages_gap=(9.0, 12.5)), ps, rows(ps))
    check("diag pages silent 3.5 s fail the cadence", failed(r, "cadence"), [c for c in r["checks"] if not c["ok"]])
    r = K.judge(wire(ps, dead_before=6.0), ps, rows(ps))
    check("the device not answering before a POST: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE",
          [c for c in r["preconditions"] if not c["ok"]])
    keep = SCHED
    SCHED = [(1.0, 3), (3.5, 3), (6.0, 3), (8.5, 4), (11.0, 4)]
    psn = posts()
    r = K.judge(wire(psn), psn, rows(psn))
    check("no 3->4->3 in the schedule: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE",
          [c for c in r["preconditions"] if not c["ok"]])
    SCHED = keep
    raw = {"t_sent": 10.01, "t_complete": 10.05, "t_end": 12.05}
    check("post_times: the reply is the raw record's t_complete, not the poster's return",
          K.post_times(100.0, [10.0, 12.1], raw) == (110.01, 110.05)
          and K.post_times(100.0, [10.0, 10.2], None) == (110.0, 110.2))
    run = {"mode": "inhibit", "arms": {"idle": {
        "base_epoch": 100.0, "post_s": [1.0, 3.03], "post_http": 200, "post_raw_timeout_s": 3.0,
        "post_schedule": [{"mode": 3, "post_s": [4.0, 6.05], "http": 200,
                           "raw": {"t_sent": 4.01, "t_complete": 4.03, "raw": "HTTP/1.1 200 OK"}}]}}}
    pr = K.posts_from_run(run)
    check("posts_from_run: the --arm-after POST is untimed with raw POSTs on; a scheduled one is timed "
          "from its raw record", pr[0]["untimed"] and pr[0]["mode"] == 3 and not pr[1].get("untimed")
          and abs(pr[1]["t_ret"] - 104.03) < 1e-9, pr)
    run["arms"]["idle"]["post_raw"] = {"t_sent": 1.045, "t_complete": 1.06, "raw": "HTTP/1.1 200 OK"}
    pr = K.posts_from_run(run)
    check("posts_from_run: with e4's post_raw the arm POST is timed from it",
          not pr[0]["untimed"] and abs(pr[0]["t_ret"] - 101.06) < 1e-9 and abs(pr[0]["t_sent"] - 101.045) < 1e-9, pr[0])
    psu = posts()
    psu[0] = dict(psu[0], t_ret=psu[0]["t_sent"] + 2.03, untimed=True)
    r = K.judge(wire(psu), psu, rows(psu))
    check("an untimed initial arm is neither late nor in the latency figures",
          r["verdict"] == "AS SPECIFIED" and not r["facts"]["late_200"]
          and r["facts"]["latency_ms"]["n"] == len(SCHED) - 1, (r["facts"]["late_200"], r["facts"]["latency_ms"]))
    psv = posts()
    psv[0] = dict(psv[0], http=500, untimed=True)
    r = K.judge(wire(psv), psv, rows(psv))
    check("an untimed reply that is not 200 still fails", failed(r, "POST 0 (None->3): reply"),
          [c for c in r["checks"] if not c["ok"]][:2])
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("a late 200 fails", 'rc.startswith("ok"),', 'rc in ("ok", "ok (untimed)"),'),
    ("any late reply passes", '    if post.get("http") != 200:',
     '    if post.get("http") != 200 and post["t_ret"] - post["t_sent"] <= REPLY_MAX_S:'),
    ("untimed initial arm judged by its time", '    if post.get("untimed"):\n        return "ok (untimed)"',
     '    if False:\n        return "ok (untimed)"'),
    ("refusal text in a 200 ignored", "    if any(r in body for r in REFUSALS):", "    if False:"),
    ("8.1 row 12's refusal text not known", '            "bus is down for a firmware update")', '            )'),
    ("applied not checked", 'pa.get("mode") == p["mode"],', 'True,'),
    ("new arm not checked", "na < nb,", "True,"),
    ("class balance without held", '+ k["depth"] + k["held"])', '+ k["depth"])'),
    ("class balance without carried_in", '(k["queued"] + k["carried_in"])', 'k["queued"]'),
    ("one short allowed with nothing held", "x == -1 and held == 1)", "x == -1)"),
    ("one over allowed in a window", "inside and x == -1 and held == 1)", "inside and abs(x) == 1 and held == 1)"),
    ("window ignored", "        inside = any(", "        inside = True or any("),
    ("cadence ignored", "spans and not over and not missing,", "spans and not missing,"),
    ("cadence split at every POST", "        if merged and abs(merged[-1][1] - a) < 1e-9:", "        if False:"),
    ("live precondition dropped", 'v.need("%s: live before (device answering)" % label, live,',
     'v.need("%s: live before (device answering)" % label, True,'),
    ("reply from the poster's return", "raw.get(\"t_complete\") is not None:", "raw.get(\"t_complete\") is None:"),
]


def mutants():
    src = open(os.path.join(HERE, "mode_change_judge.py"), encoding="utf-8").read()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "mode_change_mutants")   # outside SeaDrive
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "mode_change_judge.py"), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, cwd=os.getcwd(),
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.getcwd()))
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:80]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
