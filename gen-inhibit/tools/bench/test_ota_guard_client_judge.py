#!/usr/bin/env python3
"""Test of ota_guard_client_judge.py (B21 at the client, spec 9.3, topic (E), 2026-11-19), on
synthetic probe records: a whole 403 with its reason and nothing touched PASSes whatever the
connection's end; each can-fail the reviewer named (the 403 cut short, the image changed) and the
others (another status, no Content-Length, no reason, the mode after changed, a restart) FAIL; a
reading the harness never sent (the mode not read back as set, no page read in the record) is VOID,
and one sent and unanswered ERROR (the reviewer's reading (t), answer to b07f7984); an older record
with no page reads is VOID with its whole 403s counted (reading (w)).

    python3.13 -B test_ota_guard_client_judge.py [--mutants]
--mutants applies each edit in MUTANTS to a copy of the judge in scratch/ and runs this test on it:
every one must fail a check. Gen-inhibit tester, 2026-11-19.
"""
import copy
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("CLIENT_JUDGE_DIR") or HERE)
import ota_guard_client_judge as J  # noqa: E402

SHA = "ab" * 32
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


def page(t, up_ms, mode, sha=SHA, rtt=0.03):
    return {"epoch_sent": t, "epoch_answered": t + rtt, "error": None, "mode": mode, "uptime_ms": up_ms,
            "elf_sha256": sha, "reset_reason": "SW"}


def row(mode=3, **kw):
    """A refused request at t=100 (page before at 100.0, after at 101.0, the device up 500 s)."""
    r = {"mode": "INHIBIT", "mode_set": mode, "rep": 0, "set_http": 200, "mode_read": mode,
         "page_before": page(100.0, 500000, mode), "page_after": page(101.0, 501000, mode),
         "status_line": "HTTP/1.1 403 Forbidden", "reply_complete": True, "content_length": 27,
         "reply_body": "OTA refused: bus is owned\r\n", "ending": "fin"}
    r.update(kw)
    return r


def rec(*rows):
    return {"body": "multipart", "page_first": page(90.0, 490000, 0), "rows": list(rows)}


def v(r):
    return J.judge_rep(r, page(90.0, 490000, 0))


