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


def image_elf_sha256(path):
    """The descriptor's app_elf_sha256 (0x90 into esp_app_desc_t) as 64 hex
    characters: what the page's `elf_sha256` reports for this image (spec 11,
    "The page names the binary it runs"). `git_version` cannot tell builds of
    one commit apart; this can."""
    with open(path, "rb") as fh:
        head = fh.read(APP_DESC_OFF + 0xB0)
    if len(head) < APP_DESC_OFF + 0xB0:
        return None
    if int.from_bytes(head[APP_DESC_OFF:APP_DESC_OFF + 4], "little") != APP_DESC_MAGIC:
        return None
    return head[APP_DESC_OFF + 0x90:APP_DESC_OFF + 0xB0].hex()


# ------------------------------------------------------- what the OTA did --
#
# Every OTA gets one verdict (reviewer, 2026-10-16, from the user, after a prod
# OTA ended in "connection reset"), judged by elf_sha256 -- never git_version,
# which builds of one commit share -- and by each boot's INSTANT (the read's
# wall time minus uptime_ms) against the upload's end:
#   (a1) NO STATUS, NO RESTART  no HTTP status and the boot unchanged: the
#                               upload failed with the device up.
#   (a2) NO STATUS, RESTARTED   no status, but the device restarted: the success
#                               path ran and its 303 was lost. What follows is
#                               judged as after a 303, e.g. "a2+c".
#   (b)  REFUSED                a 4xx/5xx status.
#   (c)  TOOK                   the new image, one restart, past the gate.
#   (d)  ROLLED BACK BY THE GATE  the old image, booted >= 60 s after the
#                               upload's end, reason SW.
#   (d') ROLLED BACK AFTER A CRASH  the old image, reason PANIC/INT_WDT/
#                               TASK_WDT/WDT/BROWNOUT.
#   (e)  THE NEW IMAGE NEVER RAN  the old image, its one restart <= 10 s after
#                               the upload's end.
# Anything else is reported as what it is ('other', 'undecided'), never folded
# into one of these.
#
# The numbers, from the implementor (2026-10-16, c65c45e): the success path
# starts a 2000 ms restart timer and then sends the 303, so a 303 can be lost to
# the restart. Failed uploads never restart the device. OTA_HEALTH_TIMEOUT_MS
# is 60000 on normal and prod builds, counted from the gate task's start, so a
# gate rollback boots 60 s plus boot time after the new image's boot; the
# 2026-10-04 WiCAN rehearsal measured 60.5 s (test evidence doc, 9.1). TOOK
# therefore needs 90 s of the new image's uptime. When both slots hold the same
# build the sha cannot show a rollback; a second restart does, and (e) cannot
# be told from (c) at all, which the verdict then says.
SETTLE_UPTIME_MS = 90000
GATE_MIN_S = 60.0
NEVER_RAN_MAX_S = 10.0
A1_CONFIRM_S = 30.0
SAME_BOOT_S = 1.0
CRASH_REASONS = ("PANIC", "INT_WDT", "TASK_WDT", "WDT", "BROWNOUT")
WATCH_S = 240.0
WATCH_OTHER_S = 30.0
WATCH_EVERY_S = 2.0
VERDICT_NAMES = {"a1": "NO STATUS, NO RESTART", "a2": "NO STATUS, RESTARTED",
                 "b": "REFUSED", "c": "TOOK", "d": "ROLLED BACK BY THE GATE",
                 "d'": "ROLLED BACK AFTER A CRASH", "e": "THE NEW IMAGE NEVER RAN"}


def identity(page):
    """The fields that say which image runs and since when."""
    if not page:
        return None
    return {"elf_sha256": page.get("elf_sha256"), "uptime_ms": page.get("uptime_ms"),
            "reset_reason": page.get("reset_reason"), "mode": page.get("mode"),
            "git_version": page.get("git_version")}


def boot_bounds(r):
    """(earliest, latest) wall time of the boot a reading belongs to: the read
    happened between its request (t_req) and its answer (t_resp)."""
    up = r["uptime_ms"] / 1000.0
    return r["t_req"] - up, r["t_resp"] - up


