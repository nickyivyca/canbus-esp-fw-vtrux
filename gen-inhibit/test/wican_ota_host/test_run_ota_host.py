#!/usr/bin/env python3
"""Tests run_ota_host.py, the OTA handler's host build (gen-inhibit spec 9.2 items 4-6;
shared/wican W12-W14). Gen-inhibit tester, 2026-11-23.

1. The pinned vtrux/wican passes W12, W13 and W14 (the trailing-boundary stall's W13 row
   is recorded, never PASS or FAIL: the reviewer's ruling, 2026-11-23).
2. The can-fail: shared/main tag 2026.11.04's handler, from before W12-W14, fails all
   three, each on the cases its defect shows in: W12 on a reset and a close mid-body,
   W13 on a stall (21 timeouts), W14 on every case (OTA_SIZE_UNKNOWN).
3. Planted mutants of the pinned handler's text, one defect each, compiled and run: each
   must fail its own row and only the rows it should. One breaks the write pointer
   (spec 9.2 item 2), which the control must catch as VOID.
4. Mutants of the judge: each must turn a row of the pinned or the pre-fix run wrong.
5. The extraction: braces and the word esp_ota_begin in comments, strings and
   #defines are not read; none, or two, handler functions are refused; #defines are
   taken through the ones they name; helper functions, enums and typedefs the handler
   uses are taken through what they use, a helper defined after it with a prototype,
   and the build succeeds; a function the fake declares, or one nothing calls, is not.

    python3.13 -B test_run_ota_host.py [--wican DIR] [--prefix DIR]
--wican defaults to the pinned checkout, --prefix to ../shared-2026.11.04/shared/wican
beside the worktree root. Exit 0 every check passes. Compiles in scratch/, removed.
"""
import argparse
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_ota_host as R   # noqa: E402

FAILS = []
PREFIX_TAG = "2026.11.04"


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:200]))
    if not ok:
        FAILS.append(label)


def verdicts(rows):
    """{row: PASS/FAIL} over judged rows, and the set of (row, case) that failed."""
    out = {}
    failed = set()
    for row, cname, ok, _ in rows:
        if ok is None:
            continue
        if ok is False:
            failed.add((row, cname))
            out[row] = "FAIL"
        else:
            out.setdefault(row, "PASS")
    return out, failed


def run_text(text, tag):
    try:
        return R.run("<in memory>", src_override=text,
                     work=os.path.join(R.WORKTREE, "scratch", "wican-ota-host", "%d_%s" % (os.getpid(), tag)))
    except R.Refused as e:
        return None, [], [], "REFUSED %s" % e


