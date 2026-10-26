"""Harness test for batt_voltage_log.extract(): keeps only the two fields, parses the volts.

A status page carrying any other key (credentials, SSIDs, URLs) must not leak
into the record, and "14.1V" must read as 14.1. Fails if extract() passes
through another key or misreads the voltage.

  py -3.14 tools/gen_inhibit/bench/test_batt_voltage_log.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("BATT_DIR") or HERE)        # the mutants run another copy (BATT_DIR)
import batt_voltage_log as B                                    # noqa: E402

FAKE = {"git_version": "a71b90a", "batt_voltage": "14.1V", "ssid": "SECRET-SSID",
        "batt_alert_pass": "SECRET-PASS", "batt_mqtt_user": "SECRET-USER",
        "batt_alert_url": "mqtt://SECRET-HOST", "sleep_volt": "13.1"}


def parked():
    """PARKED (gen-inhibit reviewer, 2026-10-25): main() refuses before any request and writes
    nothing, saying why. A fake lowid_flood_skip_bench records any request; RUNS points at an
    empty scratch folder."""
    import contextlib
    import io
    import shutil
    import types
    import gi_paths
    asked = []
    fake = types.ModuleType("lowid_flood_skip_bench")
    fake.dev_get = lambda path, timeout=8.0: asked.append(path) or '{"batt_voltage": "14.1V"}'
    saved, real_runs = sys.modules.get("lowid_flood_skip_bench"), B.RUNS
    runs = gi_paths.scratch("batt-voltage-parked")
    shutil.rmtree(runs, ignore_errors=True)
    os.makedirs(runs)
    sys.modules["lowid_flood_skip_bench"] = fake
    B.RUNS = runs
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = B.main()
    except BaseException as e:      # noqa: BLE001  a raise is this check's FAIL
        rc = "raised %s" % type(e).__name__
    finally:
        B.RUNS = real_runs
        if saved is None:
            sys.modules.pop("lowid_flood_skip_bench", None)
        else:
            sys.modules["lowid_flood_skip_bench"] = saved
    written = os.listdir(runs)
    shutil.rmtree(runs, ignore_errors=True)
    text = out.getvalue()
    return [
        ("parked: main() refuses (exit 2)", rc == 2),
        ("parked: no request made", asked == []),
        ("parked: nothing written", written == []),
        ("parked: the refusal names the reason (the factory reset, /check_status)",
         "REFUSED" in text and "factory reset" in text and "/check_status" in text),
    ]


def main():
    fails = []
    r = B.extract(FAKE)
    checks = [
        ("only git_version, batt_voltage, batt_v kept",
         set(r) == {"git_version", "batt_voltage", "batt_v"}),
        ("no SECRET value in the record", "SECRET" not in repr(r)),
        ("14.1V -> 14.1", r["batt_v"] == 14.1),
        ("'12V' -> 12.0", B.extract({"batt_voltage": "12V"})["batt_v"] == 12.0),
        ("'13.85' (no unit) -> 13.85", B.extract({"batt_voltage": "13.85"})["batt_v"] == 13.85),
        ("missing -> None", B.extract({})["batt_v"] is None),
        ("'0.0V' (no reading) -> None, raw kept",
         B.extract({"batt_voltage": "0.0V"})["batt_v"] is None
         and B.extract({"batt_voltage": "0.0V"})["batt_voltage"] == "0.0V"),
        ("'0.2V' stays 0.2 (a real low reading)", B.extract({"batt_voltage": "0.2V"})["batt_v"] == 0.2),
        ("garbage -> None", B.extract({"batt_voltage": "n/a"})["batt_v"] is None),
    ]
    checks += parked()
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
