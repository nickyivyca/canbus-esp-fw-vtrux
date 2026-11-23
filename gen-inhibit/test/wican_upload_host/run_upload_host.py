#!/usr/bin/env python3
"""The multipart upload handlers on the host: shared/wican W16, with W12 for the OTA
upload's refusal. Gen-inhibit tester, 2026-11-23, stage (b) batch 2.

W16 (shared/main 829ef4b6): "Both multipart upload handlers, the OTA upload and
/upload/car_data.json, look for the first boundary line, the part's Content-Type header
and the blank line after it only within the bytes received so far. The handler keeps
reading into its buffer until it finds them, the buffer is full or the body ends, and only
then refuses a body whose start lacks any of them, with 400; nothing is written. For the
OTA upload that is a failed exit under W12."

It takes both handlers out of vtrux/wican's config_server.c as they are: the OTA handler
as ../wican_ota_host/run_ota_host.py finds it (the one function that calls
esp_ota_begin()), and the handler the httpd_uri_t for "/upload/car_data.json" names, with
the file-scope items they need (run_ota_host.closure). It compiles them on the host under
ASan and UBSan against ../wican_ota_host/fake_idf.c and fake_file.c. The fake poisons the
receive buffer past the bytes each receive delivered, so a read of a byte not received
stops the case with ASan's use-after-poison: W16's "only within the bytes received".
upload_cases.c runs each case in its own process, for each handler:

  complete                      the control: the image arrives byte for byte, or VOID
  split_* and trickle_*         a well-formed start split across receives (5 places), or
                                1 or 3 bytes per receive: accepted, every byte kept
  no_line, no_type, no_blank    a start lacking the boundary line, the Content-Type header
                                or the blank line: 400, nothing written
  ends_early                    the body ends inside the start: 400, nothing written
  long_start(_trickle)          a start that does not end before the buffer fills: 400,
                                nothing written

Nothing written: for the OTA upload no esp_ota_write(), and (W12) no session open at
return and no restart; for car_data.json no fopen() and no fwrite(). A removal (unlink)
is recorded, not judged. A case that reads past the received bytes FAILS; any other crash
is VOID, never a pass.

    python3.13 -B run_upload_host.py [--wican DIR] [--json OUT] [--no-sanitize]
Exit 0 every row passes, 1 a row fails or the run is VOID, 2 refused. Writes only under
the worktree's scratch/wican-upload-host/<pid>/, removed at the end.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OTA_DIR = os.path.join(os.path.dirname(HERE), "wican_ota_host")
_spec = importlib.util.spec_from_file_location("run_ota_host", os.path.join(OTA_DIR, "run_ota_host.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)
Refused = R.Refused
WORKTREE = R.WORKTREE

CAR_URI = "/upload/car_data.json"
HANDLERS = ("ota", "car")
GOOD = ("split_in_boundary", "split_after_boundary", "split_in_type", "split_before_blank",
        "split_in_blank", "trickle_1", "trickle_3")
BAD = ("no_line", "no_type", "no_blank", "ends_early", "long_start", "long_start_trickle")
CASES = ("complete",) + GOOD + BAD
FAKE_HEADERS = R.FAKE_HEADERS + [os.path.join(HERE, "fake_file.h")]


# ----------------------------------------------------------------- extraction --

def strip_comments(text):
    """Comments blanked (newlines kept), strings kept, so a .uri's string can be read."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif text[i] in "\"'":
            q = text[i]
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        else:
            i += 1
    return "".join(out)


def registered_handler(text, uri):
    """The .handler of the one file-scope httpd_uri_t whose .uri is `uri`."""
    clean = R.blank_preprocessor(R.strip_c(text))
    nocomment = strip_comments(text)
    names = []
    for head_start, op, cl in R.top_blocks(clean):
        if not re.search(r"\bhttpd_uri_t\s+\w+\s*=\s*$", clean[head_start:op].strip()):
            continue
        body = nocomment[op:cl]
        if not re.search(r'\.uri\s*=\s*"%s"' % re.escape(uri), body):
            continue
        m = re.search(r"\.handler\s*=\s*(\w+)", body)
        if not m:
            raise Refused('the httpd_uri_t for "%s" names no .handler' % uri)
        names.append(m.group(1))
    if len(names) != 1:
        raise Refused('%d httpd_uri_t registrations for "%s" in config_server.c; the harness takes exactly one'
                      % (len(names), uri))
    return names[0]


