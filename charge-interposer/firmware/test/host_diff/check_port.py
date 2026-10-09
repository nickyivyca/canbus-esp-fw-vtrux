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

Usage (from the repo root):
    py -3.12 projects/vtrux/tools/interposer/firmware/test/host_diff/check_port.py
"""

import os
import re
import sys

sys.path.insert(0, ".")

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
    problems = []
    check_config(problems)
    check_identifiers(problems)
    check_payloads(problems)
    check_extractors(problems)
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