def main():
    good = v(row())
    check("a whole 403 with its reason, nothing touched: PASS", good[0] == "PASS", good)
    for end in ("rst (ConnectionResetError)", "open: client closed", "timeout"):
        check("the ending %r is recorded, not judged: still PASS" % end, v(row(ending=end))[0] == "PASS")
    # THE CAN-FAILS: the 403 cut short; the image changed
    r = v(row(reply_complete=False, reply_body="OTA ref"))
    check("the 403 cut short in its body: FAIL, saying so", r[0] == "FAIL" and "cut short" in r[1][0], r)
    a = row()
    a["page_after"] = page(101.0, 501000, 3, sha="cd" * 32)
    r = v(a)
    check("the image after not the run's first: FAIL", r[0] == "FAIL" and any("image after" in w for w in r[1]), r)
    r = v(row(status_line="HTTP/1.1 200 OK"))
    check("a 200 in place of the 403: FAIL", r[0] == "FAIL" and "no 403" in r[1][0], r)
    r = v(row(status_line=None, reply_complete=False, content_length=None, reply_body=None, ending="rst (x)"))
    check("nothing reached the client: FAIL", r[0] == "FAIL" and "no 403" in r[1][0], r)
    r = v(row(content_length=None, reply_complete=False))
    check("a 403 with no Content-Length: FAIL, saying it cannot be known whole",
          r[0] == "FAIL" and "no Content-Length" in r[1][0], r)
    r = v(row(reply_body="", content_length=0))
    check("a whole 403 with an empty body: FAIL (no reason)", r[0] == "FAIL" and "no reason" in r[1][0], r)
    r = v(row(reply_body="  \r\n", content_length=4))
    check("a body of white space is no reason: FAIL", r[0] == "FAIL", r)
    a = row()
    a["page_after"] = page(101.0, 501000, 0)
    r = v(a)
    check("the mode after not the mode set: FAIL", r[0] == "FAIL" and any("mode after" in w for w in r[1]), r)
    a = row()
    a["page_after"] = page(101.0, 300, 3)              # restarted 0.3 s before the read
    r = v(a)
    check("a restart (uptime fell): FAIL, not continuous", r[0] == "FAIL" and any("uptime" in w for w in r[1]), r)
    a = row()
    a["page_after"] = page(101.0, 501500, 3)           # 1.5 s of device time in a host interval of 0.97..1.03 s
    check("uptime ahead of the host's interval: FAIL", v(a)[0] == "FAIL", v(a))
    a = row()
    a["page_after"] = page(101.0, 500900, 3)           # 0.9 s, under the interval's 0.97 s
    check("uptime behind the host's interval: FAIL", v(a)[0] == "FAIL", v(a))
    for up, label in ((500970, "at the interval's low end"), (501030, "at its high end")):
        a = row()
        a["page_after"] = page(101.0, up, 3)
        check("uptime %s: PASS" % label, v(a)[0] == "PASS", v(a))
    # reading (t): a reading the harness never sent is VOID; one sent and unanswered is ERROR
    r = v(row(set_http=500, page_before=page(100.0, 500000, 0), status_line=None, reply_complete=None))
    check("the mode set not read back as set (no request sent): VOID", r[0] == "VOID" and "not read back" in r[1][0], r)
    r = v(row(page_before=page(100.0, 500000, 1), status_line=None, reply_complete=None))
    check("the mode read back another mode (no request sent): VOID", r[0] == "VOID", r)
    r = v(row(page_before=dict(page(100.0, None, None), error="GET /gen_inhibit returned None", elf_sha256=None),
              status_line=None, reply_complete=None))
    check("the read after the mode set sent and unanswered: ERROR", r[0] == "ERROR" and "did not answer" in r[1][0], r)
    a = row()
    a["page_after"] = dict(page(101.0, None, None), error="GET /gen_inhibit returned None")
    r = v(a)
    check("the page read after the request sent and unanswered: ERROR", r[0] == "ERROR", r)
    a = row(reply_complete=False)
    a["page_after"] = dict(page(101.0, None, None), error="x")
    check("... but a 403 cut short beside it still FAILs", v(a)[0] == "FAIL", v(a))
    a = row()
    a["page_before"] = dict(a["page_before"], elf_sha256=None)
    check("a page before answering without the image: ERROR", v(a)[0] == "ERROR", v(a))
    a = row()
    del a["page_after"]
    r = v(a)
    check("a record with no read after (never sent): VOID", r[0] == "VOID" and "no page read after" in r[1][0], r)
    a = row(reply_complete=False)
    del a["page_after"]
    check("... but a 403 cut short beside it still FAILs", v(a)[0] == "FAIL", v(a))
    r = J.judge_rep(row(), None)
    check("a record with no first page: VOID", r[0] == "VOID" and "first" in r[1][0], r)
    # a record from before the reply fields: raw_head when it holds the whole reply
    head = "HTTP/1.1 403 Forbidden\r\nContent-Type: text/html\r\nContent-Length: 25\r\n\r\n"
    old = {k: x for k, x in row().items() if k not in ("reply_complete", "content_length", "reply_body")}
    whole = dict(old, raw_head=head + "gen_inhibit is armed; P1", bytes=len(head) + 24)
    whole["raw_head"] += "!"
    whole["bytes"] += 1
    check("an older record: a whole reply in raw_head is judged from it (PASS)", v(whole)[0] == "PASS", v(whole))
    short = dict(old, raw_head=head + "gen_inhibit", bytes=len(head) + 11)
    check("an older record: a 403 cut short in raw_head: FAIL", v(short)[0] == "FAIL" and "cut short" in v(short)[1][0],
          v(short))
    part = dict(old, raw_head=(head + "x" * 400)[:300], bytes=520)
    check("an older record: raw_head only part of the reply: ERROR, can't be told",
          v(part)[0] == "ERROR" and "can't be told" in v(part)[1][0], v(part))
    # reading (w): an older record with no page reads around the requests (the e3d9437 baseline's form)
    base = {k: x for k, x in whole.items() if k not in ("page_before", "page_after", "mode_set")}
    base.update(mode_read=3)        # the older record names the mode only ("INHIBIT")
    r = v(base)
    check("an older record with no page read before or after: VOID for the reads, never PASS",
          r[0] == "VOID" and "no page read before and after" in r[1][0], r)
    r = v(dict(base, mode_read=1))
    check("an older record whose mode read back another mode: VOID, not read back", r[0] == "VOID"
          and "not read back" in r[1][0], r)
    check("... and its whole 403 with its reason still counted", J.whole_403(base), base)
    # the run (FAIL, then ERROR, then VOID, then PASS)
    unanswered = row()
    unanswered["page_after"] = dict(page(101.0, None, None), error="x")
    notset = row(set_http=500, page_before=page(100.0, 500000, 0))
    check("run: every rep PASS -> PASS", J.judge(rec(row(), row(mode=1)))[0] == "PASS")
    a = row()
    a["page_after"] = page(101.0, 300, 3)
    check("run: one FAIL among PASSes, an ERROR and a VOID -> FAIL",
          J.judge(rec(row(), unanswered, a, notset))[0] == "FAIL")
    check("run: an ERROR among PASSes and a VOID -> ERROR", J.judge(rec(row(), notset, unanswered))[0] == "ERROR")
    check("run: a VOID among PASSes -> VOID, never PASS", J.judge(rec(row(), notset))[0] == "VOID")
    check("run: no rep -> VOID", J.judge(rec())[0] == "VOID")
    rr = rec(row())
    rr["page_first"] = dict(rr["page_first"], elf_sha256=None)
    check("run: a first page answering without the image -> ERROR, never PASS", J.judge(rr)[0] == "ERROR", J.judge(rr))
    rr = rec(row())
    del rr["page_first"]
    check("run: no first page in the record -> VOID", J.judge(rr)[0] == "VOID", J.judge(rr))
    rr = rec(row())
    rr["page_first"] = dict(rr["page_first"], elf_sha256="cd" * 32)
    check("run: the image all along not the run's first page's -> FAIL", J.judge(rr)[0] == "FAIL")
    # the printed lines
    import io
    import contextlib
    import gi_paths
    d = gi_paths.scratch("test-ota-guard-client-judge")
    gi_paths.remove_tree(d)
    os.makedirs(d)
    try:
        p = os.path.join(d, "r.json")
        with open(p, "w", encoding="ascii") as f:
            json.dump(rec(row(), row(mode=1, ending="rst (x)"), base), f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = J.main([p])
    finally:
        gi_paths.remove_tree(d)
    lines = out.getvalue().splitlines()
    check("main: a header naming the record, a line per rep with its ending recorded, then the verdict",
          rc == 1 and len(lines) == 5 and lines[0] == "judged at the client from r.json (spec 9.3)"
          and "ending rst (x) (recorded, not judged)" in lines[2]
          and lines[-1] == "verdict VOID: 3 reps, 2 PASS, 0 FAIL, 0 ERROR, 1 VOID, 3 with the whole 403 and its "
                           "reason (multipart body)", lines)
    print("\n%d ok, %d FAIL" % (oks, len(fails)))
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


MUTANTS = [
    ("the status not checked", '    if len(parts) < 2 or parts[1] != "403":\n', "    if not parts:\n"),
    ("a reply cut short taken whole", "    elif not row.get(\"reply_complete\"):\n", "    elif False:\n"),
    ("no Content-Length accepted", '    if row.get("content_length") is None:\n        out.append',
     '    if False:\n        out.append'),
    ("an empty reason accepted", '    if row.get("reply_complete") and not (row.get("reply_body") or "").strip():\n',
     '    if row.get("reply_complete") and not (row.get("reply_body") or ""):\n'),
    ("the mode after not compared", '    if after.get("mode") != row["mode_set"]:\n', "    if False:\n"),
    ("the image after not compared", '    if after.get("elf_sha256") != first.get("elf_sha256"):', "    if False:"),
    ("the uptime not compared", "    if not ok:\n        bad.append(\"the uptime", "    if False:\n        bad.append(\"the uptime"),
    ("uptime: no upper bound", "    return lo_b <= el <= hi_b,", "    return lo_b <= el,"),
    ("uptime: the interval loosened to the reads' far ends both ways",
     '    lo = after["epoch_sent"] - before["epoch_answered"]\n', '    lo = after["epoch_sent"] - before["epoch_answered"] - 0.1\n'),
    ("a mode not read back judged anyway",
     '    if row.get("set_http") != 200 or mode_read != row.get("mode_set"):\n', "    if False:\n"),
    ("a mode not read back ERROR, not VOID (before reading (t))",
     '        return VOID, ["the mode set was not read back', '        return ERROR, ["the mode set was not read back'),
    ("a read never sent ERROR, not VOID", '        return (FAIL if bad else VOID), bad + ["no page read',
     '        return (FAIL if bad else ERROR), bad + ["no page read'),
    ("a read sent and unanswered VOID, not ERROR", '        return (FAIL if bad else ERROR), bad + ["the page read %s did',
     '        return (FAIL if bad else VOID), bad + ["the page read %s did'),
    ("a read never sent taken as PASS", '        return (FAIL if bad else VOID), bad + ["no page read',
     '        return (FAIL if bad else PASS), bad + ["no page read'),
    ("the read after the mode set unanswered read as not read back",
     '    if sb == UNANSWERED and before.get("mode") is None:\n', "    if False:\n"),
    ("an ERROR outranks a FAIL", "RANK = {PASS: 0, VOID: 1, ERROR: 2, FAIL: 3}", "RANK = {PASS: 0, VOID: 1, ERROR: 3, FAIL: 2}"),
    ("a VOID outranks an ERROR", "RANK = {PASS: 0, VOID: 1, ERROR: 2, FAIL: 3}", "RANK = {PASS: 0, VOID: 2, ERROR: 1, FAIL: 3}"),
    ("no rep is a PASS", "    if not out:\n        return VOID, out\n", "    if not out:\n        return PASS, out\n"),
    ("an older record's raw_head never read", '    if "reply_complete" in row:\n        return row\n',
     '    if True:\n        return row\n'),
    ("an older record's partial raw_head taken as the whole reply",
     '    if row.get("bytes") is None or row["bytes"] != len(raw) or len(raw) >= RAW_HEAD_KEEP:\n',
     '    if row.get("bytes") is None:\n'),
    ("the first page not needed", '    states = {"before": sb, "after": read_state(after), "first": read_state(first)}',
     '    states = {"before": sb, "after": read_state(after), "first": ANSWERED if first else NOT_SENT}'),
    ("an older record's mode not taken from its name", "        row = dict(row, mode_set=MODE_NUMBER.get(row.get(\"mode\")))\n",
     "        pass\n"),
    ("the whole-403 count from the raw row (an older record's never counted)",
     "    full = with_reply(row)\n    return full is not None", "    full = row\n    return \"reply_complete\" in full"),
]


def mutants():
    import gi_paths
    src = open(os.path.join(HERE, "ota_guard_client_judge.py"), encoding="utf-8", newline="").read()
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("ERROR    control: the unmutated judge fails this test")
        return 1
    base = gi_paths.scratch("mutants-ota_guard_client_judge")
    killed = errors = 0
    try:
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "ota_guard_client_judge.py"), "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new))
            env = dict(os.environ, CLIENT_JUDGE_DIR=d, PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               timeout=120, env=env)
            failed = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
            dead = bool(failed) and "Traceback" not in r.stderr
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else "SURVIVED", n, name, ("; " + failed[0][:90]) if failed else ""))
    finally:
        gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if sys.argv[1:] == ["--mutants"] else main())
