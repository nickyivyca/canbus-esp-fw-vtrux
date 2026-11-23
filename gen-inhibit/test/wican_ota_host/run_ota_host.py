#!/usr/bin/env python3
"""The OTA upload handler on the host: gen-inhibit spec 9.2 items 4-6 (shared/wican
W12, W13, W14). Gen-inhibit tester, 2026-11-23, stage (b) batch 1.

It takes the OTA handler, the one function of vtrux/wican's config_server.c that calls
esp_ota_begin(), out of the component's file as it is, with the file-scope #defines and
the struct it uses, and compiles it on the host against fake_idf.c, a fake of the
ESP-IDF calls it makes, under ASan and UBSan. ota_cases.c drives it through a complete
upload and each kind of failed one, and this script judges each case's record against
the spec's words:

  W14  every esp_ota_begin() is given OTA_WITH_SEQUENTIAL_WRITES (0xfffffffe), never
       OTA_SIZE_UNKNOWN or a size: ESP-IDF then erases each block as it is written.
  W12  every exit after the update starts, but the restart into the new image, ends
       the session: none is open when the handler returns, unless it started the
       restart. A call on a session that is not open is recorded, not judged.
  W13  a stall is dropped on the 4th receive timeout in a row: the handler returns
       having taken exactly 4, in a row (the session's end is W12's row); 3 in a row, or 3 or 2
       at a time with chunks between them, do not drop it, and the upload completes.
       The 5 s of each timeout is the server's, measured on the bench.

The control: the complete upload must restart into the image, written byte for byte,
or nothing is judged (VOID): a fake that cannot carry a good upload proves nothing.

The component is the checkout of the tag firmware/main/idf_component.yml pins
(../gen_inhibit_shim/wican_ref.py), or --wican DIR. Its pre-fix form, shared/main tag
2026.11.04 (OTA_SIZE_UNKNOWN, 20 timeouts, no abort on a receive failure), is the
can-fail: test_run_ota_host.py runs it and requires W12, W13 and W14 to fail there.

    python3.13 -B run_ota_host.py [--wican DIR] [--json OUT] [--no-sanitize]
Exit 0 every row passes, 1 a row fails or the run is VOID, 2 refused (no component, no
handler, no compiler). Writes only under the worktree's scratch/wican-ota-host/<pid>/,
removed at the end.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SEQ = 0xFFFFFFFE
UNKNOWN = 0xFFFFFFFF
DROP_AT = 4                                     # W13: the 4th timeout in a row

W12_CASES = ("reset_mid", "close_mid", "invalid_mid", "reset_first", "close_after_preamble",
             "reset_at_trailer", "write_fails", "end_refuses", "setboot_fails",
             "stall_mid", "stall_first", "stall_at_trailer")
# ALL_CASES below takes every case once; the stall cases are listed in W12_CASES.
STALL_CASES = ("stall_mid", "stall_first")
# A stall at the trailing boundary (gen-inhibit reviewer's ruling, 2026-11-23, reading B):
# W13 and spec 9.2 item 5 bound how long a stalled upload holds the server; the trailing
# read giving up sooner keeps that bound, and W12 ends the session. So its W13 row is
# RECORDED, not judged: the timeouts taken (5 s each on the device) and the reply.
RECORDED_STALL = ("stall_at_trailer",)
SPACED_CASES = ("timeouts3_mid", "timeouts3_x4", "timeouts2_x2")
ALL_CASES = ("complete",) + W12_CASES + ("begin_fails",) + SPACED_CASES


class Refused(Exception):
    pass


# ----------------------------------------------------------------- extraction --

def strip_c(text):
    """The text with comments, string and char literals blanked to spaces (newlines
    kept), so braces and words inside them are not read. Same length as text."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def blank_preprocessor(clean):
    """Preprocessor lines (with continuations) blanked, so `#define X {` is not a block."""
    lines = clean.split("\n")
    cont = False
    for idx, ln in enumerate(lines):
        if cont or ln.lstrip().startswith("#"):
            cont = ln.rstrip().endswith("\\")
            lines[idx] = " " * len(ln)
        else:
            cont = False
    return "\n".join(lines)


def top_blocks(clean):
    """[(head_start, open, close)] for each file-scope {...}: head_start is where the
    declaration before it starts (after the previous ';', '}' or file start)."""
    blocks = []
    depth = 0
    last_end = 0
    start = None
    for i, c in enumerate(clean):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                raise Refused("unbalanced '}' at offset %d" % i)
            if depth == 0:
                blocks.append((last_end, start, i))
                last_end = i + 1
        elif c == ";" and depth == 0:
            last_end = i + 1
    if depth:
        raise Refused("unbalanced '{' at end of file")
    return blocks


_FUNC_HEAD = re.compile(r"(?:static\s+)?esp_err_t\s+(\w+)\s*\(([^()]*)\)\s*$")


