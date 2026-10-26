"""Log the WiCAN's battery-sense voltage before/after a run, and nothing else.

Gen-inhibit tester, 2026-10-09, for the 12 V runs (the reviewer asked for
batt_voltage before and after every run). /check_status also carries stock
credentials and the device's network config, so extract() keeps ONLY
git_version and batt_voltage; nothing else from that page is written or
printed.

What a reading can and cannot show: it is the voltage at the OBD connector's
battery pin, not the USB rail; it comes in 0.1 V steps and is a spot sample,
so a sag between two reads is invisible; and a reading can repeat a stale
last value with nothing to mark it. "0.0V" means no reading (batt_v None,
batt_voltage kept as sent). Good for before/after, not for catching a reset.

    py -3.14 tools/gen_inhibit/bench/batt_voltage_log.py <label>
Appends one JSON line to runs/batt_voltage_log_<YYYYMMDD>.jsonl and prints it.

PARKED since 2026-10-25 (gen-inhibit reviewer): main() refuses at start with the reason and
requests nothing, because batt_voltage is on /check_status only and that page is not read
until the WiCAN's factory reset (reviewer, 2026-10-20).
"""
import json
import os
import re
import sys
import time
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RUNS = gi_paths.runs()
KEEP = ("git_version", "batt_voltage")


def extract(status):
    """{git_version, batt_voltage (as sent), batt_v (float or None)} -- only these."""
    out = {k: status.get(k) for k in KEEP}
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*V?\s*", str(status.get("batt_voltage") or ""))
    v = float(m.group(1)) if m else None
    # A device that just answered the page is not at 0 V: "0.0V" is the page's
    # "no reading yet" (the implementor, 2026-10-09), so it is not a voltage.
    out["batt_v"] = None if v == 0.0 else v
    return out


PARKED = ("batt_voltage is on /check_status only, and no /check_status request is made until the WiCAN's "
          "factory reset (reviewer, 2026-10-20); parked, not retired (gen-inhibit reviewer, 2026-10-25)")


def main():
    """PARKED: refuses before any request, and writes nothing. read_and_log() is the tool as
    it ran on 2026-10-09; main() calls it again once the bar is lifted."""
    print("REFUSED: batt_voltage_log is parked: %s. Nothing was requested or written." % PARKED)
    return 2


def read_and_log():
    """The tool as it ran until 2026-10-25. Not called while parked."""
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    import lowid_flood_skip_bench as LF
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": label}
    try:
        raw = LF.dev_get("/check_status")
        rec.update(extract(json.loads(raw)))
    except Exception as e:                          # unreachable device is a reading too
        rec["error"] = type(e).__name__
    line = json.dumps(rec)
    path = os.path.join(RUNS, "batt_voltage_log_%s.jsonl" % time.strftime("%Y%m%d"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
