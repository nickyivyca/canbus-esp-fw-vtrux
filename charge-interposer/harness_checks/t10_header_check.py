#!/usr/bin/env python3
"""twai_port T10's one host fake against ESP-IDF v5.4.2: the fake's
driver/twai.h declares what ESP-IDF's driver/twai.h and hal/twai_types.h
declare, with the same values, for the S3 and the C3.

Tester, 2026-11-08 (port step 2b; the charge-interposer reviewer's ruling
on the T10 header diff: committed harness with a can-fail, reading the
shared fake at our pin and the IDF install, by the exported shell's
IDF_PATH, refused unless it is v5.4.2). Since 2b host_bridge compiles the
real twai_port on this fake, so the fake's header is the driver interface
the host build of the vehicle port sees.

    py -3.14 -B charge-interposer/harness_checks/t10_header_check.py [--can-fail]

The fake: TWAI_FAKE_DIR (<checkout>/shared/twai_port/test) if set, else
the pinned checkout's (shared_pin.folder("shared/twai_port")), its
include/driver/twai.h. ESP-IDF: IDF_PATH, whose tools/cmake/version.cmake
must say 5.4.2.

Each side is read as the compiler would for each chip: comments removed,
the #if / #ifdef / #ifndef / #elif / #else branches taken with that chip's
values (ESP-IDF's components/soc/<chip>/include/soc/soc_caps.h, and
CONFIG_XTAL_FREQ from components/esp_hw_support/port/<chip>/Kconfig.xtal's
default; any other name reads 0, as in C). From what is left:
  - #define NAME -> its value, with the side's own object-like macros
    expanded (ESP-IDF's also from soc_caps.h and esp_intr_alloc.h);
  - enum typedefs -> their items; struct / union typedefs -> their fields;
  - function prototypes twai_* -> return type and parameters.
A value that is a number on both sides compares as a number; ESP-IDF's
enum constants (hal/twai_types.h's and the chip's soc/clk_tree_defs.h)
count as defines of their values, so the fake's `#define TWAI_CLK_SRC_APB
4` compares with ESP-IDF's `TWAI_CLK_SRC_APB = SOC_MOD_CLK_APB`.

PASS when, for both chips: every define, enum, struct and function of the
fake is in ESP-IDF with the same value (its include guard aside), and
every one of ESP-IDF's driver/twai.h and hal/twai_types.h is in the fake,
except the allowances below, each with its reason, each of which must
still be needed (an allowance nothing needs is a failure: it would hide
the next difference of its kind).

--can-fail: the check on copies, each with one edit, must fail every one:
a struct field, a define's value, a prototype, an enum item, a timing
macro set to the other crystal's branch, a function missing from the
fake, and on ESP-IDF's side a new TWAI_ define and a version that is not
5.4.2 (refused). And each of RULES matters: with fake_in_idf or
idf_in_fake off, a fake-side mutant passes; an allowance nothing needs
passes with allowances_needed off and fails with it on.

Exit 0 pass, 1 a difference, 2 refused (no IDF_PATH, not v5.4.2, a file
missing, no fake).
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

IDF_VERSION = "5.4.2"
CHIPS = ("esp32s3", "esp32c3")
FAKE_H = os.path.join("include", "driver", "twai.h")
# ESP-IDF's API headers (both directions) and value headers (the fake's
# values may come from these; their own names are not required in the fake)
API_H = ("components/driver/twai/include/driver/twai.h",
         "components/hal/include/hal/twai_types.h")
VALUE_H = ("components/esp_hw_support/include/esp_intr_alloc.h",
           "components/soc/{chip}/include/soc/soc_caps.h",
           "components/soc/{chip}/include/soc/clk_tree_defs.h")
CAPS_H = "components/soc/{chip}/include/soc/soc_caps.h"
XTAL_KCONFIG = "components/esp_hw_support/port/{chip}/Kconfig.xtal"
VERSION_CMAKE = "tools/cmake/version.cmake"

# ESP-IDF names the fake leaves out, by kind, each with its reason
# (shared/twai_port/test/README.md and include/README.md)
ONLY_IDF = {
    "functions": (re.compile(r"^twai_\w+_v2$"),
                  "the _v2 (multi-controller) calls are left out, so a call the fake "
                  "does not implement fails to link (test/README.md)"),
    "defines": (re.compile(r"^TWAI_FRAME_(EXTD|STD)_ID_LEN_BYTES$"),
                "the ID-length constants of hal/twai_types.h; no host-built source "
                "uses them (checked: UNUSED_IN)"),
}
# where an ONLY_IDF define must be unused: twai_port's sources and include,
# and the interposer's main/
UNUSED_IN = ("{tp}/twai_port.c", "{tp}/twai_port_ll_unsupported.c",
             "{tp}/include/twai_port.h", "{code}/firmware/main")

# switched off one at a time by --can-fail's own control of the rules
RULES = {k: True for k in ("fake_in_idf", "idf_in_fake", "allowances_needed")}


class Refused(Exception):
    pass


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        raise Refused("cannot read %s (%s)" % (path, e))


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return re.sub(r"\\\n", " ", s)


def norm(t):
    return re.sub(r"\s+", " ", t).strip()


_NUM = re.compile(r"^\(?\s*(-?\s*(?:0[xX][0-9a-fA-F]+|\d+))[uUlL]*\s*\)?$")


def c_eval(expr, values):
    """A preprocessor #if expression, as C: defined(X), the known values,
    any other name 0."""
    e = re.sub(r"\bdefined\s*\(\s*(\w+)\s*\)", lambda m: "1" if m.group(1) in values else "0",
               expr)
    e = re.sub(r"\bdefined\s+(\w+)", lambda m: "1" if m.group(1) in values else "0", e)

    def ident(m):
        v = values.get(m.group(0))
        n = num(v) if v is not None else 0
        return str(n if n is not None else 0)
    e = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", e)
    e = re.sub(r"\b[A-Za-z_]\w*\b", ident, e)
    e = e.replace("&&", " and ").replace("||", " or ")
    e = re.sub(r"!(?!=)", " not ", e)
    try:
        return bool(eval(e, {"__builtins__": {}}, {}))      # noqa: S307
    except Exception as ex:                                  # noqa: BLE001
        raise Refused("cannot evaluate #if %r (%s)" % (expr, ex))


def num(v):
    if v is None:
        return None
    m = _NUM.match(v.strip())
    if m:
        return int(m.group(1).replace(" ", ""), 0)
    m = re.match(r"^\(\s*(\d+)\s*<<\s*(\d+)\s*\)$", v.strip())
    if m:
        return int(m.group(1)) << int(m.group(2))
    return None


def preprocess(text, values):
    """(the defines of the active branches {name: [value]}, the text of the
    active branches with directives removed). values: the macros known
    beforehand; the file's own object-like defines join them as read."""
    vals = dict(values)
    defines = {}
    out = []
    stack = []          # [active, taken] per open #if
    for ln in strip_comments(text).split("\n"):
        m = re.match(r"^\s*#\s*(\w+)\s*(.*)$", ln)
        active = all(a for a, _t in stack)
        if not m:
            if active:
                out.append(ln)
            continue
        d, rest = m.group(1), m.group(2).strip()
        if d in ("if", "ifdef", "ifndef"):
            if not active:
                stack.append([False, True])
                continue
            c = (c_eval(rest, vals) if d == "if" else
                 (rest.split()[0] in vals) if d == "ifdef" else
                 (rest.split()[0] not in vals))
            stack.append([c, c])
        elif d == "elif":
            if not stack:
                raise Refused("#elif without #if")
            outer = all(a for a, _t in stack[:-1])
            if stack[-1][1] or not outer:
                stack[-1][0] = False
            else:
                c = c_eval(rest, vals)
                stack[-1] = [c, c]
        elif d == "else":
            if not stack:
                raise Refused("#else without #if")
            stack[-1][0] = not stack[-1][1] and all(a for a, _t in stack[:-1])
            stack[-1][1] = True
        elif d == "endif":
            if not stack:
                raise Refused("#endif without #if")
            stack.pop()
        elif d == "define" and active:
            dm = re.match(r"^(\w+)(\([^)]*\))?\s*(.*)$", rest)
            name, args, val = dm.group(1), dm.group(2) or "", norm(dm.group(3))
            defines.setdefault(name, []).append(args + " " + val if args else val)
            if not args:
                vals[name] = val
        elif d == "undef" and active:
            vals.pop(rest.split()[0], None)
    if stack:
        raise Refused("an #if without its #endif")
    return defines, vals, "\n".join(out)


