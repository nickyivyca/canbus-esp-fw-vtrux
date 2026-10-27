"""Cross-check the constants in machine.cpp/.h against machine.py.

The differential trace test (make_golden.py + host_runner.cpp) is the real
verification, but it needs a C++ compiler. This check needs nothing but Python
and catches the class of port bug that is both most likely and most silent: a
threshold or bit-layout constant transcribed wrong. A wrong `vmax_full_mv` does
not fail to compile and does not look wrong; it just charges to the wrong
voltage.

Compares three things:

  1. every field of Config, Python defaults vs configDefaults() in machine.cpp
  2. the frame identifiers and enum values in machine.h
  3. the two hard-coded page-03 payloads
  4. the codes logged in event fields: RepeatEnd (REPEAT ended's c) against
     machine.py's REPEAT_* constants, and TripReason (TRIP's a) against the
     position of each reason in machine.py's TRIP_KEYS (added 2026-10-13:
     neither was compared, and RepeatEnd numbers implicitly, so the regex in
     check_identifiers could not have seen it)

`--self-test` runs item 4 against renumbered copies of machine.h, which must
each fail.

Usage (from the repo root):
    py -3.12 projects/vtrux/tools/interposer/firmware/test/host_diff/check_port.py
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_FW_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "src"))
_INTERPOSER = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _INTERPOSER)

import machine as M          # noqa: E402


def _read(name):
    with open(os.path.join(_FW_SRC, name), "r", encoding="utf-8") as f:
        return f.read()


def _num(expr):
    """Evaluate a simple C integer expression: literals, u suffixes, * + -."""
    e = expr.strip().rstrip(";").strip()
    if e in ("true", "false"):
        return e == "true"
    e = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uU]?[lL]*\b", r"\1", e)
    if not re.fullmatch(r"[\dxa-fA-F\s*+\-()]+", e):
        raise ValueError("cannot evaluate %r" % expr)
    return eval(e, {"__builtins__": {}}, {})       # noqa: S307 - constrained above


def check_config(problems):
    cpp = _read("machine.cpp")
    body = re.search(r"void configDefaults\(Config& c\) \{(.*?)\n\}", cpp,
                     re.S)
    if not body:
        problems.append("configDefaults() not found in machine.cpp")
        return
    got = {}
    for m in re.finditer(r"c\.(\w+)\s*=\s*([^;]+);", body.group(1)):
        got[m.group(1)] = _num(m.group(2))

    py = M.Config()
    for slot in py.__slots__:
        want = getattr(py, slot)
        if slot not in got:
            problems.append("Config.%s missing from machine.cpp" % slot)
            continue
        if got[slot] != want:
            problems.append("Config.%s: python=%r cpp=%r"
                            % (slot, want, got[slot]))
    for name in got:
        if name not in py.__slots__:
            problems.append("Config.%s in machine.cpp but not machine.py"
                            % name)
    print("config fields checked: %d" % len(py.__slots__))


def check_identifiers(problems):
    hdr = _read("machine.h")
    consts = {}
    for m in re.finditer(
            r"static const \w+ (\w+)\s*=\s*([^;]+);", hdr):
        consts[m.group(1)] = _num(m.group(2))
    for m in re.finditer(r"^\s*(MODE_\w+|S_\w+|TO_\w+)\s*=\s*(\d+)",
                         hdr, re.M):
        consts[m.group(1)] = int(m.group(2))

    # C++ name -> machine.py name
    pairs = [
        ("CMD_ID", "CMD_ID"),
        ("BMS_STATUS", "BMS_STATUS"),
        ("BMS_LIMITS", "BMS_LIMITS"),
        ("BMS_DATA1", "BMS_DATA1"),
        ("BMS_DATA2", "BMS_DATA2"),
        ("VCM_EVAP", "VCM_EVAP"),
        ("CHG_STATUS", "CHG_STATUS"),
        ("CHG_HV_STATUS", "CHG_HV_STATUS"),
        ("CHG_INFO", "CHG_INFO"),
        ("CHG_STATE_CHARGING", "CHG_STATE_CHARGING"),
        ("MODE_EXPORT", "MODE_EXPORT"),
        ("MODE_CHARGER", "MODE_CHARGER"),
        ("MODE_STANDBY", "MODE_STANDBY"),
        ("MODE_LOW_POWER", "MODE_LOW_POWER"),
        ("MODE_INVALID", "MODE_INVALID"),
        ("TO_VEHICLE", "TO_VEHICLE"),
        ("TO_CHARGER", "TO_CHARGER"),
        ("S_PASSTHROUGH", "S_PASSTHROUGH"),
        ("S_MONITOR", "S_MONITOR"),
        ("S_OVERRIDE", "S_OVERRIDE"),
        ("S_TERMINATED", "S_TERMINATED"),
        ("S_SAFE", "S_SAFE"),
        # diagnostics (spec 8): the on-wire ids and versions must agree, or a
        # log decodes against the wrong schema
        ("DIAG_SA", "DIAG_SA"),
        ("DIAG_STATUS_ID", "DIAG_STATUS_ID"),
        ("DIAG_OBSERVED_ID", "DIAG_OBSERVED_ID"),
        ("DIAG_COUNTERS_ID", "DIAG_COUNTERS_ID"),
        ("DIAG_BUILD_ID", "DIAG_BUILD_ID"),
        ("DIAG_SCHEMA_VER", "DIAG_SCHEMA_VER"),
        ("DIAG_FW_VER", "DIAG_FW_VER"),
    ]
    for cname, pname in pairs:
        if cname not in consts:
            problems.append("%s not found in machine.h" % cname)
            continue
        want = getattr(M, pname)
        if consts[cname] != want:
            problems.append("%s: python=%r cpp=%r" % (cname, want, consts[cname]))
    print("identifiers checked  : %d" % len(pairs))

    # maincClosed() must accept exactly machine.py's MAINC_CLOSED
    m = re.search(r"inline bool maincClosed\(uint8_t ms\) \{ return ([^;]+); \}",
                  hdr)
    if not m:
        problems.append("maincClosed() not found in machine.h")
    else:
        accepted = set(int(x) for x in re.findall(r"ms == (\d+)u", m.group(1)))
        if accepted != set(M.MAINC_CLOSED):
            problems.append("maincClosed: python=%r cpp=%r"
                            % (sorted(M.MAINC_CLOSED), sorted(accepted)))


def parse_enum(hdr, name):
    """{member: value} of `enum <name> ... { ... };` in machine.h, numbered
    the C way: an explicit `= N` sets the value, every other member is one
    more than the last. Comments are stripped first. None if absent."""
    m = re.search(r"enum\s+%s\b[^{]*\{(.*?)\};" % name, hdr, re.S)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    out, nxt = {}, 0
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
            k, nxt = k.strip(), _num(v)
        else:
            k = item
        out[k] = nxt
        nxt += 1
    return out


# machine.h's TripReason member -> the TRIP_KEYS entry it stands for. The
# code on the wire is the member's value; machine.py's is the key's index
# + 1 (code 0 is "no trip"), so the two must agree position for position.
TRIP_PAIRS = [
    ("TRIP_VMAX_HARD", "over hard ceiling"),
    ("TRIP_CELL_OVERVOLT", "cell_overvolt"),
    ("TRIP_CELL_UNDERVOLT_RETIRED", "cell_undervolt"),
    ("TRIP_BMS_EPO", "BMS EPO"),
    ("TRIP_BMS_ALARM_TYPE3", "BMS alarm"),
    ("TRIP_HVIL_OPEN", "HVIL open"),
    ("TRIP_CONTACTOR", "contactor state"),
    ("TRIP_CHARGER_FAULT", "charger fault"),
    ("TRIP_CHG_STATE", "charger out of state 12"),
    ("TRIP_BMS_STALE", "BMS frames stale"),
    ("TRIP_CHG_STALE", "charger frames stale"),
    ("TRIP_OVERRIDE_CAP", "override exceeded session cap"),
]

REPEAT_NAMES = ("REPEAT_COMPLETE", "REPEAT_SUPERSEDED", "REPEAT_RESET",
                "REPEAT_REPLACED")


def check_event_codes(problems, hdr=None):
    hdr = _read("machine.h") if hdr is None else hdr
    rep_end = parse_enum(hdr, "RepeatEnd")
    if rep_end is None:
        problems.append("enum RepeatEnd not found in machine.h")
    else:
        for n in REPEAT_NAMES:
            if n not in rep_end:
                problems.append("%s not in machine.h's RepeatEnd" % n)
            elif not hasattr(M, n):
                problems.append("%s not in machine.py" % n)
            elif rep_end[n] != getattr(M, n):
                problems.append("%s: python=%r cpp=%r"
                                % (n, getattr(M, n), rep_end[n]))
        extra = sorted(set(rep_end) - set(REPEAT_NAMES))
        if extra:
            problems.append("RepeatEnd members this check does not know: %s"
                            % ", ".join(extra))
    trip = parse_enum(hdr, "TripReason")
    if trip is None:
        problems.append("enum TripReason not found in machine.h")
    else:
        if trip.get("TRIP_NONE") != 0:
            problems.append("TRIP_NONE is %r, not 0" % trip.get("TRIP_NONE"))
        for cname, key in TRIP_PAIRS:
            if key not in M.TRIP_KEYS:
                problems.append("TRIP_KEYS has no %r" % key)
            elif cname not in trip:
                problems.append("%s not in machine.h's TripReason" % cname)
            elif trip[cname] != M.TRIP_KEYS.index(key) + 1:
                problems.append("%s: python code %d (%r), cpp %d"
                                % (cname, M.TRIP_KEYS.index(key) + 1, key,
                                   trip[cname]))
        if len(M.TRIP_KEYS) != len(TRIP_PAIRS) or \
                len(trip) != len(TRIP_PAIRS) + 1:
            problems.append("trip reasons: TRIP_KEYS has %d, TripReason %d "
                            "(+ TRIP_NONE), this check pairs %d"
                            % (len(M.TRIP_KEYS), len(trip) - 1,
                               len(TRIP_PAIRS)))
    print("event codes checked  : %d" % (len(REPEAT_NAMES) + len(TRIP_PAIRS)))


def self_test():
    """Item 4 against renumbered copies of machine.h: each must fail."""
    hdr = _read("machine.h")
    fails = []

    def expect(text, want_fail, what):
        probs = []
        check_event_codes(probs, text)
        ok = bool(probs) == want_fail
        print("  %s  %s%s" % ("ok  " if ok else "FAIL", what,
                              (" -> " + probs[0]) if probs else ""))
        if not ok:
            fails.append(what)

    expect(hdr, False, "machine.h as it is passes")
    renum = hdr.replace("REPEAT_COMPLETE = 0,", "REPEAT_COMPLETE = 1,", 1)
    expect(renum, True, "RepeatEnd renumbered from 1 fails")
    ins = hdr.replace("  REPEAT_RESET,", "  REPEAT_INSERTED,\n  REPEAT_RESET,",
                      1)
    expect(ins, True, "a member inserted before REPEAT_RESET fails "
                      "(appended, never inserted)")
    swap = hdr.replace("  TRIP_BMS_STALE,\n  TRIP_CHG_STALE,",
                       "  TRIP_CHG_STALE,\n  TRIP_BMS_STALE,", 1)
    expect(swap, True, "TripReason with BMS_STALE and CHG_STALE swapped "
                       "fails")
    gone = hdr.replace("  TRIP_CELL_UNDERVOLT_RETIRED,\n", "", 1)
    expect(gone, True, "TripReason with the retired slot removed fails "
                       "(every later code shifts)")
    for t in (renum, ins, swap, gone):
        if t == hdr:
            fails.append("a mutation did not apply -- the test is stale")
            print("  FAIL  a mutation did not apply")
    print("\ncheck_port self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def check_payloads(problems):
    cpp = _read("machine.cpp")
    for cname, pyname in (("CONST_03_02_CHARGING", "CONST_03_02_CHARGING"),
                          ("CONST_03_07_CHARGING", "CONST_03_07_CHARGING")):
        m = re.search(r"%s\[8\] = \{(.*?)\};" % cname, cpp, re.S)
        if not m:
            problems.append("%s not found in machine.cpp" % cname)
            continue
        got = bytes(_num(x) for x in m.group(1).split(","))
        want = getattr(M, pyname)
        if got != want:
            problems.append("%s: python=%s cpp=%s"
                            % (cname, want.hex(" "), got.hex(" ")))
    print("payloads checked     : 2")


def check_extractors(problems):
    """Run both implementations' bit extraction over the same random payloads.

    Python is executed directly; the C++ side is re-expressed here from
    machine.h and compared, so a divergence in the *header* is caught even
    though we cannot compile it. This is a transcription check, not a substitute
    for compiling -- it verifies that what machine.h says matches what
    machine.py does.
    """
    import random
    hdr = _read("machine.h")

    # (python fn, C++ body substring that must be present)
    expect = [
        (M.vmax_mv, "(((uint32_t)(d[0] & 0x1Fu) << 8) | d[1])"),
        (M.vmin_mv, "(((uint32_t)(d[2] & 0x1Fu) << 8) | d[3])"),
        (M.tmax_ddegc, "(int32_t)d[4] * 5 - 400"),
        (M.tmin_ddegc, "(int32_t)d[5] * 5 - 400"),
        (M.chg_max_ca, "((((uint32_t)(d[1] & 0x0Fu) << 8) | d[2]) * 25u)"),
        (M.cell_overvolt, "(d[6] >> 7) & 1u"),
        (M.cell_undervolt, "(d[6] >> 6) & 1u"),
        (M.soc_half_pct, "return d[3];"),
        (M.bms_epo, "(d[0] >> 1) & 1u"),
        (M.bms_hvil_mon, "(d[0] >> 3) & 1u"),
        (M.bms_alarm, "(d[1] >> 6) & 3u"),
        (M.bms_chg_done, "(d[6] >> 4) & 1u"),
        (M.mainc_stat_4, "(d[0] >> 2) & 0x0Fu"),
        (M.evap_active, "(d[4] >> 7) & 1u"),
        (M.chg_mux, "return d[0];"),
        (M.chg_state, "return d[5];"),
    ]
    missing = [c for _, c in expect if c not in hdr]
    for c in missing:
        problems.append("extractor expression not found in machine.h: %s" % c)
    print("extractors checked   : %d" % len(expect))

    # ibat and max_avail carry arithmetic worth exercising numerically.
    rng = random.Random(20260909)
    for _ in range(2000):
        d = bytes(rng.randrange(256) for _ in range(8))
        py_ibat = M.ibat_ca(d)
        cpp_ibat = ((d[4] << 8) | d[5]) * 25 // 10 - 100000
        if py_ibat != cpp_ibat:
            problems.append("ibat_ca divergence on %s" % d.hex(" "))
            break
        v = (d[1] << 8) | d[0]
        if v & 0x8000:
            v -= 1 << 16
        if M.max_avail_ca(d) != v * 5:
            problems.append("max_avail_ca divergence on %s" % d.hex(" "))
            break


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test()
    problems = []
    check_config(problems)
    check_identifiers(problems)
    check_payloads(problems)
    check_extractors(problems)
    check_event_codes(problems)
    print("")
    if problems:
        print("FAIL -- %d problem(s):" % len(problems))
        for p in problems:
            print("  %s" % p)
        return 1
    print("OK -- machine.cpp/.h agree with machine.py on every checked constant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