def find_function(text, name):
    """(params, definition text) of the one file-scope esp_err_t function `name`."""
    clean = R.blank_preprocessor(R.strip_c(text))
    head_re = re.compile(r"(?:static\s+)?esp_err_t\s+%s\s*\(([^()]*)\)\s*$" % re.escape(name))
    hits = []
    for head_start, op, cl in R.top_blocks(clean):
        head = clean[head_start:op].strip()
        m = head_re.search(head)
        if m:
            start = head_start + clean[head_start:op].index(head)
            hits.append((m.group(1), text[start:cl + 1]))
    if len(hits) != 1:
        raise Refused("%d definitions of %s() in config_server.c; the harness takes exactly one" % (len(hits), name))
    return hits[0]


def _static(defn):
    return defn if defn.lstrip().startswith("static") else "static " + defn


def _call(name, params):
    n = len([p for p in params.split(",") if p.strip() and p.strip() != "void"])
    if n == 2:
        return "bool ok = false;\n    return %s(r, &ok);" % name
    if n == 1:
        return "return %s(r);" % name
    raise Refused("the handler %s takes %d parameters; the harness knows 1 or 2" % (name, n))


def handlers_source(text):
    """((ota name, car name), the C file the host build compiles)."""
    oname, oparams, odefn = R.find_handler(text)
    cname = registered_handler(text, CAR_URI)
    cparams, cdefn = find_function(text, cname)
    struct = R.find_struct(text)
    if not struct or not re.search(r"\bscratch\s*\[", struct):
        raise Refused("config_server.c defines no struct %s with a scratch buffer at file scope" % R._STRUCT)
    items = R.closure(text, odefn + "\n" + cdefn, skip=(oname, cname), faked=R.faked_names(FAKE_HEADERS))
    parts = ['#include "fake_idf.h"', '#include "fake_engine.h"', '#include "fake_file.h"', "",
             R.assemble(items), ""]
    if not any(it[1] == "struct" and R._STRUCT in it[2] for it in items):
        parts += [struct, ""]
    parts += [_static(odefn), "", _static(cdefn), ""]
    for entry, name, params in (("ota_entry", oname, oparams), ("car_entry", cname, cparams)):
        parts += ["int %s(httpd_req_t *r)" % entry, "{",
                  "    static struct %s server_data;" % R._STRUCT,
                  "    memset(&server_data, 0, sizeof server_data);",
                  "    r->user_ctx = &server_data;",
                  "    fake_set_scratch(server_data.scratch, sizeof server_data.scratch);",
                  "    " + _call(name, params), "}", ""]
    return (oname, cname), "\n".join(parts)


# -------------------------------------------------------------------- judging --

def judge(records):
    """[(row, handler, case, ok, detail)], and void (None or why). ok None: recorded."""
    by = {(r["handler"], r["case"]): r for r in records}
    missing = ["%s/%s" % k for k in ((h, c) for h in HANDLERS for c in CASES) if k not in by]
    if missing:
        return [], "cases with no record: %s" % ", ".join(missing)
    rows = []
    voids = []
    for h in HANDLERS:
        c = by[(h, "complete")]
        if c.get("crashed"):
            voids.append("%s control crashed: %s" % (h, c["kind"]))
            continue
        good = (c["restart"] and c["written_ok"]) if h == "ota" else \
               (c["file_ok"] and c["replies"] == ["200"] and not c["file_open_at_return"])
        if not good:
            voids.append("%s control, a complete upload, did not arrive byte for byte (%s)" % (h, summary(h, c)))
    if voids:
        return [], "; ".join(voids)
    for h in HANDLERS:
        for cname in GOOD + BAD:
            r = by[(h, cname)]
            if r.get("crashed"):
                if r["read_past"]:
                    rows.append(("W16", h, cname, False, r["kind"]))
                else:
                    voids.append("%s/%s crashed: %s" % (h, cname, r["kind"]))
                continue
            if r["runaway"]:
                rows.append(("W16", h, cname, False, "the handler kept receiving past the cap"))
                continue
            if cname in GOOD:
                ok = (r["restart"] and r["written_ok"]) if h == "ota" else \
                     (r["file_ok"] and r["replies"] == ["200"])
                rows.append(("W16", h, cname, bool(ok), "accepted? %s" % summary(h, r)))
                continue
            if h == "ota":
                nothing = r["writes"] == 0
                rows.append(("W16", h, cname, r["replies"] == ["400"] and nothing and not r["restart"],
                             "reply %s, esp_ota_write calls %d, restart %s, timeouts %d"
                             % (r["replies"], r["writes"], r["restart"], r["timeouts_total"])))
                if r["begun"] == 0:
                    rows.append(("W12", h, cname, False, "no update session was begun before the refusal"))
                else:
                    rows.append(("W12", h, cname, r["open_at_return"] == 0 and not r["restart"],
                                 "%d of %d sessions open at return (abort %d, end %d)"
                                 % (r["open_at_return"], r["begun"], r["ended_by_abort"], r["ended_by_end"])))
            else:
                nothing = r["fopens"] == 0 and r["fwrites"] == 0
                rows.append(("W16", h, cname, r["replies"] == ["400"] and nothing,
                             "reply %s, fopen %d, fwrite %d, timeouts %d"
                             % (r["replies"], r["fopens"], r["fwrites"], r["timeouts_total"])))
                rows.append(("rec", h, cname, None, "unlink calls %d (recorded, not judged)" % r["unlinks"]))
    return rows, ("; ".join(voids) if voids else None)


