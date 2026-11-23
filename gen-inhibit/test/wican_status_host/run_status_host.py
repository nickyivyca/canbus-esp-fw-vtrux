#!/usr/bin/env python3
"""The status page's fw_version on the host: shared/wican W15, its page half.
Gen-inhibit tester, 2026-11-23, stage (b) batch 1 (the gen-inhibit reviewer's
instruction of 2026-11-23: on the host, as W12-W14 were; never from the device, whose
/check_status stays barred).

W15: "The status page's fw_version ... show[s] the app descriptor's version string as
it is ... Nothing is parsed out of it. The stock v<major>.<minor> parse is removed, so
the page no longer shows 0.00." The descriptor's version is `git describe --always
--tags --dirty` (shared/tools R4).

It takes the handler the component registers for the URI "/check_status" out of
vtrux/wican's config_server.c as it is (the httpd_uri_t whose .uri is "/check_status"
names it), with the file-scope #defines it names. It compiles it on the host under ASan
and UBSan against fake_status.c and ESP-IDF v5.4.2's own cJSON, with generated
stand-ins for the file-scope data the handler reads (device_config's fields,
device_id, can_datarate_str). The handler's text and names are read only to build
the fake. Each case writes a known version into the app descriptor and runs the
handler once; the body it sends must parse as one JSON object whose one fw_version
key is that version, the whole string. A prefix, 0.00, an empty value, a missing
key or a second fw_version key fails.

The cases are version strings R4 can put in the field (VERSIONS below). git_version,
the content type and the number of sends are recorded, not judged.

The component is the checkout of the tag firmware/main/idf_component.yml pins, or
--wican DIR. Its form before W15, shared/main tag 2026.11.04, is the can-fail:
test_run_status_host.py requires it to fail with fw_version 0.00.

    python3.13 -B run_status_host.py [--wican DIR] [--idf DIR] [--json OUT]
Exit 0 every case passes, 1 a case fails or is VOID (the handler crashed on the host),
2 refused (no component, no registration or handler, no ESP-IDF v5.4.2, no compiler,
the build failed). Writes only under the worktree's scratch/wican-status-host/<pid>/,
removed at the end.
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
WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
URI = "/check_status"
IDF_VERSION = "5.4.2"

# (case, version): strings shared/tools R4's `git describe --always --tags --dirty`
# can put in the 32-byte field.
VERSIONS = (
    ("commit", "0a740ce6"),                                 # the abbreviated commit, as on the bench image
    ("dirty", "0a740ce6-dirty"),                            # a build from a tree with changes
    ("stock_tag", "v4.20-1234-g0a740ce6ab-dirty"),          # described from a stock-style tag
    ("field_full", "0a740ce6ab12cd34ef56ab78cd90ef1"),      # 31 characters, the most the field holds
)

_spec = importlib.util.spec_from_file_location(
    "run_ota_host", os.path.join(os.path.dirname(HERE), "wican_ota_host", "run_ota_host.py"))
_ota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ota)
strip_c, blank_preprocessor, top_blocks = _ota.strip_c, _ota.blank_preprocessor, _ota.top_blocks
file_defines, remove_tree = _ota.file_defines, _ota.remove_tree


class Refused(Exception):
    pass


# ----------------------------------------------------------------- extraction --

def strip_comments(text):
    """The text with comments blanked to spaces (newlines kept); strings kept."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
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
        elif text[i] in "\"'":
            q = text[i]
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        else:
            i += 1
    return "".join(out)


def registered_handler(text):
    """The handler's name: the .handler of the one file-scope httpd_uri_t whose .uri is
    "/check_status". Refused when there is none or more than one."""
    clean = blank_preprocessor(strip_c(text))
    nocomment = strip_comments(text)
    names = []
    for head_start, op, cl in top_blocks(clean):
        if not re.search(r"\bhttpd_uri_t\s+\w+\s*=\s*$", clean[head_start:op].strip()):
            continue
        body = nocomment[op:cl]
        if not re.search(r'\.uri\s*=\s*"%s"' % re.escape(URI), body):
            continue
        m = re.search(r"\.handler\s*=\s*(\w+)", body)
        if not m:
            raise Refused('the httpd_uri_t for "%s" names no .handler' % URI)
        names.append(m.group(1))
    if len(names) != 1:
        raise Refused('%d httpd_uri_t registrations for "%s" in config_server.c; the harness takes exactly one'
                      % (len(names), URI))
    return names[0]


def find_function(text, name):
    """The definition text of the one file-scope esp_err_t function `name`."""
    clean = blank_preprocessor(strip_c(text))
    head_re = re.compile(r"(?:static\s+)?esp_err_t\s+%s\s*\(([^()]*)\)\s*$" % re.escape(name))
    hits = []
    for head_start, op, cl in top_blocks(clean):
        head = clean[head_start:op].strip()
        if head_re.search(head):
            start = head_start + clean[head_start:op].index(head)
            hits.append(text[start:cl + 1])
    if len(hits) != 1:
        raise Refused("%d definitions of %s() in config_server.c; the harness takes exactly one" % (len(hits), name))
    return hits[0]


