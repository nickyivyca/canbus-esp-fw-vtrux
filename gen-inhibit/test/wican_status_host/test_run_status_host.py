#!/usr/bin/env python3
"""Tests run_status_host.py, the status page's fw_version on the host (shared/wican W15,
the page half). Gen-inhibit tester, 2026-11-23.

1. The pinned vtrux/wican passes every case.
2. The can-fail: shared/main tag 2026.11.04's handler, from before W15, fails with
   fw_version 0.00 on the commit, the dirty and the full-field cases. Its stock_tag case,
   which the stock parse matches, is recorded as it comes out (on a 64-bit host its
   "%ld" into a uint32_t is a stack overflow, so ASan stops it: VOID).
3. Planted mutants of the pinned handler's text, one defect each, compiled and run: a
   renamed key, an empty value, a value cut to 8 characters, a leading v dropped, the
   stock parse put back, a second fw_version key. Each must fail the cases it shows in
   and pass the others.
4. Mutants of the judge: each must change a verdict on the records of 1-3.
5. The extraction: the handler is the one the "/check_status" registration names, past
   registrations in comments and for other URIs; none, or two, are refused; the
   file-scope data the handler reads gets stand-ins.

    python3.13 -B test_run_status_host.py [--wican DIR] [--prefix DIR] [--idf DIR]
--wican defaults to the pinned checkout, --prefix to ../shared-2026.11.04/shared/wican
beside the worktree root. Exit 0 every check passes. Compiles in scratch/, removed.
"""
import argparse
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_status_host as R   # noqa: E402

FAILS = []
PREFIX_TAG = "2026.11.04"
CASES = [c for c, _ in R.VERSIONS]


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:240]))
    if not ok:
        FAILS.append(label)


def verdicts(rows):
    return {case: ok for case, ok, _ in rows}


DECL = "\tconst char *fver = esp_app_get_description()->version;\n"
ADD = '\tcJSON_AddStringToObject(root, "fw_version", fver);\n'

# Each: (name, old, new, {case: expected verdict}, a word the failing details must hold).
MUTANTS = [
    ("key renamed", ADD, ADD.replace('"fw_version"', '"fw_ver"'),
     {c: False for c in CASES}, "no fw_version key"),
    ("empty value", DECL, '\tconst char *fver = "";\n', {c: False for c in CASES}, "empty"),
    ("cut to 8 characters", DECL,
     "\tstatic char fbuf[9];\n\tsnprintf(fbuf, sizeof fbuf, \"%s\", esp_app_get_description()->version);\n"
     "\tconst char *fver = fbuf;\n",
     {"commit": True, "dirty": False, "stock_tag": False, "field_full": False}, "a prefix"),
    ("leading v dropped", DECL, DECL + "\tif (fver[0] == 'v') fver++;\n",
     {"commit": True, "dirty": True, "stock_tag": False, "field_full": True}, "not the descriptor's version"),
    ("stock parse put back", DECL,
     "\tstatic char fbuf[16];\n\tunsigned int fmaj = 0, fmin = 0;\n"
     "\tsscanf(esp_app_get_description()->version, \"v%u.%u\", &fmaj, &fmin);\n"
     "\tsnprintf(fbuf, sizeof fbuf, \"%u.%02u\", fmaj, fmin);\n\tconst char *fver = fbuf;\n",
     {c: False for c in CASES}, "parsed"),
    ("a second fw_version key", ADD, ADD + '\tcJSON_AddStringToObject(root, "fw_version", "0.00");\n',
     {c: False for c in CASES}, "2 fw_version keys"),
]


def run_text(text, tag, idf):
    try:
        return R.run("<in memory>", idf, src_override=text,
                     work=os.path.join(R.WORKTREE, "scratch", "wican-status-host", "%d_%s" % (os.getpid(), tag)))
    except R.Refused as e:
        return None, [], "REFUSED %s" % e