def boots(before, readings):
    """The distinct boots seen, in order, `before` first. A reading starts a new
    boot when its boot instant is more than SAME_BOOT_S (plus both reads'
    spread) from the current one's."""
    out = []
    for i, r in enumerate([before] + list(readings)):
        if not r or r.get("uptime_ms") is None or r.get("t_req") is None:
            continue
        lo, hi = boot_bounds(r)
        cur = out[-1] if out else None
        if cur is not None and abs((lo + hi) / 2 - (cur["lo"] + cur["hi"]) / 2) \
                <= SAME_BOOT_S + (hi - lo) + (cur["hi"] - cur["lo"]):
            cur["lo"], cur["hi"] = max(cur["lo"], lo), min(cur["hi"], hi)
            cur["last_uptime_ms"], cur["reads"] = r["uptime_ms"], cur["reads"] + 1
            cur["last_t"] = r["t_resp"]
            continue
        out.append({"lo": lo, "hi": hi, "elf_sha256": r.get("elf_sha256"),
                    "reset_reason": r.get("reset_reason"), "last_uptime_ms": r["uptime_ms"],
                    "reads": 1, "last_t": r["t_resp"], "index": i - 1})
    return out


def describe_boots(bs, t_end):
    return "; ".join("boot %d %s... %s at end%+.2f..%+.2f s, %d read(s), last uptime %d ms"
                     % (k, (b["elf_sha256"] or "?")[:8], b["reset_reason"],
                        b["lo"] - t_end, b["hi"] - t_end, b["reads"], b["last_uptime_ms"])
                     for k, b in enumerate(bs))


def classify(code, t_end, before, readings, image_sha, settle_ms=SETTLE_UPTIME_MS):
    """(verdict, detail). verdict is one of VERDICT_NAMES' keys, "a2+<x>",
    'other' or 'undecided'. The detail always carries the boot arithmetic."""
    if code is not None and code >= 400:
        return "b", "HTTP %d" % code
    if code is not None and code != 303:
        return "other", "HTTP %d, not the handler's 303" % code
    bs = boots(before, readings)
    arith = describe_boots(bs, t_end)
    if not bs:
        return "undecided", "no reading carried a boot instant"
    new, before_sha = bs[1:], bs[0]["elf_sha256"]
    if code is None and not new:
        if bs[0]["last_t"] - t_end >= A1_CONFIRM_S:
            return "a1", "no status; " + arith
        return "undecided", "no status, and no reading %d s past the upload's end; %s" % (
            A1_CONFIRM_S, arith)
    pre = "a2+" if code is None else ""
    if not new:
        return "undecided", "a 303, then no restart in the window; " + arith
    last = new[-1]
    same = image_sha == before_sha
    if last["elf_sha256"] == image_sha and len(new) == 1 and last["last_uptime_ms"] >= settle_ms:
        return pre + "c", ("the same build is in both slots, so (e) cannot be told from (c); "
                           if same else "") + arith
    if last["elf_sha256"] == before_sha and (not same or len(new) >= 2):
        if last["reset_reason"] in CRASH_REASONS:
            return pre + "d'", arith
        if last["lo"] - t_end >= GATE_MIN_S and last["reset_reason"] == "SW":
            return pre + "d", arith
        if len(new) == 1 and last["hi"] - t_end <= NEVER_RAN_MAX_S:
            return pre + "e", arith
        return "undecided", "back on the old image, but not as any of d, d', e; " + arith
    if last["elf_sha256"] not in (image_sha, before_sha):
        return "other", "running neither the image nor the one before; " + arith
    return "undecided", "the window closed before the gate decided; " + arith


def decided(verdict):
    return verdict.split("+")[-1] in ("a1", "c", "d", "d'", "e")


def watch(read, code, t_end, before, image_sha, now, sleep, settle_ms=SETTLE_UPTIME_MS,
          every=WATCH_EVERY_S):
    """Read the device until the verdict is decided or the window closes: WATCH_S
    after a 303 or no status, WATCH_OTHER_S after any other status. Each reading
    records t_req and t_resp, the wall times around the read."""
    window = WATCH_S if code is None or code == 303 else WATCH_OTHER_S
    readings = []
    while now() - t_end < window:
        t_req = now()
        r = read()
        rec = dict(r) if r else {"answered": False}
        rec["t_req"], rec["t_resp"] = t_req, now()
        readings.append(rec)
        if decided(classify(code, t_end, before, readings, image_sha, settle_ms)[0]):
            break
        sleep(every)
    return readings

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
           "-w", CURL_WRITE_OUT,
           "-F", "ota_file=@%s" % path, url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.SubprocessError) as e:
        return {"http_code": None, "curl_error": "curl failed: %s" % e}
    return parse_curl(r.stdout, r.stderr, r.returncode)


