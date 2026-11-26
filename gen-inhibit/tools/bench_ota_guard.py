#!/usr/bin/env python3
"""Spec 12.4 row 17, bench half: OTA is refused while armed, in any non-OFF mode.

    py -3.11 bench_ota_guard.py --host 192.168.80.1
    py -3.11 bench_ota_guard.py --host 192.168.80.1 --allow-ota-write

WHAT IT CHECKS. `POST /upload/ota.bin` must return **403** whenever
`gen_inhibit` holds the bus, which is every mode but OFF
(`gen_inhibit_owns_bus()` is `mode != GI_OFF`). The guard is at
`config_server.c:1016` and exists for two reasons, both real: reflashing
underneath an armed transmitter on a live powertrain bus is never wanted, and
`can_disable()` further down calls `twai_driver_uninstall()`, which would tear
the driver out from under a worker parked inside `twai_receive()`.

THE ACCEPTANCE HALF IS OPT-IN, and deliberately so. Proving "accepted when OFF"
means actually posting to the OTA endpoint, which calls `can_disable()` and
writes flash on a device with **no USB port and no factory reset**. So the
default run checks only the refusals -- the safety property -- and
`--allow-ota-write` is needed for the other half. Even then this sends a
deliberately invalid image and asserts the status is *not* 403: that
distinguishes "the guard let it through" from "the guard stopped it" without
completing a flash.

PRECONDITIONS ARE CHECKED AND WILL REFUSE THE RUN. Two matter:

  flight config   the device must be in `wifi_mode = AP`. Spec section 8 makes
                  that permanent -- the APStation reconnect path calls
                  `esp_wifi_stop()`/`start()`, which has no place on a device
                  transmitting to a live bus. A run against an APStation device
                  is not a run against the build that flies, so it is refused
                  rather than warned about.

  a second route  reaching an AP-only device means associating this laptop's
                  WiFi with the WiCAN, which costs its internet unless another
                  interface carries the default route. Checked by EFFECT --
                  an actual connection to an outside host -- not by reading a
                  routing table and hoping.

This needs the bench. It has not been run against hardware yet; nothing below
has been verified against a device.

THE HTTP HELPERS ARE THE PINNED CORE'S (port step 4 item 2, 2026-11-25).
http, have_external_route, wifi_mode, ap_mode_detail, gi_status, set_mode,
device_identity, runs_dir, MODES, TIMEOUT and RUNS_ENV moved to shared/bench's
vtrux_bench/run/wican_http.py (FLASH-SPEC, "What moves, beside the flasher"),
and this file takes them from the shared/bench checkout gen-inhibit pins
(gi_core_pin), never from PYTHONPATH. They keep their names here, so
bench_flash_guard.py and ota_guard_raw_probe.py import them from this file as
before. set_mode's form stays this guard's own: `mode=<m>&offset=500`, the
core's default. The guard checks below stay gen-inhibit's.
"""

import argparse
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def core_pin():
    """gi_core_pin: the one already imported, else loaded by file from bench/ beside
    this file."""
    mod = sys.modules.get("gi_core_pin")
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            "gi_core_pin", os.path.join(HERE, "bench", "gi_core_pin.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gi_core_pin"] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("gi_core_pin", None)
            raise
    return mod


def wican_http():
    """The pinned core's vtrux_bench.run.wican_http. A pin that fails its check stops
    the run here, before the device is touched (exit 2, the refusal printed)."""
    pin = core_pin()
    try:
        pin.use_bench()
    except pin.Refused as e:
        print("REFUSED: the pinned core could not be used (gi_core_pin): %s" % e)
        sys.exit(2)
    from vtrux_bench.run import wican_http as WH
    return WH


_WH = wican_http()
http = _WH.http
have_external_route = _WH.have_external_route
wifi_mode = _WH.wifi_mode
ap_mode_detail = _WH.ap_mode_detail
gi_status = _WH.gi_status
set_mode = _WH.set_mode
device_identity = _WH.device_identity
runs_dir = _WH.runs_dir
MODES = _WH.MODES
TIMEOUT = _WH.TIMEOUT
RUNS_ENV = _WH.RUNS_ENV


def multipart_bogus():
    """A deliberately invalid OTA body.

    Enough to reach the handler and be rejected on its merits, and nothing like
    a valid image -- so a device that accepts the POST does not end up running
    it. The point of the acceptance half is only to show the status is not 403.
    """
    boundary = "----vtruxbenchboundary"
    body = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="file"; filename="ota.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        % boundary
    ).encode() + b"NOT_A_VALID_ESP_IMAGE" + ("\r\n--%s--\r\n" % boundary).encode()
    return body, "multipart/form-data; boundary=%s" % boundary