def judge_module(replacements):
    """A copy of run_status_host with its source text changed, for a judge mutant."""
    with open(os.path.join(HERE, "run_status_host.py"), encoding="utf-8") as f:
        src = f.read()
    for old, new in replacements:
        if src.count(old) != 1:
            raise AssertionError("judge mutant text %r found %d times" % (old, src.count(old)))
        src = src.replace(old, new)
    spec = importlib.util.spec_from_loader("rsh_mutant", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = os.path.join(HERE, "run_status_host.py")
    exec(compile(src, "rsh_mutant", "exec"), mod.__dict__)
    return mod


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wican")
    ap.add_argument("--idf")
    ap.add_argument("--prefix", default=os.path.join(os.path.dirname(R.WORKTREE),
                                                     "shared-%s" % PREFIX_TAG, "shared", "wican"))
    a = ap.parse_args(argv)
    wican = R.wican_dir(a.wican)
    sets = {}                                     # label -> records, for the judge mutants

    # 1 -- pinned
    name, recs, rows = R.run(wican, a.idf)
    check("pinned: the handler is check_status_handler", name == "check_status_handler", name)
    check("pinned: every case passes", verdicts(rows) == {c: True for c in CASES}, rows)
    sets["pinned"] = recs

    # 2 -- the can-fail
    _, precs, prows = R.run(a.prefix, a.idf)
    pv = verdicts(prows)
    for c in ("commit", "dirty", "field_full"):
        d = [r[2] for r in prows if r[0] == c]
        check("pre-fix %s: %s fails with fw_version 0.00" % (PREFIX_TAG, c),
              pv.get(c) is False and d and "'0.00'" in d[0], d)
    st = [r for r in prows if r[0] == "stock_tag"]
    check("pre-fix: stock_tag does not pass (recorded: %s)" % (st[0][1] if st else None),
          bool(st) and st[0][1] is not True, st)
    sets["prefix"] = precs

    # 3 -- planted mutants of the pinned text
    with open(os.path.join(wican, "config_server.c"), encoding="utf-8", errors="replace") as f:
        text = f.read()
    for i, (mname, old, new, want, word) in enumerate(MUTANTS):
        if text.count(old) != 1:
            check("mutant %s: its text is in the handler once" % mname, False, "found %d" % text.count(old))
            continue
        _, mrecs, mrows = run_text(text.replace(old, new), "m%d" % i, a.idf)
        if isinstance(mrows, str):
            check("mutant %s: builds" % mname, False, mrows)
            continue
        mv = verdicts(mrows)
        check("mutant %s: verdicts %s" % (mname, want), mv == want, mrows)
        fdet = [d for _, ok, d in mrows if ok is False]
        check("mutant %s: the failures say %r" % (mname, word), fdet and all(word in d for d in fdet), fdet)
        sets["mutant " + mname] = mrecs

    # 4 -- judge mutants
    def killed(mod):
        for label, rs in sets.items():
            if verdicts(R.judge(copy.deepcopy(rs))) != verdicts(mod.judge(copy.deepcopy(rs))):
                return label
        return None

    jmutants = [
        ("a prefix accepted", [("    if v == want:\n", "    if want.startswith(v):\n")]),
        ("a missing key accepted", [('        return False, "no fw_version key"',
                                     '        return True, "no fw_version key"')]),
        ("a second key accepted", [("    if len(got) > 1:", "    if len(got) > 2:")]),
        ("a crash accepted", [('        return None, "the handler crashed', '        return True, "the handler crashed')]),
        ("only the commit case judged", [('    ("dirty", "0a740ce6-dirty"),', ""),
                                         ('    ("stock_tag", "v4.20-1234-g0a740ce6ab-dirty"),', ""),
                                         ('    ("field_full", "0a740ce6ab12cd34ef56ab78cd90ef1"),', "")]),
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
    planted = (
        "#define FW_KEY \"fw_version\"\n#define UNUSED_DEF 9\n"
        "static esp_err_t the_status(httpd_req_t *req)\n{\n"
        "    cJSON *root = cJSON_CreateObject(); /* { */\n"
        "    cJSON_AddStringToObject(root, FW_KEY, esp_app_get_description()->version);\n"
        "    cJSON_AddStringToObject(root, \"port\", device_config.port);\n"
        "    cJSON_AddStringToObject(root, \"id\", device_id);\n"
        "    char *s = cJSON_PrintUnformatted(root); httpd_resp_send(req, s, HTTPD_RESP_USE_STRLEN);\n"
        "    free(s); cJSON_Delete(root); return ESP_OK;\n}\n"
        "static esp_err_t decoy(httpd_req_t *req) { char c = '{'; return ESP_FAIL; }\n"
        "/* static const httpd_uri_t old = { .uri = \"/check_status\", .handler = decoy }; */\n"
        "static const httpd_uri_t other_uri = { .uri = \"/check_status_x\", .handler = decoy };\n"
        "static const httpd_uri_t status_uri = {\n    .uri = \"/check_status\",\n"
        "    // .handler = decoy,\n    .handler = the_status,\n};\n")
    try:
        hname, src = R.handler_source(planted)
        check("extract: the handler the registration names, past comments and other URIs",
              hname == "the_status", hname)
        check("extract: the #defines it names, not others",
              '#define FW_KEY "fw_version"' in src and "UNUSED_DEF" not in src, src[:300])
        check("extract: stand-ins for device_config.port and device_id, none for can_datarate_str",
              "const char *port;" in src and "device_id =" in src and "can_datarate_str" not in src, src[:600])
        check("extract: the decoy not taken", "decoy" not in src)
        _, prec, prow = run_text(planted, "planted", a.idf)
        check("extract: the planted handler builds and passes", verdicts(prow) == {c: True for c in CASES}, prow)
    except R.Refused as e:
        check("extract: planted handler", False, e)
    base = "static esp_err_t h(httpd_req_t *req) { return ESP_OK; }\n"
    for label, txt in [
            ("no registration", base),
            ("two registrations", base + 'static const httpd_uri_t a = { .uri = "/check_status", .handler = h };\n'
                                         'static const httpd_uri_t b = { .uri = "/check_status", .handler = h };\n'),
            ("a registration with no handler", base + 'static const httpd_uri_t a = { .uri = "/check_status" };\n'),
            ("a handler with no definition", 'static const httpd_uri_t a = { .uri = "/check_status", .handler = g };\n')]:
        try:
            R.handler_source(txt)
            check("extract: %s refused" % label, False)
        except R.Refused as e:
            check("extract: %s refused" % label, True, e)

    print("\n%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    for f in FAILS:
        print("  " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