# curl's own account of the upload (reviewer, 2026-10-16): the status, the
# bytes it sent and how long it took, so a failed upload shows how far it got.
CURL_WRITE_OUT = "\n%{http_code} %{size_upload} %{time_total}"


def parse_curl(stdout, stderr, rc):
    """{http_code, size_upload, time_total, body, curl_error, curl_rc}.
    http_code is None when no status came back: curl writes 000 then, which
    the first version of this read as a status of 0 (03:09, 2026-10-16). The
    body is whatever the device sent; its error exits each send one."""
    out = {"http_code": None, "size_upload": None, "time_total": None, "body": "",
           "curl_error": (stderr or "").strip()[:300], "curl_rc": rc}
    text = stdout or ""
    body, _, tail = text.rpartition("\n")
    parts = tail.split()
    if len(parts) == 3:
        try:
            code = int(parts[0])
            out["http_code"] = code if code > 0 else None
            out["size_upload"] = int(float(parts[1]))
            out["time_total"] = float(parts[2])
            out["body"] = body[:2000]
        except ValueError:
            out["body"] = text[:2000]
    else:
        out["body"] = text[:2000]
    return out

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
    ap.add_argument("--record", help="with --flash: write the OTA's outcome "
                    "(before, the readings after, the verdict) to this JSON file")
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
    #
    # The verdict is classify()'s, from elf_sha256 and boot instants -- never
    # git_version, which builds of one commit share (2026-10-16).
    image_sha = image_elf_sha256(args.bin)
    need(image_sha is not None, "image carries an elf_sha256", image_sha or "")

    def read():
        # The association usually survives the reboot; if it does not, rejoin.
        page, _ = gi_status(args.host, strict=False)
        if page is None:
            cur, state = wlan_state()
            if not (state and state.lower() == "connected" and cur == args.ssid):
                join_ap(args.ssid, timeout=20.0)
        return identity(page)

    t_req = time.time()
    before = read()
    if before:
        before.update(t_req=t_req, t_resp=time.time())
    need(bool(before and before.get("elf_sha256") and before.get("uptime_ms") is not None),
         "device reports elf_sha256 and uptime", before)
    if fails:
        print("\nNOTHING WRITTEN: %s" % ", ".join(fails))
        return 1
    say(True, "before", "elf_sha256 %s... uptime %s ms reset_reason %s; image %s..."
        % (before["elf_sha256"][:8], before["uptime_ms"], before["reset_reason"], image_sha[:8]))
    print("\nposting %s (%d bytes) to http://%s/upload"
          % (os.path.basename(args.bin), os.path.getsize(args.bin), args.host))
    t_start = time.time()
    up = post_ota(args.host, args.bin)
    t_end = time.time()
    code = up.get("http_code")
    say(None, "upload ended", "HTTP %s; curl sent %s of %d bytes in %s s (wall %.2f s); %s"
        % (code, up.get("size_upload"), os.path.getsize(args.bin), up.get("time_total"),
           t_end - t_start, up.get("curl_error") or "no curl error"))
    if up.get("body"):
        say(None, "reply body", up["body"][:200].replace("\n", " | "))

    readings = watch(read, code, t_end, before, image_sha, time.time, time.sleep)
    verdict, detail = classify(code, t_end, before, readings, image_sha)
    names = " + ".join(VERDICT_NAMES.get(v, v.upper()) for v in verdict.split("+"))
    print("\nVERDICT: (%s) %s -- %s" % (verdict, names, detail))
    if args.record:
        rec = {"image": os.path.basename(args.bin), "image_elf_sha256": image_sha,
               "image_version": img_ver, "image_bytes": os.path.getsize(args.bin),
               "before": before, "t_start": t_start, "t_end": t_end,
               "wall_s": round(t_end - t_start, 3), "upload": up, "readings": readings,
               "boots": boots(before, readings), "settle_uptime_ms": SETTLE_UPTIME_MS,
               "verdict": verdict, "verdict_name": names, "detail": detail}
        with open(args.record, "w") as fh:
            json.dump(rec, fh, indent=1)
        print("wrote %s" % args.record)
    need(verdict.split("+")[-1] == "c", "the OTA took", "(%s) %s" % (verdict, names))
    if fails:
        print("\nFLASH NOT CONFIRMED: %s" % ", ".join(fails))
        return 1

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
    print("flashed and verified: the device is running %s..." % image_sha[:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