def handler_source(text):
    """(handler name, the C file the host build compiles): the fake's header, the
    #defines the handler names (and those they name), stand-ins for the file-scope data
    it reads, the handler, and an entry."""
    name = registered_handler(text)
    defn = find_function(text, name)
    words_in = set(re.findall(r"\b[A-Za-z_]\w*\b", blank_preprocessor(strip_c(defn))))
    defs = file_defines(text)
    want, seen, todo = [], set(), [defn]
    while todo:
        for w in sorted(set(re.findall(r"\b[A-Za-z_]\w*\b", strip_c(todo.pop())))):
            if w in defs and w not in seen:
                seen.add(w)
                want.append(w)
                todo.append(defs[w])
    parts = ['#include "fake_status.h"', ""]
    parts += [defs[w] for w in want]
    parts.append("")
    fields = sorted(set(re.findall(r"\bdevice_config\s*\.\s*(\w+)", blank_preprocessor(strip_c(defn)))))
    if fields:
        parts.append("static struct {\n%s\n} device_config = {\n%s\n};"
                     % ("\n".join("    const char *%s;" % f for f in fields),
                        "\n".join('    .%s = "fake-%s",' % (f, f) for f in fields)))
    if "device_id" in words_in:
        parts.append('static char *device_id = "fake-device-id";')
    if "can_datarate_str" in words_in:
        parts.append('static const char *can_datarate_str[] = {"fake-rate"};')
    parts += ["", defn if defn.lstrip().startswith("static") else "static " + defn, ""]
    parts += ["int status_entry(httpd_req_t *r)", "{", "    return (int)%s(r);" % name, "}", ""]
    return name, "\n".join(parts)


# -------------------------------------------------------------------- judging --

def _pairs(pairs):
    return pairs                                  # keep every key, duplicates included


def judge_case(rec):
    """(ok, detail) for one case's record. ok None: VOID (crashed on the host)."""
    want = rec["version"]
    if rec.get("crashed"):
        return None, "the handler crashed on the host (exit %s): %s" % (rec.get("returncode"), rec.get("stderr_tail"))
    try:
        pairs = json.loads(rec["body"], object_pairs_hook=_pairs)
    except ValueError as e:
        return False, "the body is not JSON: %s" % e
    if not isinstance(pairs, list):
        return False, "the body is not a JSON object"
    got = [v for k, v in pairs if k == "fw_version"]
    if not got:
        return False, "no fw_version key"
    if len(got) > 1:
        return False, "%d fw_version keys: %r" % (len(got), got)
    v = got[0]
    if not isinstance(v, str):
        return False, "fw_version is not a string: %r" % (v,)
    if v == want:
        return True, "fw_version %r, the descriptor's version" % v
    if v == "":
        kind = "empty"
    elif want.startswith(v):
        kind = "a prefix of the descriptor's version"
    elif re.fullmatch(r"\d+\.\d\d", v):
        kind = "a parsed <major>.<minor>"
    else:
        kind = "not the descriptor's version"
    return False, "fw_version %r, %s %r" % (v, kind, want)


def judge(records):
    """[(case, ok, detail)] over VERSIONS, in order; a case with no record fails."""
    by = {r["case"]: r for r in records}
    rows = []
    for case, version in VERSIONS:
        r = by.get(case)
        if r is None or r.get("version") != version:
            rows.append((case, False, "no record for version %r" % version))
            continue
        ok, detail = judge_case(r)
        rows.append((case, ok, detail))
    return rows


# ---------------------------------------------------------------------- build --

def wican_dir(explicit):
    if explicit:
        return explicit
    try:
        return _ota.wican_dir(None)
    except _ota.Refused as e:
        raise Refused(str(e))


def idf_dir(explicit):
    """ESP-IDF v5.4.2's tree, for its cJSON: --idf, $IDF_PATH, or ~/esp/esp-idf-v5.4.2."""
    d = explicit or os.environ.get("IDF_PATH") or os.path.expanduser("~/esp/esp-idf-v%s" % IDF_VERSION)
    vc = os.path.join(d, "tools", "cmake", "version.cmake")
    try:
        with open(vc, encoding="utf-8") as f:
            t = f.read()
    except OSError as e:
        raise Refused("no ESP-IDF tree at %s (%s); pass --idf" % (d, e))
    ver = ".".join(re.search(r"IDF_VERSION_%s\s+(\d+)" % k, t).group(1) for k in ("MAJOR", "MINOR", "PATCH"))
    if ver != IDF_VERSION:
        raise Refused("ESP-IDF at %s is v%s; the firmware builds with v%s" % (d, ver, IDF_VERSION))
    cj = os.path.join(d, "components", "json", "cJSON")
    if not os.path.isfile(os.path.join(cj, "cJSON.c")):
        raise Refused("no cJSON.c in %s" % cj)
    return d, cj