# Each: (name, old, new, rows that must FAIL with a case they must fail on, or "VOID").
MUTANTS = [
    ("w14 size unknown", "esp_ota_begin(update_partition, OTA_WITH_SEQUENTIAL_WRITES,",
     "esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN,", {("W14", "complete")}),
    ("w12 no abort on a receive failure",
     "            esp_ota_abort(update_handle);\n            /* Respond with 500 Internal Server Error */\n"
     "            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, \"Failed to receive file\");",
     "            /* Respond with 500 Internal Server Error */\n"
     "            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, \"Failed to receive file\");",
     {("W12", "reset_mid"), ("W12", "close_mid")}),
    ("w12 no abort on a stall", "                    esp_ota_abort(update_handle);\n                    httpd_resp_send_err(req, HTTPD_408_REQ_TIMEOUT",
     "                    httpd_resp_send_err(req, HTTPD_408_REQ_TIMEOUT", {("W12", "stall_mid")}),
    ("w13 five timeouts", "#define OTA_RECV_MAX_TIMEOUTS  4", "#define OTA_RECV_MAX_TIMEOUTS  5",
     {("W13", "stall_mid")}),
    ("w13 three timeouts", "#define OTA_RECV_MAX_TIMEOUTS  4", "#define OTA_RECV_MAX_TIMEOUTS  3",
     {("W13", "timeouts3_mid"), ("W13", "stall_mid")}),
    ("w13 off by one", "if (++timeouts >= OTA_RECV_MAX_TIMEOUTS)", "if (++timeouts > OTA_RECV_MAX_TIMEOUTS)",
     {("W13", "stall_mid")}),
    ("w13 count never restarts", "        timeouts = 0;\n", "\n", {("W13", "timeouts3_x4"), ("W13", "timeouts2_x2")}),
    # The write pointer off the receive target (spec 9.2 item 2's kind of defect). Since
    # W16 (2026.11.23.2) the start is read before the loop and `buf` never moves, so
    # dropping the loop's reset, as this mutant did before, changes nothing there; one
    # byte off is a defect in both forms.
    ("9.2 item 2 write pointer off the receive target", "        buf = scratch;\n",
     "        buf = scratch + 1;\n", "VOID"),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican")
    ap.add_argument("--prefix", default=os.path.join(os.path.dirname(R.WORKTREE),
                                                     "shared-%s" % PREFIX_TAG, "shared", "wican"))
    a = ap.parse_args(argv)
    wican = R.wican_dir(a.wican)

    # 1 -- pinned
    name, recs, rows, void = R.run(wican)
    v, failed = verdicts(rows)
    check("pinned: not void", void is None, void)
    check("pinned: W12 W13 W14 pass", v == {"W12": "PASS", "W13": "PASS", "W14": "PASS"}, (v, sorted(failed)))
    check("pinned: the trailing-boundary stall's W13 row is recorded, not judged",
          any(r[1] == "stall_at_trailer" and r[0] == "W13" and r[2] is None and "500" in r[3] for r in rows))
    pinned_rows = rows

    # 2 -- the can-fail
    _, _, prows, pvoid = R.run(a.prefix)
    pv, pfailed = verdicts(prows)
    check("pre-fix %s: not void" % PREFIX_TAG, pvoid is None, pvoid)
    check("pre-fix: W12, W13, W14 all fail", pv == {"W12": "FAIL", "W13": "FAIL", "W14": "FAIL"}, pv)
    for want in [("W12", "reset_mid"), ("W12", "close_mid"), ("W12", "invalid_mid"), ("W13", "stall_mid"),
                 ("W13", "stall_first"), ("W14", "complete"), ("W14", "stall_mid")]:
        check("pre-fix fails %s %s" % want, want in pfailed)
    check("pre-fix still passes what it did right (W12 write_fails, W13 timeouts3_x4)",
          ("W12", "write_fails") not in pfailed and ("W13", "timeouts3_x4") not in pfailed)

    # 3 -- planted mutants of the pinned text
    with open(os.path.join(wican, "config_server.c"), encoding="utf-8", errors="replace") as f:
        text = f.read()
    for i, (mname, old, new, want) in enumerate(MUTANTS):
        if text.count(old) != 1:
            check("mutant %s: its text is in the handler once" % mname, False, "found %d" % text.count(old))
            continue
        _, _, mrows, mvoid = run_text(text.replace(old, new), "m%d" % i)
        if want == "VOID":
            # VOID by the control, or (since the receive buffer is poisoned past what was
            # received, 2026-11-23) by its read of bytes no receive delivered.
            check("mutant %s: VOID by the control or a read past the received bytes" % mname,
                  bool(mvoid) and ("control" in mvoid or "read past the received bytes" in mvoid), mvoid)
            continue
        mv, mfailed = verdicts(mrows)
        check("mutant %s: not void" % mname, mvoid is None, mvoid)
        check("mutant %s: fails %s" % (mname, sorted(want)), want <= mfailed, sorted(mfailed))
        others = {row for row, _ in mfailed} - {row for row, _ in want}
        check("mutant %s: fails no other requirement" % mname, not others, sorted(others))

    # 4 -- judge mutants: the records of the pinned run, judged wrongly, must change a verdict
    good = [r for r in recs if not r.get("crashed")]
    jm = []

    def jmutant(label, fn):
        saved = {k: getattr(R, k) for k in ("DROP_AT", "SEQ", "STALL_CASES", "W12_CASES", "SPACED_CASES")}
        try:
            fn()
            rows_m, void_m = R.judge(copy.deepcopy(good))
            rows_p, void_p = R.judge(copy.deepcopy([r for r in R_prefix_recs]))
        finally:
            for k, val in saved.items():
                setattr(R, k, val)
        base = [(r[0], r[1], r[2]) for r in pinned_rows]
        got = [(r[0], r[1], r[2]) for r in rows_m]
        basep = [(r[0], r[1], r[2]) for r in prows]
        gotp = [(r[0], r[1], r[2]) for r in rows_p]
        check("judge mutant %s: a verdict changes" % label, base != got or basep != gotp or void_m or void_p)
        jm.append(label)

    global R_prefix_recs
    _, R_prefix_recs, _, _ = R.run(a.prefix)
    jmutant("drop at 21", lambda: setattr(R, "DROP_AT", 21))
    jmutant("drop at 5", lambda: setattr(R, "DROP_AT", 5))
    jmutant("SEQ is SIZE_UNKNOWN", lambda: setattr(R, "SEQ", R.UNKNOWN))
    jmutant("no stall cases", lambda: setattr(R, "STALL_CASES", ()))
    jmutant("W12 skips mid-body reset and close",
            lambda: setattr(R, "W12_CASES", tuple(c for c in R.W12_CASES if c not in ("reset_mid", "close_mid",
                                                                                       "invalid_mid", "reset_first",
                                                                                       "close_after_preamble"))))
    # the control itself: a complete upload that did not restart must VOID
    bad = copy.deepcopy(good)
    for r in bad:
        if r["case"] == "complete":
            r["written_ok"] = False
    _, bvoid = R.judge(bad)
    check("judge: a control that did not write the image is VOID", bool(bvoid), bvoid)
    _, mvoid = R.judge([r for r in good if r["case"] != "stall_mid"])
    check("judge: a missing case is VOID", bool(mvoid) and "stall_mid" in mvoid, mvoid)

    # 5 -- extraction
    planted = (
        '#define OTA_RECV_MAX_TIMEOUTS  4\n#define DEP 7\n#define USES_DEP (DEP + 1)\n#define LBRACE {\n'
        'struct file_server_data { char base_path[16]; char scratch[4096]; };\n'
        '/* esp_ota_begin( in a comment { */\n'
        'static const char *s = "esp_ota_begin( {";\n'
        'static esp_err_t other(httpd_req_t *req) { char c = \'{\'; return ESP_OK; }\n'
        'static esp_err_t the_handler(httpd_req_t *req, bool *ok)\n{\n'
        '    int x = USES_DEP; (void)x; esp_ota_handle_t h; if (0) esp_ota_begin(0, 0, &h);\n'
        '    return OTA_RECV_MAX_TIMEOUTS;\n}\n')
    try:
        hname, src = R.handler_source(planted)
        check("extract: the one handler, past braces in comments, strings, chars and #defines",
              hname == "the_handler", hname)
        check("extract: #defines taken through the ones they name",
              "#define DEP 7" in src and "#define USES_DEP" in src and "LBRACE" not in src, src[:300])
        check("extract: the struct taken", "struct file_server_data { char base_path" in src)
    except R.Refused as e:
        check("extract: planted handler", False, e)
    # The file-scope items the handler needs, followed through (2026-11-23, for W16's
    # multipart_start_read): a helper defined after the handler, the typedef'd enum it
    # returns, a helper that helper calls; not a function the fake declares, nor one
    # nothing calls.
    helpers = (
        'struct file_server_data { char base_path[16]; char scratch[4096]; };\n'
        'typedef enum { ST_OK = 0, ST_BAD } st_t;\n'
        'static int inner(int v) { return v + 1; }\n'
        'const char *get_path_from_uri(char *d, const char *b, const char *u, size_t n) { return 0; }\n'
        'static int unused_fn(void) { return 3; }\n'
        'static st_t step(httpd_req_t *req);\n'
        'static esp_err_t the_handler(httpd_req_t *req, bool *ok)\n{\n'
        '    esp_ota_handle_t h; esp_ota_begin(esp_ota_get_next_update_partition(0), '
        'OTA_WITH_SEQUENTIAL_WRITES, &h);\n'
        '    if (step(req) != ST_OK) { esp_ota_abort(h); return ESP_FAIL; }\n'
        '    esp_ota_abort(h); return ESP_OK;\n}\n'
        'static st_t step(httpd_req_t *req) { return inner(0) ? ST_OK : ST_BAD; }\n')
    try:
        hname, src = R.handler_source(helpers)
        check("extract: a helper defined after the handler, with a prototype before it",
              "static st_t step(httpd_req_t *req);" in src and "return inner(0) ? ST_OK : ST_BAD;" in src, src[:600])
        check("extract: the typedef'd enum and the helper's helper taken",
              "typedef enum { ST_OK = 0, ST_BAD } st_t;" in src and "static int inner(int v)" in src)
        check("extract: a function the fake declares not taken, nor one nothing calls",
              "return 0; }" not in src.split("get_path_from_uri", 1)[-1][:80] and "unused_fn" not in src, src[:600])
        try:
            R.run("<in memory>", src_override=helpers,
                  work=os.path.join(R.WORKTREE, "scratch", "wican-ota-host", "%d_helpers" % os.getpid()))
            check("extract: the handler with its helpers builds", True)
        except R.Refused as e:
            check("extract: the handler with its helpers builds", False, e)
    except R.Refused as e:
        check("extract: handler with helpers", False, e)
    for label, txt in [("none", "static esp_err_t f(httpd_req_t *r) { return 0; }\n"),
                       ("two", "static esp_err_t f(httpd_req_t *r) { esp_ota_begin(0,0,0); return 0; }\n"
                               "static esp_err_t g(httpd_req_t *r) { esp_ota_begin(0,0,0); return 0; }\n")]:
        try:
            R.handler_source(txt)
            check("extract: %s handler refused" % label, False)
        except R.Refused as e:
            check("extract: %s handler refused" % label, True, e)

    print("\n%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    for f in FAILS:
        print("  " + f)
    return 1 if FAILS else 0


R_prefix_recs = []

if __name__ == "__main__":
    sys.exit(main())