def enums_of(body):
    """{typedef name: [items]}, and {constant: value} (sequential, or an
    explicit number or earlier constant; None where neither, and for the
    constants after it, which no comparison takes as equal to anything)."""
    enums, consts = {}, {}
    for m in re.finditer(r"typedef\s+enum\s*\w*\s*\{(.*?)\}\s*(\w+)\s*;", body, re.S):
        items = [norm(x) for x in m.group(1).split(",") if norm(x)]
        enums[m.group(2)] = items
        n = -1
        for it in items:
            if "=" in it:
                k, v = [norm(x) for x in it.split("=", 1)]
                n = num(v) if num(v) is not None else consts.get(v)
            else:
                k, n = it, (None if n is None else n + 1)
            consts[k] = n
    return enums, consts


def extract(text, values):
    defines, vals, body = preprocess(text, values)
    enums, consts = enums_of(body)
    structs, funcs = {}, {}
    for m in re.finditer(r"typedef\s+(struct|union)\s*\w*\s*\{(.*?)\}\s*(\w+)\s*;", body, re.S):
        structs[m.group(3)] = [norm(x) for x in re.split(r";", m.group(2)) if norm(x)]
    flat = re.sub(r"typedef\s+(enum|struct|union)\s*\w*\s*\{.*?\}\s*\w+\s*;", " ", body,
                  flags=re.S)
    for m in re.finditer(r"([A-Za-z_][\w\s\*]*?)\b(twai_\w+)\s*\(([^;{)]*)\)\s*;", flat):
        funcs[m.group(2)] = norm(m.group(1)) + " (" + norm(m.group(3)) + ")"
    return {"defines": defines, "enums": enums, "structs": structs, "functions": funcs,
            "consts": consts, "vals": vals}


