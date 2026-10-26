"""Harness test for test_only_inert.py's judgements (no bus, no device).

    py -3.14 gen-inhibit/tools/bench/test_test_only_inert.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_only_inert as T                                        # noqa: E402


def a(t, up, rr="SW", b="63eb50f"):
    return {"t": t, "uptime_ms": up, "reset_reason": rr, "build": b}


class FakeLF:
    HOST = "fake"

    @staticmethod
    def page():
        return {"uptime_ms": 1234, "reset_reason": "SW", "mode": 0, "elf_sha256": "ab" * 32}


def alive_run():
    real = T.lf
    T.lf = lambda: FakeLF
    try:
        a = T.alive()
    finally:
        T.lf = real
    return a["build"], a["uptime_ms"]


def probe_json_run(body='{"probe":"gi_ecc"}'):
    calls = []
    real = T.http

    def fake(method, path, timeout=60.0):
        calls.append((method, path))
        return 200, body
    T.http = fake
    try:
        out = T.probe_json((("GET", "/gi_ecc"), ("POST", "/never")))
    finally:
        T.http = real
    return calls, out


def main():
    triggers = T.load_triggers()
    crash_obs = triggers["GI_TEST_RESTART_QUIESCE"]["observe"]
    tx_obs = triggers["GI_INSTRUMENT_TXABORT"]["observe"]
    rxq_obs = triggers["GI_INSTRUMENT_RXQ"]["observe"]
    rb_obs = triggers["OTA_HEALTH_FAULT_INJECT"]["observe"]
    inert_crash = {"reset": False, "http_status": {"GET": 404, "POST": 404}, "kept_running": True}
    checks = [
        ("kept running: uptime advanced by the elapsed time, same reason and build",
         T.kept_running(a(100.0, 50000), a(110.0, 60000)) is True),
        ("kept running: an uptime drop (a reset) is not continuity",
         T.kept_running(a(100.0, 50000), a(110.0, 3000)) is False),
        ("kept running: uptime that advanced too little is not continuity (a reset and a fast reboot)",
         T.kept_running(a(100.0, 50000), a(130.0, 52000)) is False),
        ("kept running: uptime that advanced too much is not continuity",
         T.kept_running(a(100.0, 50000), a(101.0, 60000)) is False),
        ("kept running: a changed reset reason is not continuity",
         T.kept_running(a(100.0, 50000), a(110.0, 60000, rr="PANIC")) is False),
        ("kept running: a changed build is not continuity",
         T.kept_running(a(100.0, 50000), a(110.0, 60000, b="dace947")) is False),
        ("kept running: a missing reading is not continuity",
         T.kept_running(a(100.0, 50000), a(110.0, None)) is False and T.kept_running(None, a(1, 1)) is False),
        ("tagged: AB 7E and AB 7F in bytes 2-3, nothing else",
         len(T.tagged([(0, 1, b"\x01\x00\xab\x7e"), (0, 1, b"\x01\x00\xab\x7f\x00"), (0, 1, b"\xab\x7e\x00\x00"),
                       (0, 1, b"\x00\x00\xab"), (0, 1, b"\x00\x00\xab\x7d")])) == 2),
        ("POST status: 'ok' is 200, an HTTPError repr its code, no answer None",
         [T.post_status(x) for x in ("ok", "<HTTPError 404: 'Not Found'>", "HTTP Error 500: x",
                                     "TimeoutError()", None)] == [200, 404, 500, None, None]),
        ("absent: crash probe on an inert image passes",
         T.judge("absent", crash_obs, inert_crash)[0] is True),
        ("absent: a reset fails", T.judge("absent", crash_obs, dict(inert_crash, reset=True))[0] is False),
        ("absent: the GET answering 200 fails",
         T.judge("absent", crash_obs, dict(inert_crash, http_status={"GET": 200, "POST": 404}))[0] is False),
        ("absent: a 404 with the device not kept running fails (reviewer's condition)",
         T.judge("absent", crash_obs, dict(inert_crash, kept_running=False))[0] is False),
        ("absent: an undetermined reset fails, never passes",
         T.judge("absent", crash_obs, dict(inert_crash, reset=None))[0] is False),
        ("present: a reset passes", T.judge("present", crash_obs, dict(inert_crash, reset=True))[0] is True),
        ("present: nothing reached fails (the probe reaches nothing)",
         T.judge("present", crash_obs, inert_crash)[0] is False),
        ("txabort absent: 404 and no tagged frame, witness listening",
         T.judge("absent", tx_obs, {"http_status": {"/x": 404}, "tagged_frames": 0, "witness_ok": True,
                                    "kept_running": True})[0] is True),
        ("txabort absent: a deaf witness is undetermined, not a pass",
         T.judge("absent", tx_obs, {"http_status": {"/x": 404}, "tagged_frames": 0, "witness_ok": False,
                                    "kept_running": True})[0] is False),
        ("txabort absent: a tagged frame fails",
         T.judge("absent", tx_obs, {"http_status": {"/x": 404}, "tagged_frames": 1, "witness_ok": True,
                                    "kept_running": True})[0] is False),
        ("txabort present: 200 with tagged frames passes",
         T.judge("present", tx_obs, {"http_status": {"/x": 200}, "tagged_frames": 4, "witness_ok": True})[0] is True),
        ("page key absent: live and no key passes",
         T.judge("absent", rxq_obs, {"live": True, "key_present": False, "kept_running": True})[0] is True),
        ("page key: not live is undetermined (the state was not reached)",
         T.judge("absent", rxq_obs, {"live": False, "key_present": False, "kept_running": True})[0] is False),
        ("page key present: the key fails absent, passes present",
         T.judge("absent", rxq_obs, {"live": True, "key_present": True, "kept_running": True})[0] is False
         and T.judge("present", rxq_obs, {"live": True, "key_present": True})[0] is True),
        ("rollback absent: same build, uptime continuous",
         T.judge("absent", rb_obs, {"build_before": "63eb50f", "build_after": "63eb50f",
                                    "uptime_continuous": True, "kept_running": True})[0] is True),
        ("rollback: a changed build is a rollback",
         T.judge("absent", rb_obs, {"build_before": "63eb50f", "build_after": "dace947",
                                    "uptime_continuous": False, "kept_running": False})[0] is False),
        ("rollback: same build but a reset counts as the effect",
         T.judge("absent", rb_obs, {"build_before": "63eb50f", "build_after": "63eb50f",
                                    "uptime_continuous": False, "kept_running": True})[0] is False),
        ("page key: found nested (abortwin inside the scheduler object) and at the top level",
         T.page_has_key('{"mode":3,"sched":{"abortwin":{"reached":0}}}', "abortwin")
         and T.page_has_key('{"rxq":{"samples":4},"mode":3}', "rxq")),
        ("page key: a key that merely starts with the name, or the name as a value, does not match",
         not T.page_has_key('{"rxq_max":4,"note":"rxq"}', "rxq") and not T.page_has_key("", "rxq")
         and not T.page_has_key(None, "rxq")),
        # The witness's positive control (2026-10-16): both PCAN markers seen.
        ("witness: both markers seen is listening",
         T.witness_listening([(1.0, T.MARKER_ID, T.MARKERS[0]), (5.0, T.MARKER_ID, T.MARKERS[1])])),
        ("witness: only the marker before the probe is not listening across it",
         not T.witness_listening([(1.0, T.MARKER_ID, T.MARKERS[0])])),
        ("witness: other frames (the X2's diag) without the markers are not the control",
         not T.witness_listening([(1.0, 0x7F4, T.MARKERS[0]), (2.0, 0x7F5, T.MARKERS[1])])),
        ("witness: the marker id with another payload is not a marker",
         not T.witness_listening([(1.0, T.MARKER_ID, b"\x00" * 8), (5.0, T.MARKER_ID, T.MARKERS[1])])),
        ("witness: a marker is not a trial-tagged frame",
         T.tagged([(1.0, T.MARKER_ID, m) for m in T.MARKERS]) == []),
        # Why an arm did not go live (reviewer, 2026-10-17): the reason fields are recorded.
        ("page key record: a not-live page keeps its arm_block and abort_reason",
         lambda: (lambda r: r["live"] is False and r["mode"] == 3 and r["reasons"]["arm_block"] == "gate not yet evaluated"
          and r["reasons"]["abort_reason"] == "error-frame rate exceeded" and r["reasons"]["err_n"] == 2)(
             T.page_key_result(200, '{"mode":3,"inhibit_live":false,"arm_block":"gate not yet evaluated",'
                                    '"abort_reason":"error-frame rate exceeded","err_n":2}', "rxq"))),
        ("page key record: every reason field is a key, None where the page lacks it",
         lambda: (lambda r: set(r["reasons"]) == set(T.REASON_FIELDS) and r["reasons"]["worker"] is None
          and {"arm_block", "abort_reason"} <= set(T.REASON_FIELDS))(
             T.page_key_result(200, '{"mode":3,"inhibit_live":true}', "rxq"))),
        ("page key record: live and a nested key are read as before",
         lambda: (lambda r: r["live"] is True and r["key_present"] is True and r["key_top_level"] is False)(
             T.page_key_result(200, '{"mode":3,"inhibit_live":true,"sched":{"abortwin":{}}}', "abortwin"))),
        ("page key record: no page reads not live with every reason None, and does not raise",
         lambda: all((lambda r: r["live"] is False and all(v is None for v in r["reasons"].values()))(
             T.page_key_result(None, b, "rxq")) for b in (None, "", "<html>", "[1,2]"))),
        ("spacing: none before the first switch, --gap-s before each later one",
         lambda: [T.wait_before(k, 30.0) for k in range(3)] == [0.0, 30.0, 30.0]
         and [T.wait_before(k, 0.0) for k in range(2)] == [0.0, 0.0]),
        ("every trigger in the table has an observable this rig can judge",
         all(all(T.effect_present(o, {}) is None and (o in ("http_status", "reset", "tagged_frames", "rollback")
                                                      or o.startswith("page_key:") or o.startswith("probe_json:"))
                 for o in t["observe"]) for t in triggers.values())),
        # probe_json (GI_INSTRUMENT_ECC, 2026-10-20): the body must name the probe.
        ("probe_json: an answer naming the probe is the effect",
         T.effect_present("probe_json:gi_ecc", {"probe_json": {"/gi_ecc": "gi_ecc"}}) is True),
        ("probe_json: an answer naming another probe, or none, is not",
         T.effect_present("probe_json:gi_ecc", {"probe_json": {"/gi_ecc": "gi_txabort"}}) is False
         and T.effect_present("probe_json:gi_ecc", {"probe_json": {"/gi_ecc": ""}}) is False),
        ("probe_json: no answer is undetermined, never a pass",
         T.effect_present("probe_json:gi_ecc", {"probe_json": {"/gi_ecc": None}}) is None
         and T.effect_present("probe_json:gi_ecc", {"probe_json": {}}) is None),
        ("probe_answer: the probe field of a JSON object",
         lambda: T.probe_answer(200, ' {"probe":"gi_ecc","isr":[]}') == "gi_ecc"),
        ("probe_answer: a 200 that is not a JSON object naming a probe reads as absent, not as the instrument",
         lambda: [T.probe_answer(200, b) for b in ("<html>ok</html>", "[1,2]", '{"mode":0}', "")] == [""] * 4),
        ("probe_answer: a 404 is absent, no answer is undetermined",
         lambda: T.probe_answer(404, "") == "" and T.probe_answer(None, "URLError") is None),
        ("absent on the product image: no probe named, kept running, passes",
         T.judge("absent", ("probe_json:gi_ecc",), {"probe_json": {"/gi_ecc": ""}, "kept_running": True})[0] is True),
        ("present on the _ecc image passes; absent there fails",
         T.judge("present", ("probe_json:gi_ecc",), {"probe_json": {"/gi_ecc": "gi_ecc"}})[0] is True
         and T.judge("absent", ("probe_json:gi_ecc",), {"probe_json": {"/gi_ecc": "gi_ecc"},
                                                       "kept_running": True})[0] is False),
        ("the probe GETs only the table's GET paths and records both readings",
         lambda: (lambda calls, out: calls == [("GET", "/gi_ecc")] and out["http_status"] == {"/gi_ecc": 200}
                  and out["probe_json"] == {"/gi_ecc": "gi_ecc"})(*probe_json_run())),
        ("the probe records what the answer named: a page naming no probe is recorded as absent",
         lambda: probe_json_run('{"mode":0,"inhibit_live":false}')[1]["probe_json"] == {"/gi_ecc": ""}),
        # 2026-10-20: the build comes from /gen_inhibit's elf_sha256, never /check_status.
        ("alive reads the build from /gen_inhibit, without build_id",
         lambda: alive_run() == ("ab" * 32, 1234)),
        ("nothing in the rig calls build_id or /check_status",
         "build_id(" not in open(T.__file__, encoding="utf-8").read()
         and '"/check_status"' not in open(T.__file__, encoding="utf-8").read()),
    ]
    fails = []
    for label, ok in checks:
        if callable(ok):      # evaluated one at a time: an exception is that check's FAIL
            try:
                ok = bool(ok())
            except Exception as e:
                print("       %s raised %r" % (label[:40], e))
                ok = False
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
