#!/usr/bin/env python3
"""Tests the raw-POST path of e4_device_calibration.py (--post-raw-timeout, --post-chain).

    py -3.14 test_e4_rawpost.py          # exits 1 on any failure

Added 2026-10-14 with the path itself (reviewer: the missing INHIBIT reply from
OBSERVE). Checked here, against test_raw_http_probe.py's fake server:

  reply_failed      a clean complete 200 with a JSON body is not a failure; a
                    reset, a cut body, a bad Content-Length, a non-JSON body, a
                    bad status line or no reply each is
  ModePoster        records the raw reply, its HTTP status, and on a failed reply
                    a fresh GET; never raises (the 2026-10-12 thread did)
  schedule_due      with --post-chain the next POST waits for the previous one;
                    without it, it does not
  parse_netstat     keeps the device's rows only, by host, not by prefix
  run_arm wiring    arms now, or defers to a poster, by --arm-after alone; the
                    pre-replay page read by --post-raw-timeout alone (2026-10-16)
"""

import sys
import time

import e4_device_calibration as E4
import raw_http_probe as RHP
import test_raw_http_probe as T

FAILS = []


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def raw_case(name, timeout=3.0):
    r, _ = T.probe(name, timeout=timeout)
    return r


def run_poster(post_case, get_case="good", timeout=3.0):
    f_post = T.Fake(T.CASES[post_case])
    f_get = T.Fake(T.CASES[get_case])
    f_post.start()
    f_get.start()
    calls = []
    real = RHP.raw_request

    def routed(host, path, timeout=30.0, method="POST", port=80, base=0.0, **kw):
        calls.append((method, path))
        p = f_post.port if method == "POST" else f_get.port
        return real(host, path, timeout=timeout, method=method, port=p, base=base, **kw)

    E4.RHP.raw_request = routed
    try:
        p = E4.ModePoster(3)
        p.base = time.perf_counter()
        p.start()
        p.join(timeout=4 * timeout + 10)
    finally:
        E4.RHP.raw_request = real
    return p, calls


class _Stop(Exception):
    pass


def run_arm_wiring(arm_after, raw_timeout):
    """Drive run_arm with fakes as far as the witness start; return what it did."""
    did = {"calls": [], "posters": []}

    class Bus:
        def __init__(self, **kw):
            pass

        def recv(self, timeout=0.0):
            return None

    class Can:
        pass
    fake_can = Can()
    fake_can.Bus = Bus
    fake_can.Message = lambda **kw: kw

    def gi(path, method="GET", timeout=None):
        did["calls"].append((method, path))
        return 200, "{}"

    class Poster:
        def __init__(self, mode):
            did["posters"].append(mode)

    def witness(*a, **kw):
        raise _Stop()

    saved = {k: getattr(E4, k) for k in ("can", "gi", "ModePoster", "Witness", "device_sockets",
                                         "ARM_AFTER_S", "POST_RAW_TIMEOUT")}
    saved_sleep = E4.time.sleep
    try:
        E4.can, E4.gi, E4.ModePoster, E4.Witness = fake_can, gi, Poster, witness
        E4.device_sockets = lambda: []
        E4.ARM_AFTER_S, E4.POST_RAW_TIMEOUT = arm_after, raw_timeout
        E4.time.sleep = lambda s: None
        try:
            E4.run_arm("idle", [], False, {}, mode=3)
            did["reached_witness"] = False
        except _Stop:
            did["reached_witness"] = True
    finally:
        for k, v in saved.items():
            setattr(E4, k, v)
        E4.time.sleep = saved_sleep
    return did