def build_and_run(src_text, work, cjson_dir, sanitize=True):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "handler.c"), "w", encoding="utf-8", newline="\n") as f:
        f.write(src_text)
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        raise Refused("no C compiler (cc, or $CC)")
    exe = os.path.join(work, "status_host")
    flags = ["-std=gnu11", "-g", "-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-function",
             "-Wno-format", "-DWICAN_FEATURE_AUTOPID=0"]
    if sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    argv = [cc] + flags + ["-I", HERE, "-I", cjson_dir, "-o", exe, os.path.join(work, "handler.c"),
                           os.path.join(HERE, "fake_status.c"), os.path.join(cjson_dir, "cJSON.c"), "-lm"]
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused("the host build failed:\n%s" % p.stderr[-3000:])
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
    records = []
    for case, version in VERSIONS:              # one process per case, so a crash names its case
        r = subprocess.run([exe, version], capture_output=True, text=True, env=env, timeout=60)
        rec = {"case": case, "version": version}
        m = re.search(r"^RET (-?\d+) SENDS (\d+) LEN (\d+)\nTYPE (.*)\nBODYHEX ([0-9a-f]*)$", r.stdout, re.M)
        if r.returncode != 0 or not m:
            lines = [ln.strip() for ln in r.stderr.strip().splitlines()]
            named = [re.sub(r"0x[0-9a-f]+", "0x..", re.sub(r"^==\d+==", "", ln)) for ln in lines
                     if re.search(r"ERROR: AddressSanitizer|runtime error|(WRITE|READ) of size", ln)]
            frames = []
            for ln in lines:                      # the first stack's function names, no paths
                m = re.match(r"#(\d+) 0x[0-9a-f]+ in (\S+)", ln)
                if m:
                    if m.group(1) == "0" and frames:
                        break
                    frames.append(m.group(2))
            if frames:
                named.append("in " + " < ".join(frames[:6]))
            named = [re.sub(r"\s/\S+", "", ln) for ln in named]
            rec.update(crashed=True, returncode=r.returncode,
                       stderr_tail=" | ".join(named[:4] or lines[-3:]))
        else:
            body = bytes.fromhex(m.group(5)).decode("utf-8", errors="replace")
            rec.update(ret=int(m.group(1)), sends=int(m.group(2)), length=int(m.group(3)),
                       content_type=m.group(4), body=body)
            try:
                obj = json.loads(body)
                rec["git_version"] = obj.get("git_version") if isinstance(obj, dict) else None
            except ValueError:
                rec["git_version"] = None
        records.append(rec)
    return records


def run(wican, idf=None, sanitize=True, work=None, src_override=None):
    """(handler name, records, rows)."""
    _, cj = idf_dir(idf)
    if src_override is None:
        path = os.path.join(wican, "config_server.c")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            raise Refused("cannot read %s: %s" % (path, e))
    else:
        text = src_override
    name, src = handler_source(text)
    work = work or os.path.join(WORKTREE, "scratch", "wican-status-host", str(os.getpid()))
    try:
        records = build_and_run(src, work, cj, sanitize)
    finally:
        remove_tree(work)
    return name, records, judge(records)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wican", help="vtrux/wican folder (default: the pinned checkout)")
    ap.add_argument("--idf", help="ESP-IDF v5.4.2 tree, for its cJSON (default: $IDF_PATH, ~/esp/esp-idf-v5.4.2)")
    ap.add_argument("--json", help="write the records and rows here")
    ap.add_argument("--no-sanitize", action="store_true")
    a = ap.parse_args(argv)
    try:
        wican = wican_dir(a.wican)
        idf, _ = idf_dir(a.idf)
        name, records, rows = run(wican, idf, sanitize=not a.no_sanitize)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2
    print("vtrux/wican: %s" % wican)
    print("ESP-IDF (cJSON): %s" % idf)
    print('handler for "%s": %s()' % (URI, name))
    by = {r["case"]: r for r in records}
    for case, ok, detail in rows:
        r = by.get(case, {})
        rec = ("; recorded: git_version %r, type %s, sends %s, ret %s"
               % (r.get("git_version"), r.get("content_type"), r.get("sends"), r.get("ret"))
               if not r.get("crashed") else "")
        print("%-4s  W15  %-10s  %s%s" % ("VOID" if ok is None else "PASS" if ok else "FAIL", case, detail, rec))
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"wican": wican, "idf": idf, "uri": URI, "handler": name, "records": records,
                       "rows": [list(r) for r in rows]}, f, indent=1)
    fails = [r for r in rows if r[1] is False]
    voids = [r for r in rows if r[1] is None]
    print("summary: W15 page half %s; %d cases, %d fail, %d void"
          % ("PASS" if not fails and not voids else "VOID" if not fails else "FAIL", len(rows), len(fails), len(voids)))
    return 1 if fails or voids else 0


if __name__ == "__main__":
    sys.exit(main())