def expand(v, vals, depth=0):
    """v with its object-like macros (vals) expanded, then normalised."""
    if depth > 8:
        return v
    out = re.sub(r"\b[A-Za-z_]\w*\b",
                 lambda m: "(%s)" % vals[m.group(0)]
                 if m.group(0) in vals and vals[m.group(0)] not in ("", m.group(0)) else m.group(0),
                 v)
    out = norm(out)
    return out if out == v else expand(out, vals, depth + 1)


_ARITH = re.compile(r"^[0-9xXa-fA-F()<>|&+\-*~]+$")


def value(v, vals):
    """v's canonical form: its macros expanded, all whitespace removed, a
    parenthesis round a single token dropped, and a pure integer expression
    (numbers, ( ) << >> | & + - * ~) evaluated."""
    e = re.sub(r"\s+", "", expand(v, vals))
    e = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", e)
    while True:
        e2 = re.sub(r"\((-?[\w.]+)\)", r"\1", e)
        if e2 == e:
            break
        e = e2
    if e and _ARITH.match(e) and re.search(r"\d", e):
        try:
            return int(eval(e, {"__builtins__": {}}, {}))       # noqa: S307
        except Exception:                                        # noqa: BLE001
            pass
    return e


def idf_root():
    root = os.environ.get("IDF_PATH", "").strip()
    if not root:
        raise Refused("IDF_PATH is not set: run this from a shell exported for "
                      "ESP-IDF v%s (local-paths.md)" % IDF_VERSION)
    text = read(os.path.join(root, VERSION_CMAKE))
    got = ".".join(re.search(r"IDF_VERSION_%s\s+(\d+)" % k, text).group(1)
                   if re.search(r"IDF_VERSION_%s\s+(\d+)" % k, text) else "?"
                   for k in ("MAJOR", "MINOR", "PATCH"))
    if got != IDF_VERSION:
        raise Refused("IDF_PATH %s is ESP-IDF v%s, not v%s" % (root, got, IDF_VERSION))
    return root