def _write_record(path, rec):
    """Write a timestamped copy AND refresh the stable name.

    One file per run, because a fixed filename means each run erases the last --
    and the run that mattered was an OTA probe that returned a connection reset
    instead of a 403, which the next clean run overwrote.
    """
    import errno
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    root, ext = os.path.splitext(os.path.abspath(path))
    wrote = []
    for p in ("%s_%s%s" % (root, stamp, ext), root + ext):
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                json.dump(rec, fh, indent=1)
            wrote.append(os.path.basename(p))
        except OSError as e:
            if e.errno != errno.ENOENT:
                print("could not write %s: %s" % (p, e))
    return wrote


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True,
                    help="the WiCAN's address, e.g. 192.168.80.1 in AP mode")
    ap.add_argument("--allow-ota-write", action="store_true",
                    help="also check that OFF ACCEPTS an upload. This posts to "
                         "the OTA endpoint on a device with no USB port and no "
                         "factory reset. Off by default.")
    ap.add_argument("--skip-route-check", action="store_true",
                    help="proceed without a second route to the internet")
    args = ap.parse_args()
    out = os.path.join(runs_dir(), "bench_ota_guard.json")

    fails = []
    print("=== preconditions ===")

    ok, local = have_external_route()
    if ok:
        print("[  ok  ] internet reachable, local address %s" % local)
    elif args.skip_route_check:
        print("[ warn ] no internet, and --skip-route-check was given")
    else:
        print("[ FAIL ] no route to the internet. Associating WiFi with an "
              "AP-only WiCAN will leave this machine offline for the whole "
              "run. Connect Ethernet first, or pass --skip-route-check if "
              "that is genuinely fine.")
        return 2

    mode_value, err = wifi_mode(args.host)
    ap_ok, detail = ap_mode_detail(mode_value, err)
    print("[%s] device is in AP mode: %s" % ("  ok  " if ap_ok else " FAIL ", detail))
    if not ap_ok:
        return 2

    st, _ = gi_status(args.host)
    if st is None:
        print("[ FAIL ] no /gen_inhibit endpoint -- is this the inhibitor build?")
        return 2
    print("[  ok  ] inhibitor build present, mode now %s"
          % MODES.get(st.get("mode"), st.get("mode")))

    body, ctype = multipart_bogus()

    # THE ROWS, so the run leaves evidence rather than a claim.
    rows = []

    print()
    print("=== OTA must be refused in every non-OFF mode ===")
    for mode in (1, 2, 3, 4):
        code, _ = set_mode(args.host, mode)
        if code != 200:
            fails.append("could not set mode %d (%s): %s"
                         % (mode, MODES[mode], code))
            print("[ FAIL ] %-8s could not be set (%s)" % (MODES[mode], code))
            continue

        got, _ = gi_status(args.host)
        if not got or got.get("mode") != mode:
            fails.append("mode %d did not take effect" % mode)
            print("[ FAIL ] %-8s did not take effect" % MODES[mode])
            continue

        status, text = http(args.host, "/upload/ota.bin", method="POST",
                            body=body, ctype=ctype)
        rows.append({"mode": MODES[mode], "endpoint": "/upload/ota.bin",
                     "status": status, "body": (text or "")[:120],
                     "ok": status == 403})
        if status == 403:
            print("[  ok  ] %-8s OTA refused with 403" % MODES[mode])
        else:
            fails.append("mode %s: OTA returned %s, not 403" % (MODES[mode], status))
            print("[ FAIL ] %-8s OTA returned %s, not 403 -- %s"
                  % (MODES[mode], status, text[:80]))

    # Always leave the device disarmed, whatever happened above.
    set_mode(args.host, 0)

    # The record goes to the runs folder (runs_dir), resolved at the start.
    rec = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
           # A record that cannot name the image it was taken on is weak
           # evidence; /gen_inhibit's elf_sha256 names it.
           "device": device_identity(args.host),
           "wifi_mode": mode_value,
           "host": args.host,
           "checked_accepted_when_off": bool(args.allow_ota_write),
           "rows": rows}
    names = _write_record(out, rec)
    print("wrote %s (%d rows)" % (", ".join(names) or "nothing", len(rows)))

    print()
    print("=== OTA is accepted when OFF ===")
    if not args.allow_ota_write:
        print("[ SKIP ] not checked: this posts to the OTA endpoint and writes "
              "flash on a device with no USB port and no factory reset. Pass "
              "--allow-ota-write to include it. The refusals above are the "
              "safety property; this half is completeness.")
    else:
        got, _ = gi_status(args.host)
        if not got or got.get("mode") != 0:
            fails.append("could not return the device to OFF")
            print("[ FAIL ] device is not OFF, refusing to post")
        else:
            status, text = http(args.host, "/upload/ota.bin", method="POST",
                                body=body, ctype=ctype)
            if status == 403:
                fails.append("OFF refused the OTA with 403 -- the guard is "
                             "firing when it should not")
                print("[ FAIL ] OFF returned 403; the guard fires when "
                      "disarmed, so OTA is unreachable")
            else:
                print("[  ok  ] OFF did not return 403 (got %s) -- the guard is "
                      "not what stops an upload when disarmed. The image sent "
                      "was invalid on purpose, so a rejection for any OTHER "
                      "reason is the expected outcome." % status)

    print()
    if fails:
        print("%d failure(s):" % len(fails))
        for f in fails:
            print("  " + f)
        return 1
    print("the OTA guard holds in every non-OFF mode")
    return 0


if __name__ == "__main__":
    sys.exit(main())
