#!/usr/bin/env python3
"""Tests run_upload_host.py, the multipart upload handlers on the host (shared/wican W16).
Gen-inhibit tester, 2026-11-23, stage (b) batch 2.

1. shared/main tag 2026.11.23.2, W16's code, passes every row.
2. The can-fail: tag 2026.11.23, from before W16, passes no W16 row; each malformed start
   that the stock code reads past is a FAIL named "read past the received bytes".
3. Planted defects in 2026.11.23.2's text, one each, compiled and run: refusing after the
   first short read; no blank-line check; the stock unbounded memchr put back; reading past
   the body's end; the OTA refusal not ending its session; car_data.json's refusal answered
   500; the file truncated on a refusal; one byte written on an OTA refusal; the data
   offset one short. Each must fail the rows it shows in and no other (the last turns the
   control VOID).
4. Mutants of the judge: each must change a verdict on the records of 1-3.
5. The extraction: the car_data handler is the one the "/upload/car_data.json"
   registration names, past a registration in a comment and one for another URI; none or
   two are refused.

    python3.13 -B test_run_upload_host.py [--wican DIR] [--prefix DIR]
--wican defaults to ../shared-2026.11.23.2/shared/wican beside the worktree root, --prefix
to ../shared-2026.11.23/shared/wican. Exit 0 every check passes. Builds in scratch/, removed.
"""
import argparse
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_upload_host as U   # noqa: E402

FAILS = []
TARGET_TAG = "2026.11.23.2"
PREFIX_TAG = "2026.11.23"
GOOD, BAD = U.GOOD, U.BAD


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:260]))
    if not ok:
        FAILS.append(label)


def failed(rows):
    """{(row, handler, case)} of the failing rows."""
    return {(r[0], r[1], r[2]) for r in rows if r[3] is False}


def both(row, cases):
    return {(row, h, c) for h in U.HANDLERS for c in cases}


SPLITS = ("split_in_boundary", "split_after_boundary", "split_in_type", "split_before_blank",
          "split_in_blank", "trickle_1", "trickle_3")
MALFORMED_OTA = "ESP_LOGE(TAG, \"OTA refused: malformed multipart start\");\n"
MALFORMED_CAR = "ESP_LOGE(TAG, \"Upload refused: malformed multipart start\");\n"

# (name, old, new, the failing rows expected exactly, or "VOID")
MUTANTS = [
    ("refused after the first short read", "        if (have >= cap || have >= body)\n",
     "        if (have > 0 || have >= cap || have >= body)\n", both("W16", SPLITS)),
    ("no blank-line check", "            if (nl2[1] != '\\r' || nl2[2] != '\\n')\n",
     "            if (0)\n", both("W16", ("no_blank",))),
    ("the end of the body not a stop", "        if (have >= cap || have >= body)\n",
     "        if (have >= cap)\n", both("W16", ("no_line", "ends_early"))),
    ("the OTA refusal leaves its session open",
     MALFORMED_OTA + "        esp_ota_abort(update_handle);\n", MALFORMED_OTA,
     {("W12", "ota", c) for c in BAD}),
    ("car_data's refusal answered 500",
     MALFORMED_CAR + "        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,",
     MALFORMED_CAR + "        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,",
     {("W16", "car", c) for c in BAD}),
    ("the file truncated on a refusal", MALFORMED_CAR,
     MALFORMED_CAR + "        fclose(fopen(filepath, \"w\"));\n", {("W16", "car", c) for c in BAD}),
    ("a byte written on an OTA refusal", MALFORMED_OTA,
     MALFORMED_OTA + "        esp_ota_write(update_handle, scratch, 1);\n", {("W16", "ota", c) for c in BAD}),
    ("the data offset one short", "            *data_off = (nl2 + 3) - buf;\n",
     "            *data_off = (nl2 + 2) - buf;\n", "VOID"),
]
# The stock search put back: the first line's end looked for over 200 bytes, whatever was
# received. Its failures are judged by kind, not by an exact set: each must be a read past
# the received bytes, and the cases whose first receives are under 200 bytes must be among
# them.
UNBOUNDED = ("the stock 200-byte memchr put back", "        const char *nl1 = memchr(buf, '\\n', have);\n",
             "        const char *nl1 = memchr(buf, '\\n', 200);\n",
             both("W16", ("split_in_boundary", "trickle_1", "trickle_3", "no_line", "long_start_trickle")))


def run_text(text, tag):
    try:
        return U.run("<in memory>", src_override=text,
                     work=os.path.join(U.WORKTREE, "scratch", "wican-upload-host", "%d_%s" % (os.getpid(), tag)))
    except U.Refused as e:
        return None, [], [], "REFUSED %s" % e


