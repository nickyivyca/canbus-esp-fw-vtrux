#!/usr/bin/env python3
"""Preflight and flash the WiCAN over its own AP, asserting every precondition.

    py -3.11 bench_flash.py --bin <image>              # preflight only, no write
    py -3.11 bench_flash.py --bin <image> --flash      # preflight, then OTA
    py -3.11 bench_flash.py --status                   # join and report, nothing else

THE FRAMEWORK DRIVES THE WIFI, NOT A PERSON. The user's ruling, 2026-09-26: the
WiFi connection is asserted and operated by the test framework rather than being
set up by hand beforehand. A hand-made precondition is not recorded in the run
output, does not survive to the next run, and -- the part that matters -- cannot
fail visibly. So this joins the device's AP itself and refuses to go on if any
precondition is missing.

WHY THE SECOND CONNECTION MATTERS. Spec 8 makes `wifi_mode = AP` permanent, so
the only way to reach the device is to point this machine's WiFi at an access
point with no route to anything. If that is the machine's only path, the operator
loses connectivity mid-run and so does anything else depending on it. The user
asked specifically that this be checked before starting. It is checked twice --
before the join and again after -- because the interesting failure is a default
route that MOVES when the WiFi associates, which a check taken only beforehand
cannot see.

Every connectivity check is made BY CONNECTING, never by reading a routing
table: a routing table can look correct while nothing works, and the question
here is whether traffic flows.

WHAT IT REFUSES TO DO. It will not disarm the device to make an OTA possible.
Spec 9.3 refuses OTA in any non-OFF mode, and that refusal is a safety property
under test -- a tool that worked around it would be removing the thing being
relied on. If the device is armed, this says so and stops, leaving the decision
with a person.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bench_ota_guard import (            # noqa: E402
    MODES, http, have_external_route, gi_status,
)

DEFAULT_HOST = "192.168.80.1"
# The SoftAP's SSID embeds the device MAC, so it is a setting and never a
# committed default (this file is published in the firmware repo, 2026-10-08).
# The bench's value is in firmware/bench-holder.md, WiCAN section.
SSID_ENV = "GI_WICAN_SSID"
WIFI_IFACE = "Wi-Fi"

# The device's AP subnet. A "working internet" reading whose local address is in
# here is the WiCAN itself answering, which is not internet.
AP_PREFIX = "192.168.80."

# esp_app_desc_t, at offset 0x20 of an ESP-IDF app image -- the same structure
# test/build_checks/build_output.py reads, and for the same reason: the filename
# is fixed at configure time and can name a different build entirely.
APP_DESC_OFF = 0x20
APP_DESC_MAGIC = 0xABCD5432


def say(ok, what, detail=""):
    mark = "  ok  " if ok else " FAIL "
    if ok is None:
        mark = " .... "
    print("[%s] %-38s %s" % (mark, what, detail))
    return bool(ok)


def netsh(*args):
    try:
        r = subprocess.run(("netsh",) + args, capture_output=True, text=True,
                           timeout=45)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def wlan_state():
    """(ssid, state) for the WiFi interface, from netsh."""
    _, out = netsh("wlan", "show", "interfaces")
    ssid, state = None, None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"^SSID\s*:\s*(.+)$", s)
        if m and ssid is None:
            ssid = m.group(1).strip()
        m = re.match(r"^State\s*:\s*(.+)$", s)
        if m and state is None:
            state = m.group(1).strip()
    return ssid, state


def image_version(path):
    """The version embedded in the image, not the one in its filename."""
    with open(path, "rb") as fh:
        head = fh.read(APP_DESC_OFF + 0x100)
    if len(head) < APP_DESC_OFF + 0x100:
        return None, "file is too short to hold an app descriptor"
    magic = int.from_bytes(head[APP_DESC_OFF:APP_DESC_OFF + 4], "little")
    if magic != APP_DESC_MAGIC:
        return None, "no esp_app_desc_t magic at 0x20 (got 0x%08X)" % magic
    ver = head[APP_DESC_OFF + 0x10:APP_DESC_OFF + 0x30]
    return ver.split(b"\x00")[0].decode("ascii", "replace"), None


def check_status(host):
    st, txt = http(host, "/check_status")
    if st != 200:
        return None, "GET /check_status returned %s" % st
    try:
        return json.loads(txt), None
    except ValueError as e:
        return None, "check_status was not JSON: %s" % e


def join_ap(ssid, timeout=40.0):
    """Associate with the device's AP and wait for it, or explain why not."""
    rc, out = netsh("wlan", "connect", "name=%s" % ssid, "interface=%s" % WIFI_IFACE)
    if rc != 0:
        return False, "netsh wlan connect failed: %s" % out.strip()[:200]
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur, state = wlan_state()
        if state and state.lower() == "connected" and cur == ssid:
            return True, "associated with %s" % ssid
        time.sleep(1.0)
    cur, state = wlan_state()
    return False, "did not associate within %.0f s (now %r / %r)" % (timeout, cur, state)