def find_handler(text):
    """(name, params, definition text) of the one function whose body calls
    esp_ota_begin(). Refused when there is none or more than one."""
    clean = blank_preprocessor(strip_c(text))
    hits = []
    for head_start, op, cl in top_blocks(clean):
        if not re.search(r"\besp_ota_begin\s*\(", clean[op:cl]):
            continue
        head = clean[head_start:op].strip()
        m = _FUNC_HEAD.search(head)
        if not m:
            raise Refused("a block calling esp_ota_begin() is not an esp_err_t function: %r" % head[-80:])
        defn_start = head_start + clean[head_start:op].index(head)
        hits.append((m.group(1), m.group(2), text[defn_start:cl + 1]))
    if len(hits) != 1:
        raise Refused("%d functions call esp_ota_begin() in config_server.c; the harness takes exactly one"
                      % len(hits))
    return hits[0]


_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])(.*)$", re.M)
_STRUCT = "file_server_data"


def file_defines(text):
    """{name: line} for file-scope object-like #defines of one line."""
    out = {}
    for m in _DEFINE.finditer(text):
        if m.group(2).rstrip().endswith("\\"):
            continue
        out.setdefault(m.group(1), m.group(0).strip())
    return out


def find_struct(text):
    clean = blank_preprocessor(strip_c(text))
    for head_start, op, cl in top_blocks(clean):
        if re.search(r"\bstruct\s+%s\s*$" % _STRUCT, clean[head_start:op].strip()):
            end = clean.index(";", cl)
            start = head_start + clean[head_start:op].index(clean[head_start:op].strip())
            return text[start:end + 1]
    return None


def handler_source(text):
    """The C file the host build compiles: the fake's header, the #defines the handler
    (and the struct, and those defines) name, the struct, the handler, and an entry."""
    name, params, defn = find_handler(text)
    struct = find_struct(text)
    if "struct %s" % _STRUCT in defn and not struct:
        raise Refused("the handler uses struct %s, which config_server.c does not define at file scope" % _STRUCT)
    defs = file_defines(text)
    want = []
    seen = set()
    todo = [defn] + ([struct] if struct else [])
    while todo:
        words = set(re.findall(r"\b[A-Za-z_]\w*\b", strip_c(todo.pop())))
        for w in sorted(words):
            if w in defs and w not in seen:
                seen.add(w)
                want.append(w)
                todo.append(defs[w])
    nparams = len([p for p in params.split(",") if p.strip() and p.strip() != "void"])
    if nparams == 2:
        call = "bool ok = false;\n    return %s(r, &ok);" % name
    elif nparams == 1:
        call = "return %s(r);" % name
    else:
        raise Refused("the handler %s takes %d parameters; the harness knows 1 or 2" % (name, nparams))
    parts = ['#include "fake_idf.h"', "#include \"fake_engine.h\"", ""]
    parts += [defs[w] for w in want]
    parts += ["", struct or "", "", "static " + defn if not defn.lstrip().startswith("static") else defn, ""]
    parts += ["int ota_entry(httpd_req_t *r)", "{",
              "    static struct %s server_data;" % _STRUCT if struct else "    static char server_data[8192];",
              "    memset(&server_data, 0, sizeof server_data);",
              "    r->user_ctx = &server_data;",
              "    " + call, "}", ""]
    return name, "\n".join(parts)


# -------------------------------------------------------------------- judging --

def judge(records):
    """[(row, case, ok, detail)], and void (None or why)."""
    by = {r["case"]: r for r in records}
    missing = [c for c in ALL_CASES if c not in by]
    if missing:
        return [], "cases with no record: %s" % ", ".join(missing)
    rows = []
    c = by["complete"]
    if not (c["restart"] and c["written_ok"] and c["end_ok"] and not c["runaway"]):
        return [], ("the control, a complete upload, did not restart into the image written byte for byte "
                    "(restart %s, written_ok %s, end_ok %s, runaway %s)"
                    % (c["restart"], c["written_ok"], c["end_ok"], c["runaway"]))
    for r in records:
        if r["runaway"]:
            rows.append(("harness", r["case"], False, "the handler kept receiving past the cap"))
    # W14
    for cname in ALL_CASES:
        r = by[cname]
        for s in r["begin_sizes"]:
            rows.append(("W14", cname, s == SEQ,
                         "esp_ota_begin image_size 0x%08x (%s)"
                         % (s, "OTA_WITH_SEQUENTIAL_WRITES" if s == SEQ else
                            "OTA_SIZE_UNKNOWN" if s == UNKNOWN else "not sequential")))
    # W12
    for cname in W12_CASES:
        r = by[cname]
        if r["begun"] == 0:
            rows.append(("W12", cname, False, "no update session was begun; the case did not reach the body"))
            continue
        if r["restart"]:
            rows.append(("W12", cname, False, "a failed upload started the restart"))
            continue
        rows.append(("W12", cname, r["open_at_return"] == 0,
                     "%d of %d sessions open at return (abort %d, end %d); reply %s; misuse %d"
                     % (r["open_at_return"], r["begun"], r["ended_by_abort"], r["ended_by_end"],
                        r["replies"], r["misuse"])))
    b = by["begin_fails"]
    rows.append(("W12", "begin_fails", b["open_at_return"] == 0 and not b["restart"],
                 "begin refused: %d open, restart %s, reply %s" % (b["open_at_return"], b["restart"], b["replies"])))
    # W13
    for cname in STALL_CASES:
        r = by[cname]
        # The drop alone; that the drop ends the session is W12's row for the same case.
        ok = not r["restart"] and r["consec_at_return"] == DROP_AT and r["timeouts_total"] == DROP_AT
        rows.append(("W13", cname, ok,
                     "returned after %d timeouts in a row (%d in all), %d sessions open, reply %s"
                     % (r["consec_at_return"], r["timeouts_total"], r["open_at_return"], r["replies"])))
    for cname in RECORDED_STALL:
        r = by[cname]
        rows.append(("W13", cname, None,
                     "recorded, not judged (reviewer's ruling, 2026-11-23): dropped after %d timeouts in a "
                     "row (about %d s at the server's 5 s), reply %s, %d sessions open"
                     % (r["consec_at_return"], 5 * r["consec_at_return"], r["replies"], r["open_at_return"])))
    for cname in SPACED_CASES:
        r = by[cname]
        ok = r["restart"] and r["written_ok"] and r["consec_max"] < DROP_AT
        rows.append(("W13", cname, ok,
                     "most in a row %d, %d in all; completed %s, written_ok %s"
                     % (r["consec_max"], r["timeouts_total"], r["restart"], r["written_ok"])))
    return rows, None


