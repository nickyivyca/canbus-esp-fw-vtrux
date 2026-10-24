#!/usr/bin/env python3
"""Tests batch3_bench_judge on synthetic runs (test_interlock_judge's Sim for the
wire, synthetic pages) and ota_armed_bench.wait_live on a fake page reader. No
device. --mutants runs its mutants; each must make a check fail.

    REVERSE_IT_DIR=<reverse-it root> py -3.14 tools/gen_inhibit/bench/test_batch3_bench_judge.py [--mutants]
Run from the reverse-it root. Added 2026-10-16.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch3_bench_judge as B     # noqa: E402
import ota_armed_bench as O        # noqa: E402  (before test_interlock_judge, which puts
import test_interlock_judge as T   # noqa: E402   the real folder first on sys.path)

FAILS = []
RATE = 50
SHA = "ab" * 32
ARM = {"mode": 3, "t_ret": 1.0}
T0, T1 = 8.0, 11.0
REPLY = ("HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\nContent-Length: 56\r\n\r\n"
         "gen_inhibit is armed; POST /gen_inhibit_set?mode=0 first")


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def wire(live_from=1.0, silent=None, diag_gap=None, t_end=19.5):
    s = T.Sim()
    s.vcm(0.5, t_end)
    s.device(lambda t: t >= live_from and not (silent and silent[0] <= t < silent[1]))
    s.pages(0.3, t_end, lambda t: {"live": t >= live_from})
    for i, off in ((0x7F3, 0.33), (0x7F9, 0.34)):
        s.train(i, off, t_end, 1.0, lambda t: "00" * 8)
    if diag_gap:
        s.f = [x for x in s.f if not (x[1] in T.J.DIAG_IDS and diag_gap[0] <= x[0] < diag_gap[1])]
    return s.f


def page(t, mode=3, worker="armed", boot=-100.0, sha=SHA, viol=0, tx_from=1.0, **kw):
    p = {"mode": mode, "worker": worker, "uptime_ms": int((t - boot) * 1000), "elf_sha256": sha,
         "bus_violations": viol, "tx_ok": int((t - tx_from) * RATE) if mode == 3 and t >= tx_from else 0}
    p.update(kw)
    if p.get("bus_violations") is None:
        del p["bus_violations"]
    return p


def armed_rows(edit=None, t0=T0, t1=T1):
    """Reads every 0.25 s; one sent inside the upload is answered after it ends."""
    out, t = [], 0.5
    while t < 19.4:
        resp = t1 + 0.05 if t0 <= t < t1 else t + 0.02
        p = page(t) if t >= 1.0 else page(t, mode=0, worker="idle")
        if edit:
            p = edit(t, p)
        out.append({"t_req": t, "t_resp": resp, "page": p})
        t += 0.25
    return out


def armed_rec(status=None, head=REPLY, upload=True):
    if not upload:
        return {"upload": None, "abandoned": "no live inhibit"}
    return {"upload": {"epoch_start": T0, "epoch_end": T1, "t_reply": 0.03,
                       "status_line": status or head.split("\r\n")[0], "raw_head": head}}


def failed_rec(kind="short", verdict="a1", edit=None, inside=False, t0=5.0, t1=9.0, inflight=False):
    rows, t = [], 0.5
    while t < 45.0:
        resp = t1 + 0.05 if t0 <= t < t1 else t + 0.02
        if inflight and abs(t - 4.75) < 1e-9:
            rows.append({"t_req": t0 - 0.01, "t_resp": t0 + 0.25, "page": page(t0 - 0.01, mode=0, worker="idle")})
        if inside and abs(t - 6.0) < 1e-9:
            resp = 6.05
        p = page(t, mode=0, worker="updating" if inside and abs(t - 6.0) < 1e-9 else "idle")
        if edit:
            p = edit(t, p)
        rows.append({"t_req": t, "t_resp": resp, "page": p})
        t += 0.25
    return {"kind": kind, "upload": {"epoch_start": t0, "epoch_end": t1, "status_line": None, "ending": "fin"},
            "pages": rows, "bench_flash_verdict": [verdict, "detail"]}


def failed_by(r, part):
    return [c["name"] for c in r["checks"] if not c["ok"] and part in c["name"]]


def main():
    W = wire()
    r = B.judge_armed(armed_rec(), W, armed_rows(), ARM)
    check("armed: the right device is AS SPECIFIED", r["verdict"] == "AS SPECIFIED",
          [c for c in r["checks"] + r["preconditions"] if not c["ok"]])
    r = B.judge_armed(armed_rec(head="HTTP/1.1 500 Internal Server Error\r\n\r\ngen_inhibit worker did not answer "
                                     "within 1 s; nothing touched"), W, armed_rows(), ARM)
    check("armed: the worker's 500 fails the reply", failed_by(r, "the reply is the 403"))
    r = B.judge_armed(armed_rec(head="HTTP/1.1 403 Forbidden\r\n\r\nsomething else"), W, armed_rows(), ARM)
    check("armed: a 403 without the armed text fails the reply", failed_by(r, "the reply is the 403"))
    r = B.judge_armed(armed_rec(head=REPLY.replace("403 Forbidden", "200 OK")), W, armed_rows(), ARM)
    check("armed: a 200 carrying the text fails the reply", failed_by(r, "the reply is the 403"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: dict(p, worker="updating") if 10.0 <= t < 11.5 else p), ARM)
    check("armed: a page reading worker updating fails", failed_by(r, "every page from before"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: dict(p, mode=0) if 11.0 <= t < 11.5 else p), ARM)
    check("armed: a page reading mode 0 after the upload fails", failed_by(r, "every page from before"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: page(t, tx_from=11.0) if t >= 11.0 else p), ARM)
    check("armed: tx_ok restarting (a new arm) fails 'the same arm'", failed_by(r, "the same arm"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: dict(p, elf_sha256="cd" * 32) if t >= 11.0 else p), ARM)
    check("armed: a changed elf_sha256 fails", failed_by(r, "elf_sha256 unchanged"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: dict(p, uptime_ms=int((t - 10.5) * 1000))
                                                 if t >= 11.0 else p), ARM)
    check("armed: a restart (uptime reset) fails", failed_by(r, "uptime continuous"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: dict(p, bus_violations=1) if t >= 11.0 else p), ARM)
    check("armed: a violation counted fails", failed_by(r, "bus_violations"))
    r = B.judge_armed(armed_rec(), W, armed_rows(lambda t, p: {k: x for k, x in p.items() if k != "bus_violations"}), ARM)
    check("armed: a page without bus_violations fails", failed_by(r, "bus_violations"))
    r = B.judge_armed(armed_rec(), wire(silent=(9.0, 9.8)), armed_rows(), ARM)
    check("armed: the device silent 0.8 s inside the upload fails 'answers'", failed_by(r, "the device answers"))
    r = B.judge_armed(armed_rec(), wire(silent=(11.3, 11.9)), armed_rows(), ARM)
    check("armed: silent 0.6 s in the 1 s after the upload's end fails too", failed_by(r, "the device answers"))
    r = B.judge_armed(armed_rec(), wire(silent=(9.2, 9.8)), armed_rows(), ARM)
    check("armed: a 0.6 s silence straddling two half-seconds fails", failed_by(r, "the device answers"))
    r = B.judge_armed(armed_rec(), wire(silent=(9.2, 9.6)), armed_rows(), ARM)
    check("armed: a 0.4 s silence passes", not failed_by(r, "the device answers"),
          [c for c in r["checks"] if not c["ok"]])
    r = B.judge_armed(armed_rec(), wire(diag_gap=(8.0, 11.5)), armed_rows(), ARM)
    check("armed: diag silent 3.5 s across the upload fails", failed_by(r, "diag id silent"))
    r = B.judge_armed(armed_rec(upload=False), W, armed_rows(), ARM)
    check("armed: no upload made: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE")
    r = B.judge_armed(armed_rec(), wire(silent=(7.0, 8.0)), armed_rows(), ARM)
    check("armed: not live just before the upload: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE")

    WA = wire()
    r = B.judge_failed(failed_rec(), WA, ARM)
    check("failed short: AS SPECIFIED, UPDATING reported not observable", r["verdict"] == "AS SPECIFIED"
          and "not observable on the bench" in r["facts"].get("updating", ""),
          ([c for c in r["checks"] + r["preconditions"] if not c["ok"]], r["facts"].get("updating")))
    r = B.judge_failed(failed_rec(kind="truncated", verdict="b"), WA, ARM)
    check("failed truncated: verdict b is AS SPECIFIED", r["verdict"] == "AS SPECIFIED")
    r = B.judge_failed(failed_rec(kind="truncated", verdict="c"), WA, ARM)
    check("failed truncated: TAKEN is reported as a finding", failed_by(r, "FINDING"))
    r = B.judge_failed(failed_rec(kind="truncated", verdict="a2+c"), WA, ARM)
    check("failed truncated: a2+c is a finding too", failed_by(r, "FINDING"))
    r = B.judge_failed(failed_rec(kind="short", verdict="b"), WA, ARM)
    check("failed short: verdict b (a reply to the half-close) passes, as row 11 asks only for nothing taken",
          r["verdict"] == "AS SPECIFIED" and r["facts"].get("predicted") == "a1")
    r = B.judge_failed(failed_rec(kind="truncated", verdict="a1"), WA, ARM)
    check("failed truncated: verdict a1 passes too", r["verdict"] == "AS SPECIFIED")
    r = B.judge_failed(failed_rec(kind="short", verdict="c"), WA, ARM)
    check("failed short: TAKEN fails 'nothing taken'", failed_by(r, "nothing taken"))
    r = B.judge_failed(failed_rec(kind="short", verdict="undecided"), WA, ARM)
    check("failed short: an undecided verdict fails 'nothing taken'", failed_by(r, "nothing taken"))
    r = B.judge_failed(failed_rec(inside=True), WA, ARM)
    check("failed: a read answered inside reading updating passes, as a check", r["verdict"] == "AS SPECIFIED"
          and any("inside the upload reads worker updating" in c["name"] for c in r["checks"]))
    r = B.judge_failed(failed_rec(inside=True, edit=lambda t, p: dict(p, worker="idle")), WA, ARM)
    check("failed: a read answered inside reading idle fails", failed_by(r, "inside the upload"))
    r = B.judge_failed(failed_rec(inflight=True), WA, ARM)
    check("failed: a read sent before the connect, answered inside reading idle, is not inside: "
          "AS SPECIFIED, UPDATING not observable, listed in flight",
          r["verdict"] == "AS SPECIFIED" and "not observable" in str(r["facts"].get("updating"))
          and r["facts"].get("in_flight_at_connect") == [(-0.01, 0.25, "idle")], r["facts"])
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, worker="armed", mode=3) if t >= 20.0 else p), WA, ARM)
    check("failed: a page after reading armed fails", failed_by(r, "every page after"))
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, worker="updating") if 9.5 <= t < 10.0 else p), WA, ARM)
    check("failed: a page after still reading updating fails", failed_by(r, "every page after"))
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, bus_violations=1) if t >= 9.0 else p), WA, ARM)
    check("failed: a violation counted fails", failed_by(r, "bus_violations"))
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, uptime_ms=int((t - 9.5) * 1000)) if t >= 9.5 else p), WA, ARM)
    check("failed: a restart fails 'uptime continuous'", failed_by(r, "uptime continuous"))
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, elf_sha256="cd" * 32) if t >= 9.5 else p), WA, ARM)
    check("failed: another image fails", failed_by(r, "elf_sha256 unchanged"))
    r = B.judge_failed(failed_rec(), wire(live_from=99.0), ARM)
    check("failed: an arm after it that never answers fails", failed_by(r, "the arm after it works"))
    r = B.judge_failed(failed_rec(edit=lambda t, p: dict(p, worker="armed", mode=3) if t < 5.0 else p), WA, ARM)
    check("failed: not IDLE before the upload: INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE")

    clock = {"t": 0.0}
    seq = [{"mode": 0}, {"mode": 3, "tx_ok": 5}, {"mode": 3, "tx_ok": 40}]
    got = O.wait_live(10, read=lambda: seq.pop(0) if seq else {"mode": 3, "tx_ok": 99},
                      now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    check("wait_live: the first page in mode 3 with tx_ok >= 30", got == {"mode": 3, "tx_ok": 40}, got)
    clock["t"] = 0.0
    got = O.wait_live(2, read=lambda: {"mode": 3, "tx_ok": 1}, now=lambda: clock["t"],
                      sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    check("wait_live: never live -> None", got is None, got)
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("batch3_bench_judge.py", "armed text not required", "            and ARMED_TEXT in head and WORKER_500 not in head,",
     "            and WORKER_500 not in head,"),
    ("batch3_bench_judge.py", "a 200 accepted as the refusal", '(up.get("status_line") or "").startswith("HTTP/1.1 403")', "True"),
    ("batch3_bench_judge.py", "worker state during not checked",
     '           if r["page"].get("worker") != "armed" or r["page"].get("mode") != 3]', '           if r["page"].get("mode") != 3]'),
    ("batch3_bench_judge.py", "same arm not checked", 'and pa["tx_ok"] > pb["tx_ok"],', "and True,"),
    ("batch3_bench_judge.py", "boot jumps ignored", "    return (not jumps and len(shas) == 1", "    return (len(shas) == 1"),
    ("batch3_bench_judge.py", "elf_sha256 ignored", "    return (not jumps and len(shas) == 1 and shas[0] is not None,",
     "    return (not jumps,"),
    ("batch3_bench_judge.py", "armed violations not checked",
     '    v.check("bus_violations unchanged", "bus_violations" in pb and pb.get("bus_violations") == pa.get("bus_violations"),',
     '    v.check("bus_violations unchanged", True,'),
    ("batch3_bench_judge.py", "answers checked over the upload only, not the settle",
     "    empty, gap = answer_gaps(c, t0, t1 + SETTLE_S)", "    empty, gap = answer_gaps(c, t0, t1)"),
    ("batch3_bench_judge.py", "long gaps ignored", "            not empty, \"largest gap", "            True, \"largest gap"),
    ("batch3_bench_judge.py", "gap threshold loosened to 1 s", "    return [g for g in gaps if g[1] > WINDOW_S]",
     "    return [g for g in gaps if g[1] > 1.0]"),
    ("batch3_bench_judge.py", "diag cadence ignored",
     "            all(g <= J.DIAG_GAP_MAX_S for g in dg.values()), dg)", "            True, dg)"),
    ("batch3_bench_judge.py", "live precondition dropped",
     '    v.need("live before the upload (device answering in the 0.5 s before)", c.dev_answering(t0 - 0.5, t0),',
     '    v.need("live before the upload (device answering in the 0.5 s before)", True,'),
    ("batch3_bench_judge.py", "a taken truncated image not a finding", '    if kind == "truncated" and taken:', "    if False:"),
    ("batch3_bench_judge.py", "a taken short upload passes",
     '    v.check("nothing taken (bench_flash verdict a1 or b)", verdict in NOT_TAKEN,',
     '    v.check("nothing taken (bench_flash verdict a1 or b)", not taken,'),
    ("batch3_bench_judge.py", "a read in flight at the connect counted inside",
     '    inside = [r for r in ps if r["t_req"] >= t0 and t0 < r["t_resp"] < t1]',
     '    inside = [r for r in ps if t0 < r["t_resp"] < t1]'),
    ("batch3_bench_judge.py", "an inside read reading idle accepted",
     'for r in inside if r["page"].get("worker") != "updating"]', 'for r in inside if r["page"].get("worker") not in ("updating", "idle")]'),
    ("batch3_bench_judge.py", "pages after not checked for idle",
     '           if r["page"].get("worker") != "idle" or r["page"].get("mode") != 0]', "           if False]"),
    ("batch3_bench_judge.py", "failed violations not checked",
     '    v.check("bus_violations unchanged", "bus_violations" in pb and pb.get("bus_violations") == pz.get("bus_violations"),',
     '    v.check("bus_violations unchanged", True,'),
    ("batch3_bench_judge.py", "the arm after not checked", "n >= MIN_ANSWERS,", "True,"),
    ("batch3_bench_judge.py", "IDLE-before precondition dropped",
     '           before and before[-1]["page"].get("worker") == "idle" and before[-1]["page"].get("mode") == 0,',
     "           True,"),
    ("ota_armed_bench.py", "wait_live takes any mode-3 page",
     'isinstance(pg.get("tx_ok"), int) and pg["tx_ok"] >= LIVE_TX:', "True:"),
]


def mutants():
    base = gi_paths.scratch("mutants-batch3_bench_judge_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for n, (fname, name, old, new) in enumerate(MUTANTS, 1):
        src = open(os.path.join(HERE, fname), encoding="utf-8").read()
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f in ("batch3_bench_judge.py", "ota_armed_bench.py"):
            shutil.copy(os.path.join(HERE, f), d)
        open(os.path.join(d, fname), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, cwd=os.getcwd(), timeout=300,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.getcwd()))
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:90]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