def summary(h, r):
    if h == "ota":
        return "restart %s, written_ok %s, written %d of %d, reply %s" % (
            r["restart"], r["written_ok"], r["written"], r["img_len"], r["replies"])
    return "file_ok %s, file %d of %d bytes, reply %s, fopen %d" % (
        r["file_ok"], r["file_len"], r["img_len"], r["replies"], r["fopens"])


# ---------------------------------------------------------------------- build --

def build_and_run(src_text, work, sanitize=True):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "handler.c"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src_text)
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        raise Refused("no C compiler (cc, or $CC)")
    exe = os.path.join(work, "upload_host")
    flags = ["-std=gnu11", "-g", "-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
             "-Wno-unused-function", "-Wno-pointer-sign", "-Wno-format", "-Wno-sign-compare",
             "-Werror=implicit-function-declaration"]
    if sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    argv = [cc] + flags + ["-I", OTA_DIR, "-I", HERE, "-o", exe, os.path.join(work, "handler.c"),
                           os.path.join(OTA_DIR, "fake_idf.c"), os.path.join(HERE, "fake_file.c"),
                           os.path.join(HERE, "upload_cases.c")]
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused("the host build failed:\n%s" % p.stderr[-3000:])
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
    records = []
    for h in HANDLERS:
        for cname in CASES:                      # one process per case, so a crash names its case
            r = subprocess.run([exe, h, cname], capture_output=True, text=True, env=env, timeout=120)
            lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
            if r.returncode != 0 or len(lines) != 1:
                records.append({"handler": h, "case": cname, "crashed": True, "returncode": r.returncode,
                                "read_past": "use-after-poison" in r.stderr, "kind": R.crash_kind(r.stderr)})
                continue
            rec = json.loads(lines[0])
            rec["handler"] = h
            records.append(rec)
    return records


def run(wican, sanitize=True, work=None, src_override=None):
    """((ota name, car name), records, rows, void)."""
    if src_override is None:
        path = os.path.join(wican, "config_server.c")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            raise Refused("cannot read %s: %s" % (path, e))
    else:
        text = src_override
    names, src = handlers_source(text)
    work = work or os.path.join(WORKTREE, "scratch", "wican-upload-host", str(os.getpid()))
    try:
        records = build_and_run(src, work, sanitize)
    finally:
        R.remove_tree(work)
    rows, void = judge(records)
    return names, records, rows, void


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wican", help="vtrux/wican folder (default: the pinned checkout)")
    ap.add_argument("--json", help="write the records and rows here")
    ap.add_argument("--no-sanitize", action="store_true")
    a = ap.parse_args(argv)
    try:
        wican = R.wican_dir(a.wican)
        names, records, rows, void = run(wican, sanitize=not a.no_sanitize)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    print("vtrux/wican: %s" % wican)
    print("handlers: %s() (OTA), %s() (%s)" % (names[0], names[1], CAR_URI))
    for row, h, cname, ok, detail in rows:
        print("%-5s  %-4s %-4s %-20s  %s" % ("REC" if ok is None else "PASS" if ok else "FAIL", row, h, cname, detail))
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"wican": wican, "handlers": list(names), "records": records,
                       "rows": [list(r) for r in rows], "void": void}, f, indent=1)
    if void:
        print("VOID: %s" % void)
        return 1
    fails = [r for r in rows if r[3] is False]
    by_req = {k: ("FAIL" if any(r[3] is False for r in rows if r[0] == k) else "PASS") for k in ("W16", "W12")}
    print("summary: %s; %d rows, %d fail" % (", ".join("%s %s" % kv for kv in by_req.items()), len(rows), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