def chip_values(root, chip):
    caps = read(os.path.join(root, CAPS_H.format(chip=chip)))
    vals = {}
    for m in re.finditer(r"^\s*#\s*define\s+(SOC_\w+)\s+([^\n/]*)", caps, re.M):
        vals[m.group(1)] = norm(m.group(2))
    k = read(os.path.join(root, XTAL_KCONFIG.format(chip=chip)))
    choice = re.search(r"choice XTAL_FREQ.*?default (XTAL_FREQ_\w+)", k, re.S)
    if not choice:
        raise Refused("%s: no default crystal" % XTAL_KCONFIG.format(chip=chip))
    m = re.search(r"default (\d+) if %s\b" % choice.group(1), k)
    if not m:
        raise Refused("%s: no CONFIG_XTAL_FREQ for %s" % (XTAL_KCONFIG.format(chip=chip),
                                                          choice.group(1)))
    vals["CONFIG_XTAL_FREQ"] = m.group(1)
    return vals


def compare(fake_h, root, tp, chip, out):
    """The differences for one chip: [str]."""
    base = chip_values(root, chip)
    fake = extract(read(fake_h), {})
    vtext = "\n".join(read(os.path.join(root, p.format(chip=chip))) for p in VALUE_H)
    value_side = extract(vtext, base)
    api = extract("\n".join(read(os.path.join(root, p)) for p in API_H), value_side["vals"])
    iconsts = dict(value_side["consts"], **api["consts"])
    # ESP-IDF's enum constants expand like its defines (TWAI_CLK_SRC_DEFAULT
    # is one there, a define in the fake)
    ivals = dict({k: str(v) for k, v in iconsts.items() if v is not None},
                 **dict(value_side["vals"], **api["vals"]))
    guard = re.search(r"^\s*#\s*ifndef\s+(\w+)", strip_comments(read(fake_h)), re.M)
    diffs, used = [], set()
    # the fake in ESP-IDF
    for kind in ("defines", "enums", "structs", "functions"):
        for k, fv in sorted(fake[kind].items()):
            if kind == "defines" and guard and k == guard.group(1):
                continue
            if kind == "defines":
                iv = api["defines"].get(k) or value_side["defines"].get(k)
                if iv is None and k in iconsts:
                    iv = [str(iconsts[k]) if iconsts[k] is not None
                          else "(enum constant, not evaluated)"]
                if iv is None:
                    diffs.append("%s: only in the fake: define %s = %s" % (chip, k, fv))
                    continue
                a = [value(x, fake["vals"]) for x in fv]
                b = [value(x, ivals) for x in iv]
                if a != b:
                    diffs.append("%s: define %s differs: fake %s, ESP-IDF %s" % (chip, k, a, b))
            else:
                iv = api[kind].get(k, value_side[kind].get(k))
                if iv is None:
                    diffs.append("%s: only in the fake: %s %s" % (chip, kind[:-1], k))
                elif fv != iv:
                    diffs.append("%s: %s %s differs:\n      fake:    %s\n      ESP-IDF: %s"
                                 % (chip, kind[:-1], k, fv, iv))
    if not RULES["fake_in_idf"]:
        diffs = []
    # ESP-IDF's API in the fake
    missing = []
    for kind in ("defines", "enums", "structs", "functions"):
        for k in sorted(api[kind]):
            if k in fake[kind]:
                continue
            allow = ONLY_IDF.get(kind)
            if allow and allow[0].match(k):
                used.add(kind)
                if kind == "defines":
                    for p in UNUSED_IN:
                        p = p.format(tp=tp, code=CODE)
                        for f in ([os.path.join(p, x) for x in sorted(os.listdir(p))]
                                  if os.path.isdir(p) else [p]):
                            if os.path.isfile(f) and re.search(r"\b%s\b" % k, read(f)):
                                missing.append("%s: %s is left out of the fake but used in %s"
                                               % (chip, k, f))
                continue
            missing.append("%s: only in ESP-IDF: %s %s" % (chip, kind[:-1], k))
    if RULES["idf_in_fake"]:
        diffs += missing
    if RULES["allowances_needed"]:
        for kind in sorted(set(ONLY_IDF) - used):
            diffs.append("%s: the allowance for %s (%s) is not needed: remove it"
                         % (chip, kind, ONLY_IDF[kind][1]))
    out("%s: fake %s; ESP-IDF API %s" % (
        chip, ", ".join("%d %s" % (len(fake[k]), k) for k in
                        ("defines", "enums", "structs", "functions")),
        ", ".join("%d %s" % (len(api[k]), k) for k in
                  ("defines", "enums", "structs", "functions"))))
    return diffs