def judge_module(replacements):
    with open(os.path.join(HERE, "run_upload_host.py"), encoding="utf-8") as f:
        src = f.read()
    for old, new in replacements:
        if src.count(old) != 1:
            raise AssertionError("judge mutant text %r found %d times" % (old, src.count(old)))
        src = src.replace(old, new)
    spec = importlib.util.spec_from_loader("ruh_mutant", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = os.path.join(HERE, "run_upload_host.py")
    exec(compile(src, "ruh_mutant", "exec"), mod.__dict__)
    return mod


def main(argv=None):
    root = os.path.dirname(U.WORKTREE)
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican", default=os.path.join(root, "shared-%s" % TARGET_TAG, "shared", "wican"))
    ap.add_argument("--prefix", default=os.path.join(root, "shared-%s" % PREFIX_TAG, "shared", "wican"))
    a = ap.parse_args(argv)
    sets = {}

    # 1 -- W16's code
    names, recs, rows, void = U.run(a.wican)
    check("%s: the handlers" % TARGET_TAG, names == ("upload_post_body", "upload_car_data_handler"), names)
    check("%s: not void" % TARGET_TAG, void is None, void)
    check("%s: every row passes" % TARGET_TAG, rows and not failed(rows), sorted(failed(rows)))
    check("%s: 38 rows (2 handlers x 13 cases, W12 for the OTA's 6 refusals, 6 recorded)" % TARGET_TAG,
          len(rows) == 38, len(rows))
    sets["target"] = recs

    # 2 -- the can-fail
    _, precs, prows, pvoid = U.run(a.prefix)
    w16 = [r for r in prows if r[0] == "W16"]
    check("%s: no W16 row passes" % PREFIX_TAG, w16 and not [r for r in w16 if r[3]], [r[:3] for r in w16 if r[3]])
    check("%s: every case judged (no VOID)" % PREFIX_TAG, pvoid is None, pvoid)
    for h in U.HANDLERS:
        for c in ("no_line", "no_type", "ends_early", "long_start"):
            d = [r[4] for r in prows if r[:3] == ("W16", h, c)]
            check("%s %s/%s: read past the received bytes" % (PREFIX_TAG, h, c),
                  d and "read past the received bytes" in d[0], d)
    sets["prefix"] = precs

    # 3 -- planted defects
    with open(os.path.join(a.wican, "config_server.c"), encoding="utf-8", errors="replace") as f:
        text = f.read()
    for i, (mname, old, new, want) in enumerate(MUTANTS + [UNBOUNDED]):
        if text.count(old) != 1:
            check("mutant %s: its text is in the file once" % mname, False, "found %d" % text.count(old))
            continue
        _, mrecs, mrows, mvoid = run_text(text.replace(old, new), "m%d" % i)
        if isinstance(mvoid, str) and mvoid.startswith("REFUSED"):
            check("mutant %s: builds" % mname, False, mvoid)
            continue
        sets["mutant " + mname] = mrecs
        if want == "VOID":
            check("mutant %s: VOID by the control" % mname, bool(mvoid) and "control" in mvoid, mvoid)
            continue
        check("mutant %s: not void" % mname, mvoid is None, mvoid)
        got = failed(mrows)
        if (mname, old, new, want) == UNBOUNDED:
            check("mutant %s: fails at least %d rows" % (mname, len(want)), want <= got, sorted(want - got))
            dets = [r[4] for r in mrows if r[3] is False]
            check("mutant %s: each failure a read past the received bytes" % mname,
                  dets and all("read past the received bytes" in d for d in dets), dets[:3])
        else:
            check("mutant %s: fails exactly the rows expected" % mname, got == want,
                  "missing %s; extra %s" % (sorted(want - got), sorted(got - want)))

    # 4 -- judge mutants
    def verdicts(mod, rs):
        rows_, void_ = mod.judge(copy.deepcopy(rs))
        return sorted((r[:3], r[3]) for r in rows_), void_

    def killed(mod):
        for label, rs in sets.items():
            if verdicts(U, rs) != verdicts(mod, rs):
                return label
        return None

    jmutants = [
        ("any refusal code taken for 400", [('rows.append(("W16", h, cname, r["replies"] == ["400"] and nothing and '
                                             'not r["restart"],',
                                             'rows.append(("W16", h, cname, bool(r["replies"]) and nothing and '
                                             'not r["restart"],')]),
        ("a read past the received bytes taken as VOID", [('                if r["read_past"]:', '                if False:')]),
        ("car_data: nothing written not judged", [('nothing = r["fopens"] == 0 and r["fwrites"] == 0',
                                                    'nothing = True')]),
        ("OTA: nothing written not judged", [('nothing = r["writes"] == 0', 'nothing = True')]),
        ("car_data: an accepted upload's bytes not compared", [('(r["file_ok"] and r["replies"] == ["200"])\n',
                                                                 '(r["replies"] == ["200"])\n')]),
        ("W12 not judged", [('rows.append(("W12", h, cname, r["open_at_return"] == 0 and not r["restart"],',
                             'rows.append(("W12", h, cname, True,')]),
    ]
    for label, reps in jmutants:
        try:
            mod = judge_module(reps)
        except AssertionError as e:
            check("judge mutant %s: applies" % label, False, e)
            continue
        k = killed(mod)
        check("judge mutant %s: a verdict changes" % label, k is not None, k)

    # 5 -- extraction
    reg = ('static const httpd_uri_t %s = {\n    .uri = "%s",\n    .handler = %s,\n};\n')
    base = "static esp_err_t car(httpd_req_t *req) { return ESP_OK; }\n" \
           "static esp_err_t other(httpd_req_t *req) { return ESP_OK; }\n"
    planted = (base + "/* " + reg % ("old", U.CAR_URI, "other") + " */\n"
               + reg % ("x", U.CAR_URI + "x", "other") + reg % ("y", U.CAR_URI, "car"))
    try:
        check("extract: the registration's handler, past a comment and another URI",
              U.registered_handler(planted, U.CAR_URI) == "car", U.registered_handler(planted, U.CAR_URI))
    except U.Refused as e:
        check("extract: planted registration", False, e)
    for label, txt in [("no registration", base),
                       ("two registrations", base + reg % ("a", U.CAR_URI, "car") + reg % ("b", U.CAR_URI, "car"))]:
        try:
            U.registered_handler(txt, U.CAR_URI)
            check("extract: %s refused" % label, False)
        except U.Refused as e:
            check("extract: %s refused" % label, True, e)

    print("\n%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    for f in FAILS:
        print("  " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