def check_run_arm_wiring():
    """2026-10-16: the arm's if/else had been split by the pre-read block, so a run
    with neither flag crashed after arming and one with both never deferred."""
    for arm_after, raw, posts_now, deferred, pre_get in [
            (None, None, True, False, False),
            (None, 3.0, True, False, True),
            (5.0, None, False, True, False),
            (5.0, 3.0, False, True, True)]:
        tag = "run_arm(arm_after=%s, raw=%s)" % (arm_after, raw)
        try:
            d = run_arm_wiring(arm_after, raw)
        except Exception as e:      # the 2026-10-16 crash was a TypeError here
            check(tag + ": runs to the witness", False, repr(e))
            continue
        posts = [c for c in d["calls"] if c[0] == "POST"]
        gets = [c for c in d["calls"] if c[0] == "GET"]
        check(tag + ": runs to the witness", d["reached_witness"])
        check(tag + ": %s POST before the replay" % ("one" if posts_now else "no"),
              len(posts) == (1 if posts_now else 0), posts)
        check(tag + ": %s deferred poster" % ("one" if deferred else "no"),
              d["posters"] == ([3] if deferred else []), d["posters"])
        check(tag + ": pre-replay page read %s" % ("made" if pre_get else "not made"),
              len(gets) == (1 if pre_get else 0), gets)