def fake_folder():
    d = os.environ.get("TWAI_FAKE_DIR", "").strip()
    if d:
        return d
    import shared_pin
    try:
        return os.path.join(shared_pin.folder("shared/twai_port"), "test")
    except shared_pin.SharedPinError as e:
        raise Refused("no TWAI_FAKE_DIR, and the pinned shared/twai_port: %s" % e)


def check(fake_dir, root, out=print):
    """0 / 1; Refused before anything is compared."""
    fake_h = os.path.join(fake_dir, FAKE_H)
    if not os.path.isfile(fake_h):
        raise Refused("%s has no %s" % (fake_dir, FAKE_H))
    tp = os.path.dirname(os.path.abspath(fake_dir))
    out("the fake: %s" % fake_h)
    out("ESP-IDF v%s: %s" % (IDF_VERSION, root))
    diffs = []
    for chip in CHIPS:
        diffs += compare(fake_h, root, tp, chip, out)
    for d in diffs:
        out("DIFF  " + d)
    out("t10_header_check: %s" % ("PASS: the fake declares what ESP-IDF v%s declares, "
                                  "for %s" % (IDF_VERSION, " and ".join(CHIPS))
                                  if not diffs else "FAIL, %d difference(s)" % len(diffs)))
    return 0 if not diffs else 1


# ---- the can-fail -------------------------------------------------------
# (side, file under the fake folder or IDF_PATH, name, old, new); each must
# make the check fail (or, "refused", refuse)
MUTANTS = [
    ("fake", FAKE_H, "a struct field changed", "uint32_t identifier;", "uint16_t identifier;"),
    ("fake", FAKE_H, "a define's value changed", "#define TWAI_ALERT_BUS_OFF",
     "#define TWAI_ALERT_BUS_OFF (1 << 30) |"),
    ("fake", FAKE_H, "a prototype changed",
     "esp_err_t twai_transmit(const twai_message_t *message, TickType_t ticks_to_wait);",
     "esp_err_t twai_transmit(twai_message_t *message, TickType_t ticks_to_wait);"),
    ("fake", FAKE_H, "an enum item changed", "TWAI_MODE_LISTEN_ONLY", "TWAI_MODE_LISTENONLY"),
    ("fake", FAKE_H, "a timing macro from the other crystal's branch",
     ".quanta_resolution_hz = 10000000, .brp = 0, .tseg_1 = 15, .tseg_2 = 4",
     ".quanta_resolution_hz = 8000000, .brp = 0, .tseg_1 = 11, .tseg_2 = 4"),
    ("fake", FAKE_H, "a function missing from the fake",
     "esp_err_t twai_initiate_recovery(void);", ""),
    ("fake", FAKE_H, "BRP_MAX not the chips'", "#define TWAI_BRP_MAX                    16384",
     "#define TWAI_BRP_MAX                    256"),
    ("idf", API_H[1], "a new TWAI_ define in ESP-IDF",
     "#define TWAI_FRAME_MAX_DLC", "#define TWAI_NEW_THING 1\n#define TWAI_FRAME_MAX_DLC"),
    ("idf", VERSION_CMAKE, "ESP-IDF not v5.4.2 (refused)", "set(IDF_VERSION_PATCH 2)",
     "set(IDF_VERSION_PATCH 1)"),
]


def copy_idf(root, dest):
    """The ESP-IDF files this check reads, at their paths under dest."""
    paths = [VERSION_CMAKE] + list(API_H) + [p.format(chip=c) for c in CHIPS
                                            for p in VALUE_H + (XTAL_KCONFIG,)]
    for p in sorted(set(paths)):
        d = os.path.join(dest, *p.split("/"))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(root, *p.split("/")), d)
    return dest