def wait_for_device(host, timeout=60.0):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        cfg, err = check_status(host)
        if cfg is not None:
            return cfg, None
        last = err or "no answer"
        time.sleep(2.0)
    return None, "device did not answer within %.0f s (%s)" % (timeout, last)


def post_ota(host, path):
    """POST the image the way the handler was actually validated.

    CURL, NOT A HAND-ROLLED MULTIPART, and two details that are not guessable.
    The working invocation is recorded in gen-inhibit-wican-firmware.md as
    `curl -F ota_file=@...`, proven end to end on 2026-09-10 -- HTTP 303 in
    ~15 s, booted the new slot, gate marked it valid, survived a reset.

      - THE FIELD IS `ota_file`. A first attempt here used `file` and the device
        reset the connection mid-upload.
      - THE URI CARRIES THE FILENAME. config_server.c's upload handler does
        `req->uri + sizeof("/upload") - 1` and treats the remainder as the
        destination filename, so POSTing to bare `/upload` gives it an empty
        name. It must be `/upload/ota.bin`.

    Both together produced a ConnectionResetError rather than an HTTP status,
    which is worth knowing: a malformed upload to this handler does not come
    back as a 4xx.
    """
    url = "http://%s/upload/ota.bin" % host
    cmd = ("curl", "-s", "-S", "--max-time", "180",
           "-w", "\n%{http_code}",
           "-F", "ota_file=@%s" % path, url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "curl failed: %s" % e
    out = (r.stdout or "").strip().splitlines()
    code = out[-1].strip() if out else ""
    body = "\n".join(out[:-1])[:200] if len(out) > 1 else (r.stderr or "").strip()[:200]
    try:
        return int(code), body
    except ValueError:
        return None, "no HTTP status from curl (rc=%d): %s" % (r.returncode, body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", help="the image to flash (or to preflight against)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--ssid", default=os.environ.get(SSID_ENV),
                    help="the WiCAN's SoftAP SSID (default: $%s). Required; "
                         "it embeds the device MAC, so none is committed" % SSID_ENV)
    ap.add_argument("--flash", action="store_true",
                    help="actually write flash. Without it this is a preflight "
                         "and nothing is written.")
    ap.add_argument("--status", action="store_true",
                    help="join and report the device, then stop")
    args = ap.parse_args()
    if not args.ssid:
        ap.error("no SSID: pass --ssid or set %s (the bench's is in "
                 "firmware/bench-holder.md)" % SSID_ENV)

    print("WiCAN bench preflight%s" % ("" if args.flash else " (NO WRITE)"))
    print()

    fails = []

    def need(ok, what, detail=""):
        if not say(ok, what, detail):
            fails.append(what)
        return ok

    # ---------------------------------------------------------------- image --
    img_ver = None
    if args.bin:
        need(os.path.exists(args.bin), "image exists", args.bin)
        if os.path.exists(args.bin):
            img_ver, err = image_version(args.bin)
            need(img_ver is not None, "image carries a version",
                 err or "embedded version %r" % img_ver)
            size = os.path.getsize(args.bin)
            sha = hashlib.sha256(open(args.bin, "rb").read()).hexdigest()[:16]
            say(True, "image", "%d bytes, sha256 %s..." % (size, sha))
    elif not args.status:
        need(False, "an image to flash", "pass --bin, or --status to just report")

    if fails:
        print("\nPREFLIGHT FAILED before touching the network: %s"
              % ", ".join(fails))
        return 1

    # ------------------------------------------------- internet, before join --
    #
    # Taken FIRST, while WiFi is still wherever it was, so the baseline is the
    # machine's normal state rather than one this tool produced.
    ok, local_before = have_external_route()
    need(ok, "internet before the join",
         "reachable from %s" % local_before if ok else
         "no route to 1.1.1.1 or 8.8.8.8 -- joining an AP with no internet "
         "would leave this machine with none")
    if ok:
        need(not str(local_before).startswith(AP_PREFIX),
             "that internet is not the device",
             "local address %s" % local_before)

    ssid_before, state_before = wlan_state()
    say(True, "WiFi before", "%s / %s" % (ssid_before or "(none)", state_before))

    if fails:
        print("\nPREFLIGHT FAILED before touching the network: %s"
              % ", ".join(fails))
        return 1

    # ------------------------------------------------------------- the join --
    ok, why = join_ap(args.ssid)
    need(ok, "joined the device's AP", why)
    if not ok:
        print("\nPREFLIGHT FAILED: %s" % ", ".join(fails))
        return 1

    # ------------------------------------------------- internet, after join --
    #
    # THE CHECK THAT ACTUALLY MATTERS, and the one a manual setup skips. The
    # failure being looked for is a default route that MOVED when the WiFi
    # associated: the machine can have had internet a moment ago and be talking
    # to a dead AP now.
    ok, local_after = have_external_route()
    need(ok, "internet after the join",
         "still reachable from %s" % local_after if ok else
         "internet was LOST by joining the device's AP -- the default route "
         "followed WiFi. Stop and give this machine a second path.")
    if ok:
        need(not str(local_after).startswith(AP_PREFIX),
             "internet is still not the device", "local address %s" % local_after)
        say(local_after == local_before, "the same path as before",
            "%s -> %s" % (local_before, local_after))

    # ------------------------------------------------------------ the device --
    cfg, err = wait_for_device(args.host)
    need(cfg is not None, "device answers", err or "over its AP at %s" % args.host)
    if cfg is None:
        print("\nPREFLIGHT FAILED: %s" % ", ".join(fails))
        return 1

    say(True, "device reports", "git_version=%r fw_version=%r hw=%r"
        % (cfg.get("git_version"), cfg.get("fw_version"), cfg.get("hw_version")))
    need(cfg.get("wifi_mode") == "AP", "wifi_mode is AP",
         "wifi_mode=%r -- spec 8 makes AP permanent for the build that flies"
         % cfg.get("wifi_mode"))

    # LENIENT HERE, STRICT AFTER THE OTA. This page belongs to the image being
    # replaced; the mode is what preflight actually needs from it, and a device
    # whose status page is malformed is the very device that wants reflashing.
    st, err = gi_status(args.host, strict=False)
    need(st is not None, "gen_inhibit answers",
         err or (st.get("_trailing", "") if st else ""))
    if st is not None:
        mode = st.get("mode")
        say(True, "gen_inhibit mode", "%s (%s)" % (mode, MODES.get(mode, "?")))
        # SPEC 9.3. Not worked around: the refusal is a safety property under
        # test, and a tool that disarmed the device to get past it would be
        # removing the thing being relied on.
        need(mode == 0, "device is OFF, so OTA is permitted",
             "mode is %s (%s); spec 9.3 refuses OTA in any non-OFF mode. "
             "Disarm it deliberately rather than having this tool do it."
             % (mode, MODES.get(mode, "?")))

    if args.status:
        print("\n--status: reported only, nothing written.")
        return 1 if fails else 0

    if fails:
        print("\nPREFLIGHT FAILED, nothing written: %s" % ", ".join(fails))
        return 1

    print("\nevery precondition holds")
    if not args.flash:
        print("NO WRITE: this was a preflight. Re-run with --flash to OTA.")
        print("Still joined to %s so the device tests can follow." % args.ssid)
        return 0

    # ----------------------------------------------------------------- flash --
    before_sha = cfg.get("git_version")
    print("\nposting %s (%d bytes) to http://%s/upload"
          % (os.path.basename(args.bin), os.path.getsize(args.bin), args.host))
    st_code, txt = post_ota(args.host, args.bin)
    ok = st_code in (200, 302, 303)
    need(ok, "OTA accepted", "HTTP %s %s" % (st_code, (txt or "")[:120]))
    if not ok:
        print("\nFLASH FAILED: %s" % ", ".join(fails))
        return 1

    print("waiting for the device to boot the new slot")
    time.sleep(8.0)
    # The association usually survives the reboot; if it does not, rejoin.
    cur, state = wlan_state()
    if not (state and state.lower() == "connected" and cur == args.ssid):
        join_ap(args.ssid)
    cfg2, err = wait_for_device(args.host, timeout=90.0)
    need(cfg2 is not None, "device answers after the OTA", err or "")
    if cfg2 is None:
        print("\nFLASH INCOMPLETE -- the device did not come back. The rollback")
        print("gate should return it to the previous slot; check before reflashing.")
        return 1

    after_sha = cfg2.get("git_version")
    say(True, "device reports after", "git_version=%r (was %r)"
        % (after_sha, before_sha))
    # The embedded version is `git describe`-shaped, so allow a prefix match
    # either way rather than demanding equality.
    match = bool(img_ver and after_sha
                 and (after_sha.startswith(img_ver) or img_ver.startswith(after_sha)))
    need(match, "running the image that was flashed",
         "image embedded %r, device reports %r" % (img_ver, after_sha))

    # THE STATUS PAGE MUST STILL PARSE. See this script's history: an image can
    # be the right image, boot, and report its version correctly while serving a
    # status page no reader can load. Checked by parsing, not by inspecting any
    # field, so it stays correct as the schema grows.
    st, body = http(args.host, "/gen_inhibit")
    if st != 200:
        need(False, "status page parses", "HTTP %s from /gen_inhibit" % st)
    else:
        try:
            page = json.loads(body)
        except ValueError as e:
            need(False, "status page parses",
                 "/gen_inhibit is not valid JSON (%s); the image is running but "
                 "its status page is unreadable" % e)
        else:
            extra = [k for k in ("rxq",) if k in page]
            need(True, "status page parses",
                 "%d fields%s" % (len(page),
                                  "; MEASUREMENT BUILD (%s present)"
                                  % ", ".join(extra) if extra else ""))

    print()
    if fails:
        print("FLASH COMPLETED WITH FAILURES: %s" % ", ".join(fails))
        return 1
    print("flashed and verified: the device is running %r" % after_sha)
    print("NOTE: the rollback gate marks a slot valid only after the app has")
    print("proven itself (spec 9.1). A reboot that returns to the old version")
    print("means the gate did its job, not that the OTA failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