# ---------------------------------------------------------------------- build --

def wican_dir(explicit):
    if explicit:
        return explicit
    ref = os.path.join(os.path.dirname(HERE), "gen_inhibit_shim", "wican_ref.py")
    p = subprocess.run([sys.executable, "-B", ref], capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused("wican_ref.py: %s" % p.stderr.strip())
    return p.stdout.strip()


def remove_tree(path):
    def onerr(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(p)
        except OSError as e:
            print("could not remove %s: %s" % (p, e), file=sys.stderr)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerr)


def build_and_run(src_text, work, sanitize=True):
    """Compile handler.c from src_text with the fake, run every case, return records."""
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "handler.c"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src_text)
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        raise Refused("no C compiler (cc, or $CC)")
    exe = os.path.join(work, "ota_host")
    flags = ["-std=gnu11", "-g", "-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
             "-Wno-unused-function", "-Wno-pointer-sign", "-Wno-format"]
    if sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    argv = [cc] + flags + ["-I", HERE, "-o", exe, os.path.join(work, "handler.c"),
                           os.path.join(HERE, "fake_idf.c"), os.path.join(HERE, "ota_cases.c")]
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused("the host build failed:\n%s" % (p.stderr[-3000:]))
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
    records = []
    for cname in ALL_CASES:                      # one process per case, so a crash names its case
        r = subprocess.run([exe, cname], capture_output=True, text=True, env=env, timeout=120)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        if r.returncode != 0 or len(lines) != 1:
            records.append({"case": cname, "crashed": True, "returncode": r.returncode,
                            "stderr": r.stderr[-2000:]})
            continue
        records.append(json.loads(lines[0]))
    return records


def run(wican, sanitize=True, work=None, src_override=None):
    """(handler name, records, rows, void)."""
    path = os.path.join(wican, "config_server.c")
    if src_override is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            raise Refused("cannot read %s: %s" % (path, e))
    else:
        text = src_override
    name, src = handler_source(text)
    work = work or os.path.join(WORKTREE, "scratch", "wican-ota-host", str(os.getpid()))
    try:
        records = build_and_run(src, work, sanitize)
    finally:
        remove_tree(work)
    crashed = [r for r in records if r.get("crashed")]
    good = [r for r in records if not r.get("crashed")]
    rows, void = judge(good) if not crashed else ([], None)
    if crashed:
        void = "cases crashed: %s" % "; ".join("%s (exit %s) %s" % (r["case"], r["returncode"],
                                                                     r["stderr"].strip().splitlines()[-1:] )
                                                for r in crashed)
    return name, records, rows, void


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wican", help="vtrux/wican folder (default: the pinned checkout)")
    ap.add_argument("--json", help="write the records and rows here")
    ap.add_argument("--no-sanitize", action="store_true")
    a = ap.parse_args(argv)
    try:
        wican = wican_dir(a.wican)
        name, records, rows, void = run(wican, sanitize=not a.no_sanitize)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    print("vtrux/wican: %s" % wican)
    print("handler: %s()" % name)
    for row, cname, ok, detail in rows:
        print("%-5s  %-7s  %-22s  %s" % ("REC" if ok is None else "PASS" if ok else "FAIL", row, cname, detail))
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"wican": wican, "handler": name, "records": records,
                       "rows": [list(r) for r in rows], "void": void}, f, indent=1)
    if void:
        print("VOID: %s" % void)
        return 1
    fails = [r for r in rows if r[2] is False]
    recorded = [r for r in rows if r[2] is None]
    summary = {k: ("FAIL" if any(r[2] is False for r in rows if r[0] == k) else "PASS")
               for k in ("W12", "W13", "W14")}
    print("summary: %s; %d rows, %d fail, %d recorded" % (", ".join("%s %s" % kv for kv in summary.items()),
                                                          len(rows), len(fails), len(recorded)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
