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
"""

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10

MODES = {
    0: "OFF",
    1: "OBSERVE",
    2: "RESPOND",
    3: "INHIBIT",
    4: "PASSIVE",
}


def http(host, path, method="GET", body=None, ctype=None):
    """(status, text). A 4xx/5xx comes back as a status, not an exception."""
    url = "http://%s%s" % (host, path)
    req = urllib.request.Request(url, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, ssl.SSLError) as e:
        return None, str(e)
    except OSError as e:
        # A RESET IS NOT AN EXCEPTION THIS MAY LEAK, and it did on 2026-09-26.
        # A malformed OTA upload made the device close the connection, raising
        # ConnectionResetError -- which is an OSError but NOT a URLError, so it
        # escaped this function, aborted the caller mid-run, and skipped every
        # verification step that would have said what state the device was in.
        # A transport failure has to come back as "no status", like the others.
        return None, "%s: %s" % (type(e).__name__, e)


def have_external_route():
    """Is there internet right now, and not via the WiCAN?

    Checked by making a connection rather than by parsing `route print`. A
    routing table can look right while nothing works, and the thing that
    matters here is whether the operator still has connectivity after WiFi is
    pointed at the device.
    """
    for hostport in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            s = socket.create_connection(hostport, timeout=4)
            local = s.getsockname()[0]
            s.close()
            return True, local
        except OSError:
            continue
    return False, None


def device_config(host):
    st, txt = http(host, "/load_config")
    if st != 200:
        return None, "GET /load_config returned %s" % st
    try:
        return json.loads(txt), None
    except ValueError as e:
        return None, "config was not JSON: %s" % e


def gi_status(host, strict=True):
    """(page, error). With strict=False, a trailing fragment is tolerated.

    The leading JSON document is read with raw_decode either way, so the fields
    come from real data rather than from a repair. What differs is the verdict:
    strict reports trailing data as an error, lenient returns the page and puts
    the complaint in `page["_trailing"]` for the caller to report as it sees fit.

    Callers should be strict about an image they have just installed and lenient
    about one they are about to replace -- a device with a broken status page is
    exactly a device that needs reflashing, and refusing on that ground leaves
    only the cable. See bench_flash.py, which does both.
    """
    st, txt = http(host, "/gen_inhibit")
    if st != 200:
        return None, "GET /gen_inhibit returned %s" % st
    try:
        page, end = json.JSONDecoder().raw_decode(txt.lstrip())
    except ValueError as e:
        return None, "status was not JSON: %s" % e
    if not isinstance(page, dict):
        return None, "status was %s, not an object" % type(page).__name__
    rest = txt.lstrip()[end:].strip()
    if rest:
        msg = ("status page has %d bytes of trailing data after the JSON "
               "object (starts %r)" % (len(rest), rest[:40]))
        if strict:
            return None, msg
        page["_trailing"] = msg
    return page, None


def set_mode(host, mode, offset=500):
    q = urllib.parse.urlencode({"mode": mode, "offset": offset})
    return http(host, "/gen_inhibit_set?" + q, method="POST", body=b"")


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


RUNS_ENV = "GI_RUNS_DIR"
# Where this folder sat in reverse-it before it moved into the firmware repo.
_LEGACY_RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "notes", "artifacts", "gen-inhibit", "runs")


def runs_dir():
    """The bench's run-record folder: $GI_RUNS_DIR, else the reverse-it path
    beside this file if it exists. Otherwise refuse, BEFORE touching the
    device: in the firmware repo there is no runs folder, and a record must
    neither go missing nor land inside the repo."""
    d = os.environ.get(RUNS_ENV)
    if d:
        if not os.path.isdir(d):
            raise SystemExit("%s=%r is not a directory" % (RUNS_ENV, d))
        return os.path.abspath(d)
    if os.path.isdir(_LEGACY_RUNS):
        return os.path.abspath(_LEGACY_RUNS)
    raise SystemExit("no runs folder: set %s to reverse-it's "
                     "projects/vtrux/notes/artifacts/gen-inhibit/runs" % RUNS_ENV)


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


def _device_version(host):
    """The running image's version, from /check_status."""
    st, txt = http(host, "/check_status")
    if st != 200:
        return None
    try:
        return json.loads(txt).get("git_version")
    except ValueError:
        return None


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

    cfg, err = device_config(args.host)
    if cfg is None:
        print("[ FAIL ] cannot read the device config: %s" % err)
        return 2
    wifi_mode = cfg.get("wifi_mode")
    if wifi_mode == "AP":
        print("[  ok  ] device is in AP mode -- the configuration that flies")
    else:
        print("[ FAIL ] device wifi_mode is %r, not 'AP'. Spec section 8 makes "
              "AP permanent: the APStation reconnect path calls "
              "esp_wifi_stop()/start(), which has no place on a device "
              "transmitting to a live powertrain bus. A result from this "
              "configuration is not a result about the build that flies."
              % wifi_mode)
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
           # /load_config carries the CONFIG, not the build. The version is on
           # /check_status, and a record that cannot name the image it was taken
           # on is weak evidence.
           "device": _device_version(args.host),
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
