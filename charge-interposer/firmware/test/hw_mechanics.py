"""Has this change touched a hardware mechanic? (spec 10, spec 12)

Spec 10 lists the things no amount of emulation can answer -- flashing and
boot, the real transceivers and the controllers' own behaviour, throughput
at real bus load, reset and brownout, the board offline, the charger's
acknowledgement while silent. A change that touches any of them needs a
bench or truck run whatever the host tests show. Nothing classified a change
against that list, which is the spec 12 entry this closes.

**This does not replace the written procedure** in `../README.md`, and it
cannot: a change anywhere can alter timing, and only a person walking the
list can say so. What it does is make one specific failure impossible --
"the driver changed and nobody noticed". That is the one worth automating,
because it is silent and because the files it concerns are rarely the files
a change is *about*.

Two kinds of entry:

* **whole files** whose content is a hardware mechanic by construction (the
  port drivers, the port interface, the build configuration);
* **named settings** inside files that are mostly not. Hashing all of
  `main.cpp` would flag every edit to the bridge loop's logging, so the pin
  assignments, the bitrate, the loop timings and the per-iteration drain
  bounds are extracted by name and only their VALUES are compared;
* **function bodies** that are a mechanic whole. `setup()` is the boot
  path -- init order, the controller begin calls, the B-7d failed-start
  branch -- so all of it counts, minus the `INTP_PRINT*` lines, which are
  output rather than behaviour.

This folder is not under git, so the baseline is a manifest recorded at each
truck flash.

Usage (from the repo root):
    py -3.11 .../test/hw_mechanics.py                 compare against the latest manifest
    py -3.11 .../test/hw_mechanics.py --record 2026-10-04-truck
    py -3.11 .../test/hw_mechanics.py --list          show what is mapped, and to which item
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.normpath(os.path.join(_HERE, ".."))
MANIFESTS = os.path.normpath(os.path.join(
    FW, "..", "..", "..", "notes", "artifacts", "interposer-firmware",
    "flash-manifests"))

# path -> the spec 10 item it is a mechanic for.
WHOLE_FILES = {
    "src/port_twai.cpp": "the CAN controllers' own behaviour (TWAI bus-off "
                         "recovery); the real transceivers",
    "src/port_twai.h": "same",
    "src/port_mcp2515.cpp": "the CAN controllers' own behaviour (MCP2515 "
                            "transmit-buffer abort, retry and ordering)",
    "src/port_mcp2515.h": "same",
    "src/can_port.h": "the port contract both drivers implement",
    "platformio.ini": "flashing and boot; the build configuration",
}

# (file, label, regex) -> the spec 10 item. The regex must capture the value.
SETTINGS = [
    ("src/main.cpp", "CAN1_TX_GPIO",
     r"CAN1_TX_GPIO\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "CAN1_RX_GPIO",
     r"CAN1_RX_GPIO\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "MCP_CS", r"MCP_CS\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "MCP_SCK", r"MCP_SCK\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "MCP_MISO", r"MCP_MISO\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "MCP_MOSI", r"MCP_MOSI\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "MCP_INT", r"MCP_INT\s*=\s*(\d+)", "pin assignment"),
    ("src/main.cpp", "BITRATE",
     r"BITRATE\s*=\s*(\d+)UL", "the bus the board drives"),
    ("src/main.cpp", "TICK_INTERVAL_MS",
     r"TICK_INTERVAL_MS\s*=\s*(\d+)", "bridge loop timing"),
    ("src/main.cpp", "DIAG_INTERVAL_MS",
     r"DIAG_INTERVAL_MS\s*=\s*(\d+)", "bridge loop timing"),
    ("src/main.cpp", "STATUS_INTERVAL_MS",
     r"STATUS_INTERVAL_MS\s*=\s*(\d+)", "bridge loop timing"),
    ("src/main.cpp", "vehicle drain bound",
     r"i\s*<\s*(\d+)\s*&&\s*vehicle\.receive", "throughput at real bus load"),
    ("src/main.cpp", "charger drain bound",
     r"i\s*<\s*(\d+)\s*&&\s*charger\.receive", "throughput at real bus load"),
]


# (file, function, spec 10 item). The whole body is watched, with the
# INTP_PRINT* lines dropped.
FUNCTIONS = [
    ("src/main.cpp", "setup",
     "flashing and boot: init order, the controller begin calls, and the "
     "B-7d failed-start path"),
]


def extract_body(text, signature):
    """The body of `signature` by brace matching, or None.

    Braces inside strings, chars and comments do not count. A naive counter
    happens to work on today's main.cpp and would break silently on the
    first message containing a brace, which is exactly the kind of quiet
    wrong answer this script exists to avoid.
    """
    start = text.find(signature)
    if start < 0:
        return None
    i = text.find("{", start)
    if i < 0:
        return None
    depth = 0
    in_str = in_chr = in_line = in_blk = False
    body_start = i
    while i < len(text):
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_line:
            if c == "\n":
                in_line = False
        elif in_blk:
            if c == "*" and nxt == "/":
                in_blk = False
                i += 1
        elif in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif in_chr:
            if c == "\\":
                i += 1
            elif c == "'":
                in_chr = False
        elif c == "/" and nxt == "/":
            in_line = True
            i += 1
        elif c == "/" and nxt == "*":
            in_blk = True
            i += 1
        elif c == '"':
            in_str = True
        elif c == "'":
            in_chr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:i + 1]
        i += 1
    return None


def normalise_body(body):
    """Drop the output lines and the incidental whitespace."""
    out = []
    for line in body.splitlines():
        if "INTP_PRINT" in line:
            continue
        line = " ".join(line.split())
        if line:
            out.append(line)
    return "\n".join(out)


def _read(rel):
    with io.open(os.path.join(FW, rel), "r", encoding="utf-8") as f:
        return f.read()


def collect():
    """-> ({key: hash_or_value}, [problems])

    A mapped entry that cannot be found is a PROBLEM, not a skip. A renamed
    file or a reworded constant would otherwise drop silently out of the map
    and the script would keep reporting "nothing touched" forever -- the
    exact shape of failure it exists to prevent.
    """
    state, problems = {}, []
    for rel, why in sorted(WHOLE_FILES.items()):
        path = os.path.join(FW, rel)
        if not os.path.exists(path):
            problems.append("mapped file is missing: %s (%s)" % (rel, why))
            continue
        with open(path, "rb") as f:
            state["file:" + rel] = hashlib.sha256(f.read()).hexdigest()[:16]
    for rel, fn, why in FUNCTIONS:
        try:
            text = _read(rel)
        except OSError:
            problems.append("mapped file is missing: %s" % rel)
            continue
        body = extract_body(text, "void %s()" % fn)
        if body is None:
            problems.append(
                "mapped function %s() not found in %s -- renamed or "
                "restructured, so this mechanic (%s) is NOT being watched"
                % (fn, rel, why))
            continue
        digest = hashlib.sha256(
            normalise_body(body).encode("utf-8")).hexdigest()[:16]
        state["fn:%s:%s" % (rel, fn)] = digest
    for rel, label, pattern, why in SETTINGS:
        try:
            text = _read(rel)
        except OSError:
            problems.append("mapped file is missing: %s" % rel)
            continue
        found = re.findall(pattern, text)
        if not found:
            problems.append(
                "mapped setting %r not found in %s -- the pattern no longer "
                "matches, so this mechanic (%s) is NOT being watched"
                % (label, rel, why))
            continue
        state["set:%s:%s" % (rel, label)] = ",".join(found)
    return state, problems


def latest_manifest():
    if not os.path.isdir(MANIFESTS):
        return None, None
    names = sorted(n for n in os.listdir(MANIFESTS) if n.endswith(".json"))
    if not names:
        return None, None
    path = os.path.join(MANIFESTS, names[-1])
    with io.open(path, "r", encoding="utf-8") as f:
        return names[-1], json.load(f)


def why_for(key):
    if key.startswith("file:"):
        return WHOLE_FILES.get(key[5:], "?")
    for rel, fn, why in FUNCTIONS:
        if key == "fn:%s:%s" % (rel, fn):
            return why
    for rel, label, _p, why in SETTINGS:
        if key == "set:%s:%s" % (rel, label):
            return why
    return "?"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", metavar="LABEL",
                    help="write a manifest for the image about to be "
                         "flashed, e.g. 2026-10-04-truck")
    ap.add_argument("--list", action="store_true",
                    help="show what is mapped and to which spec 10 item")
    a = ap.parse_args()

    if a.list:
        print("Whole files (any change is a hardware mechanic):")
        for rel, why in sorted(WHOLE_FILES.items()):
            print("  %-26s %s" % (rel, why))
        print("\nFunction bodies (all of it, minus the INTP_PRINT* lines):")
        for rel, fn, why in FUNCTIONS:
            print("  %-26s %-22s %s" % (rel, fn + "()", why))
        print("\nNamed settings (only the value is watched):")
        for rel, label, _p, why in SETTINGS:
            print("  %-26s %-22s %s" % (rel, label, why))
        return 0

    state, problems = collect()
    if problems:
        print("FAIL -- the map itself is broken, so a clean result would "
              "mean nothing:")
        for p in problems:
            print("  - " + p)
        return 2

    if a.record:
        if not os.path.isdir(MANIFESTS):
            os.makedirs(MANIFESTS)
        path = os.path.join(MANIFESTS, "%s.json" % a.record)
        if os.path.exists(path):
            print("FAIL -- %s already exists; manifests are a record of what "
                  "was flashed and are not overwritten" % path)
            return 2
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"label": a.record,
                       "recorded": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "state": state}, f, indent=2, sort_keys=True)
            f.write("\n")
        print("recorded %d watched items to %s" % (len(state), path))
        return 0

    name, man = latest_manifest()
    if man is None:
        print("No manifest yet. The baseline is recorded at a truck flash:")
        print("    py -3.11 %s --record <label>"
              % os.path.relpath(__file__))
        print("\nUntil then this cannot say whether anything changed, and "
              "the written procedure in firmware/README.md is the only "
              "classification there is.")
        return 1

    old = man.get("state", {})
    changed = [k for k in sorted(set(old) | set(state))
               if old.get(k) != state.get(k)]
    print("baseline: %s (%s, %d items)"
          % (name, man.get("recorded", "?"), len(old)))
    if not changed:
        print("\nNo mapped hardware mechanic has changed since that flash.")
        print("The written procedure still applies: a change elsewhere can "
              "alter timing, and only walking spec 10 can say so.")
        return 0
    print("\n%d mapped item(s) changed -- this needs a bench or truck run, "
          "whatever the host tests show:" % len(changed))
    for k in changed:
        print("  - %s\n      %s" % (k, why_for(k)))
        if k in old and k in state and k.startswith("set:"):
            print("      was %r, now %r" % (old[k], state[k]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