def main():
    E4.HOST = "127.0.0.1"
    E4.POST_RAW_TIMEOUT = 3.0

    # reply_failed
    for name, want in [("good", False), ("late_2s", False), ("complete_open", False),
                       ("rst_no_reply", True), ("fin_no_reply", True), ("cl_longer", True),
                       ("cl_shorter", True), ("cut_mid_json_cl", True), ("cut_mid_json_nocl", True),
                       ("cut_then_rst", True), ("garbage_status", True), ("chunked_cut", True)]:
        r = raw_case(name)
        check("reply_failed(%s) is %s" % (name, want), E4.reply_failed(r) is want, r["ending"])
    r = raw_case("good")
    r2 = dict(r, status_line="HTTP/1.1 500 Internal Server Error")
    check("reply_failed: a complete 500 is a failure", E4.reply_failed(r2) is True)
    check("reply_failed(None) is True", E4.reply_failed(None) is True)
    # The ending alone must decide it: a clean body that ended in a RST or a timeout.
    for ending in ("rst", "timeout", "connect_error"):
        check("reply_failed: a good body ending %s is a failure" % ending,
              E4.reply_failed(dict(r, ending=ending)) is True)

    # ModePoster, good reply: no follow-up.
    p, calls = run_poster("good")
    check("poster good: status 200", p.status == 200, p.status)
    check("poster good: raw recorded, ending fin", p.raw is not None and p.raw["ending"] == "fin")
    check("poster good: no follow-up GET", p.followup is None and calls == [("POST", "/gen_inhibit_set?mode=3&offset=500")], calls)
    check("poster good: t_end is the raw t_end", p.t_end == p.raw["t_end"])
    check("poster good: no exception", p.exc is None, p.exc)
    check("poster good: sockets recorded before and after",
          p.sockets_before is not None and p.sockets_after is not None)

    # ModePoster, cut reply: a follow-up GET on a new connection.
    p, calls = run_poster("cut_mid_json_cl")
    check("poster cut: follow-up GET made", p.followup is not None and calls[-1] == ("GET", "/gen_inhibit"), calls)
    check("poster cut: follow-up answered", p.followup is not None and p.followup["ending"] == "fin"
          and p.followup["body_json_ok"] is True)
    check("poster cut: follow-up after the POST", p.followup is not None
          and p.followup["t_sent"] >= p.raw["t_end"], (p.followup and p.followup["t_sent"], p.raw["t_end"]))
    check("poster cut: status still 200 (status line was fine)", p.status == 200, p.status)

    # ModePoster, no reply at all: timeout, then the follow-up also times out.
    p, calls = run_poster("no_reply_open", "no_reply_open", timeout=1.0)
    check("poster silent: ending timeout", p.raw["ending"] == "timeout", p.raw["ending"])
    check("poster silent: status None", p.status is None)
    check("poster silent: follow-up recorded its own timeout",
          p.followup is not None and p.followup["ending"] == "timeout")

    # ModePoster never raises: an exception inside is recorded.
    real = RHP.raw_request

    def boom(*a, **k):
        raise ValueError("planted")
    E4.RHP.raw_request = boom
    try:
        p = E4.ModePoster(3)
        p.base = time.perf_counter()
        p.start()
        p.join(timeout=10)
    finally:
        E4.RHP.raw_request = real
    check("poster exception: recorded, thread ended", p.exc is not None and "planted" in p.exc
          and not p.is_alive(), p.exc)

    # The urllib path is unchanged when no raw timeout is set.
    E4.POST_RAW_TIMEOUT = None
    seen = []
    real_gi = E4.gi
    E4.gi = lambda path, method="GET", timeout=6.0: (seen.append((path, method)) or (200, "{}"))
    try:
        p = E4.ModePoster(3)
        p.base = time.perf_counter()
        p.run()
    finally:
        E4.gi = real_gi
        E4.POST_RAW_TIMEOUT = 3.0
    check("no raw timeout: urllib gi() POST as before",
          seen == [("/gen_inhibit_set?mode=3&offset=500", "POST")] and p.raw is None and p.status == 200, seen)

    # schedule_due
    class Alive:
        def __init__(self, a):
            self.a = a

        def is_alive(self):
            return self.a
    sched = [{"at_s": 20.0, "poster": Alive(True)}, {"at_s": 20.0, "poster": Alive(False)}]
    E4.POST_CHAIN = True
    check("chain: first due at its time", E4.schedule_due(sched, 0, 20.0) is True)
    check("chain: not before its time", E4.schedule_due(sched, 0, 19.99) is False)
    check("chain: second waits while the first is alive", E4.schedule_due(sched, 1, 20.5) is False)
    sched[0]["poster"].a = False
    check("chain: second starts once the first is done", E4.schedule_due(sched, 1, 20.5) is True)
    check("chain: past the end is never due", E4.schedule_due(sched, 2, 99.0) is False)
    sched[0]["poster"].a = True
    E4.POST_CHAIN = False
    check("no chain: second does not wait", E4.schedule_due(sched, 1, 20.5) is True)

    # parse_netstat
    txt = ("\nActive Connections\n\n  Proto  Local Address          Foreign Address        State\n"
           "  TCP    192.168.80.2:50001     198.51.100.1:80        ESTABLISHED\n"
           "  TCP    192.168.80.2:50002     198.51.100.10:80       ESTABLISHED\n"
           "  TCP    192.168.80.2:50003     198.51.100.1:80        TIME_WAIT\n"
           "  TCP    203.0.113.5:50004      203.0.113.4:8086         ESTABLISHED\n")
    rows = E4.parse_netstat(txt, "198.51.100.1")
    check("netstat: the device's two rows, not .10's", rows == [["50001", "198.51.100.1:80", "ESTABLISHED"],
                                                             ["50003", "198.51.100.1:80", "TIME_WAIT"]], rows)

    check_run_arm_wiring()

    # arm_post_fields (2026-10-16): the --arm-after POST's raw record is kept
    class FakePoster:
        t_start, t_end, status = 1.04, 3.07, 200
        raw = {"t_sent": 1.045, "t_complete": 1.06, "status_line": "HTTP/1.1 200 OK"}
        followup, exc = None, None
    f = E4.arm_post_fields(FakePoster(), 2235)
    check("arm_post_fields: the arm POST keeps its raw record, index, times and status",
          f["post_raw"] is FakePoster.raw and f["post_index"] == 2235 and f["post_s"] == [1.04, 3.07]
          and f["post_http"] == 200 and "post_followup" in f and "post_exception" in f, f)
    f0 = E4.arm_post_fields(None, 99)
    check("arm_post_fields: no poster gives all-None fields", all(v is None for v in f0.values())
          and set(f0) == set(f), f0)

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