def copy_fake(fake_dir, dest):
    """The fake's folder and its twai_port beside it (UNUSED_IN), under dest."""
    tp = os.path.dirname(os.path.abspath(fake_dir))
    t = os.path.join(dest, "twai_port")
    shutil.copytree(tp, t, ignore=shutil.ignore_patterns("__pycache__"))
    return os.path.join(t, os.path.basename(os.path.normpath(fake_dir)))


def can_fail(fake_dir, root, out=print):
    import scratch_dir
    bad = 0
    with scratch_dir.run("t10-header-canfail") as work:
        rc = check(fake_dir, root, out=lambda s: None)
        out("%s  control: the unchanged files pass" % ("ok  " if rc == 0 else "FAIL"))
        bad += rc != 0
        for n, (side, rel, name, old, new) in enumerate(MUTANTS, 1):
            w = os.path.join(work, "m%d" % n)
            fd = copy_fake(fake_dir, os.path.join(w, "fake"))
            ir = copy_idf(root, os.path.join(w, "idf"))
            p = os.path.join(fd if side == "fake" else ir, *rel.split("/"))
            text = read(p)
            if text.count(old) != 1:
                out("FAIL  m%d %s: NOT APPLIED (matched %d times)" % (n, name, text.count(old)))
                bad += 1
                continue
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(old, new))
            saved = os.environ.get("IDF_PATH")
            os.environ["IDF_PATH"] = ir
            try:
                try:
                    r = check(fd, idf_root(), out=lambda s: None)
                    got = "fails" if r else "PASSES"
                except Refused:
                    got = "refused"
            finally:
                if saved is None:
                    os.environ.pop("IDF_PATH", None)
                else:
                    os.environ["IDF_PATH"] = saved
            want = "refused" if "(refused)" in name else "fails"
            ok = got == want
            bad += not ok
            out("%s  m%d %s: %s" % ("ok  " if ok else "FAIL", n, name, got))
        # the rules each matter. fake_in_idf and idf_in_fake: with the rule
        # off, some fake-side mutant above passes. allowances_needed: an
        # allowance nothing needs (for a kind with none) passes with the rule
        # off and fails with it on.
        quiet = lambda s: None                                  # noqa: E731
        for rule in sorted(RULES):
            RULES[rule] = False
            try:
                if rule == "allowances_needed":
                    ONLY_IDF["structs"] = (re.compile(r"^never_needed_t$"), "a stale one")
                    try:
                        ok = check(fake_dir, root, out=quiet) == 0
                        RULES[rule] = True
                        ok = ok and check(fake_dir, root, out=quiet) != 0
                    finally:
                        del ONLY_IDF["structs"]
                else:
                    survived = []
                    for n, (side, rel, name, old, new) in enumerate(MUTANTS, 1):
                        if side != "fake":
                            continue
                        w = os.path.join(work, "r-%s-%d" % (rule, n))
                        fd = copy_fake(fake_dir, os.path.join(w, "fake"))
                        p = os.path.join(fd, *rel.split("/"))
                        text = read(p)
                        with open(p, "w", encoding="utf-8", newline="") as f:
                            f.write(text.replace(old, new))
                        if check(fd, root, out=quiet) == 0:
                            survived.append(name)
                    ok = bool(survived)
                out("%s  rule %s: %s" % ("ok  " if ok else "FAIL", rule,
                                         "matters" if ok else "nothing needs it"))
                bad += not ok
            finally:
                RULES[rule] = True
    out("t10_header_check --can-fail: %s" % ("PASS" if not bad else "FAIL, %d" % bad))
    return 0 if not bad else 1


def main(argv):
    if argv not in ([], ["--can-fail"]):
        print("usage: t10_header_check.py [--can-fail]")
        return 2
    try:
        root = idf_root()
        fd = fake_folder()
        if argv:
            return can_fail(fd, root)
        return check(fd, root)
    except Refused as e:
        print("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
