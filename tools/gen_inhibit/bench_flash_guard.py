#!/usr/bin/env python3
"""Spec 5.1 item 3, bench half: every flash writer is refused while armed.

    py -3.11 bench_flash_guard.py --host 192.168.80.1
    py -3.11 bench_flash_guard.py --host 192.168.80.1 --check-accepted

WHAT AND WHY. `CONFIG_TWAI_ISR_IN_IRAM` is deliberately unset (spec 5), so while
the flash cache is disabled for a write the TWAI interrupt cannot run at all and
the controller's small hardware FIFO -- a few frames, ~2 ms at truck load -- is
the only buffer. A deeper software receive queue does not help, because nothing is
moving frames into it. That is why spec 9.3 refuses OTA while armed, and the audit
of 2026-09-26 found OTA is not the only flash writer: five HTTP handlers write
SPIFFS files. Spec 5.1 item 3 requires all five refused the 9.3 way, with a 403
naming the reason.

`bench_ota_guard.py` covers the OTA endpoint. This covers the other five, which
had a source-level check (`config_vs_spec`) and nothing against hardware.

THE REFUSAL IS THE SAFETY PROPERTY AND IS ALL THE DEFAULT RUN CHECKS. The gate is
`gen_inhibit_owns_bus()`, i.e. any mode but OFF, so each endpoint is tried in all
four non-OFF modes.

THE "ACCEPTED WHEN OFF" HALF IS OPT-IN, AND USES ONLY PROBES THAT WRITE NOTHING.
It did not, once, and the cost is worth recording. The first version posted a
deliberately invalid body on the stated expectation that a handler would "reject
it on its merits". `store_config_handler` VALIDATES NOTHING: it writes the request
body verbatim to `/spiffs/config.json` and reboots. So config.json became the
bytes `not-json-at-all`, the device fell back to `device_config_default`, its AP
passphrase became the firmware default, and bench access was gone until the config
was restored from `wican-config/backup/`. On a device with no USB port and no
factory reset that was a poor thing to assume.

What the handlers actually do, now read rather than assumed:

  store_config     `if (buf_size <= 0) return ESP_FAIL;` BEFORE any fopen, so a
  store_canflt     zero-length POST reaches the handler and writes nothing. These
  store_auto_data  three are probed, with an empty body.

  store_car_data   `fopen(filepath, "w")` is the FIRST statement after the guard,
                   before any validation -- so ANY request past the guard
                   truncates car_data.json, a zero-length one included.

  upload_car_data  Rejects an oversized Content-Length before fopen, so only a
                   >2 MB body would be safe. Anything smaller reaches fopen.

Those last two are SKIPPED, and that is a stated limitation rather than a hidden
one. All five share the one gate (`flash_write_refused`), and all five are covered
by the refusal half; showing on three that the 403 is conditional on the mode is
what rules out an unconditional refusal.

WHY NOT-403 IS THE RIGHT ASSERTION, and not "200". 403 is the guard's own answer,
produced before the handler looks at the body at all. Any other status -- or even
a closed connection, which is what `return ESP_FAIL` without a response produces
-- means execution reached the handler, which is exactly the claim. Asserting 200
would require a valid body, which is the thing that must not be sent.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Results belong in the bench's runs folder, not next to the tool:
# bench_ota_guard.runs_dir() ($GI_RUNS_DIR once this folder is in the repo).
from bench_ota_guard import (            # noqa: E402
    MODES, gi_status, have_external_route, http, runs_dir, set_mode,
)

# The five handlers spec 5.1 item 3 names, with the method each expects and a
# body that no handler can accept. `config_vs_spec.py` checks the source side;
# this checks the device.
#
# The bodies are junk ON PURPOSE -- see the header. Each is well-formed HTTP and
# nonsense to the handler.
# The five handlers spec 5.1 item 3 names. `off_safe` says whether the endpoint
# can be probed when OFF without writing: see the header for what each handler
# does before it opens the file. An empty body is the probe, because these three
# check the content length before any fopen.
WRITERS = (
    ("/store_config",        "POST", b"", True),
    ("/store_canflt",        "POST", b"", True),
    ("/store_auto_data",     "POST", b"", True),
    # fopen(..., "w") before any validation: truncates on ANY request.
    ("/store_car_data",      "POST", b"", False),
    # Takes its destination from the URI, like the OTA handler. Only a >2 MB
    # body is rejected before fopen, so there is no cheap safe probe.
    ("/upload/car_data.json", "POST", b"", False),
)

fails = []

# Every row, so the run leaves evidence rather than a claim. Reviewed 2026-09-26:
# "20 of 20 refused" existed only in a chat message, which is not a record.
records = []


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


def say(ok, name, detail="", **extra):
    print("[%s] %-46s %s" % ("  ok  " if ok else " FAIL ", name, detail))
    rec = {"check": name, "ok": bool(ok), "detail": detail}
    rec.update(extra)
    records.append(rec)
    if not ok:
        fails.append(name)


def preconditions(host):
    print("=== preconditions ===")
    ok, detail = have_external_route()
    say(ok, "internet reachable", detail)

    page, err = gi_status(host, strict=False)
    if page is None:
        say(False, "gen_inhibit answers", err or "")
        return False
    if page.get("wifi_mode") not in (None, "AP"):
        say(False, "device is in AP mode",
            "wifi_mode=%r -- spec 8 makes AP permanent for the build that flies"
            % page.get("wifi_mode"))
        return False
    say(True, "device is in AP mode", "the configuration that flies")
    say(True, "inhibitor build present", "mode now %s"
        % MODES.get(page.get("mode"), page.get("mode")))
    return True


def refusals(host):
    print("\n=== every flash writer must be refused in every non-OFF mode ===")
    for mode in (1, 2, 3, 4):
        st, _ = set_mode(host, mode)
        page, _ = gi_status(host, strict=False)
        got = (page or {}).get("mode")
        if got != mode:
            say(False, "arm %s" % MODES[mode],
                "asked for mode %d, device reports %r" % (mode, got))
            continue
        for path, method, body, _off_safe in WRITERS:
            code, txt = http(host, path, method=method, body=body)
            detail = "HTTP %s" % code
            rec_extra = {"mode": MODES[mode], "endpoint": path,
                         "status": code, "body": (txt or "")[:120]}
            if code == 403:
                # The 403 must name the reason, per spec 5.1 item 3's "the 9.3
                # way". A bare 403 from some other layer would read the same.
                named = "gen_inhibit" in (txt or "")
                say(named, "%-8s %s refused with 403" % (MODES[mode], path),
                    "" if named else "403 but the body does not name "
                                     "gen_inhibit: %r" % (txt or "")[:80],
                    **rec_extra)
            else:
                say(False, "%-8s %s refused" % (MODES[mode], path),
                    "%s -- a flash write was NOT refused while armed" % detail,
                    **rec_extra)
    set_mode(host, 0)


def accepted_when_off(host):
    print("\n=== and reached when OFF (invalid bodies; 403 is the only failure) ===")
    set_mode(host, 0)
    page, _ = gi_status(host, strict=False)
    if (page or {}).get("mode") != 0:
        say(False, "device is OFF for the acceptance half",
            "mode is %r" % (page or {}).get("mode"))
        return
    for path, method, body, off_safe in WRITERS:
        if not off_safe:
            print("[ SKIP ] %-46s %s" % (path, "no probe that writes nothing "
                                               "-- see the header"))
            continue
        code, _ = http(host, path, method=method, body=body)

        # A CLOSED CONNECTION COUNTS AS REACHED ONLY IF THE DEVICE IS STILL
        # THERE. These handlers answer a zero-length body with `return ESP_FAIL`
        # and no response, so httpd drops the socket and the status is None --
        # but an unreachable device gives None too, and a row that passes on
        # either cannot fail for the reason it exists. So None is followed by a
        # liveness probe, and the row stands or falls on that.
        alive = None
        if code is None:
            alive = http(host, "/check_status")[0] == 200
            if not alive:
                say(False, "%s reached the handler when OFF" % path,
                    "no response, AND the device stopped answering -- so this "
                    "says nothing about the handler",
                    endpoint=path, status=code, device_alive=False)
                continue

        detail = "HTTP %s" % code
        if code is None:
            detail = ("no response, device still answering -- the handler "
                      "closed the socket, which is `return ESP_FAIL` before any "
                      "write")
        elif code == 403:
            detail += (" -- still refused while OFF, so the guard is "
                       "unconditional rather than mode-gated")
        say(code != 403, "%s reached the handler when OFF" % path, detail,
            endpoint=path, status=code, device_alive=alive)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="192.168.80.1")
    ap.add_argument("--out", default=None,
                    help="where to write the per-row record (default: "
                         "bench_flash_guard.json in the runs folder, $GI_RUNS_DIR)")
    ap.add_argument("--check-accepted", action="store_true",
                    help="also check each endpoint is REACHED when OFF. Sends "
                         "invalid bodies only; see the header for why a valid "
                         "one must never be sent to /store_config")
    args = ap.parse_args()
    if args.out is None:
        args.out = os.path.join(runs_dir(), "bench_flash_guard.json")

    if not preconditions(args.host):
        print("\nPRECONDITIONS FAILED, nothing was exercised.")
        return 2

    refusals(args.host)
    if args.check_accepted:
        accepted_when_off(args.host)
    else:
        print("\n[ SKIP ] the accepted-when-OFF half: pass --check-accepted. "
              "The refusals above are the safety property.")

    # THE VERSION IS ON /check_status, not on the inhibitor's page. The first
    # version of this record wrote device=None, and a result that cannot name the
    # image it was taken on is not much of a record.
    ver = None
    st_code, st_txt = http(args.host, "/check_status")
    if st_code == 200:
        try:
            ver = json.loads(st_txt).get("git_version")
        except ValueError:
            ver = None
    out = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "device": ver,
           "host": args.host,
           "checked_accepted_when_off": bool(args.check_accepted),
           "rows": records,
           "failed": fails}
    names = _write_record(args.out, out)
    print("\nwrote %s (%d rows)" % (", ".join(names) or "nothing", len(records)))

    print()
    if fails:
        print("%d FAILED: %s" % (len(fails), ", ".join(fails[:6])))
        return 1
    print("spec 5.1 item 3 holds on the device: every flash writer is refused "
          "while gen_inhibit owns the bus, and each 403 names the reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
