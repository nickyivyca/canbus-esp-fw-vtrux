"""Drive the OTA rollback rehearsal on the WiCAN. One phase per invocation.

WHAT THIS PROVES, AND WHY THE EXISTING WORK DOES NOT. `rollback-test/` already
proved ESP-IDF bootloader rollback end to end -- `rollback_capture.txt` has a
fault-injected image booting PENDING_VERIFY, the gate timing out, and the
bootloader returning to ota_0. That was on the BENCH BOARD: its own firmware,
its own `/status`, its own `rollback_bench` SoftAP, a 30 s timeout. What has
never been exercised is the gate in `ota_health.c` (`main/` until port step
1a, vtrux/wican's since) on the WiCAN, on the
image that is a candidate to go on the truck. That is gap 6.

USER AUTHORISATION, 2026-10-04: the full rehearsal including the deliberate
fault injection, after being offered the weaker variant (OTA a valid image,
watch the gate pass, never exercise the rollback branch) and choosing against
it. See `plans/gen-inhibit-wican-implementation.md`.

TWO THINGS THE PLAN GOT WRONG, corrected here because the script is what gets
re-read:

1.  "SERIAL ON COM4 IS THE ONLY WAY BACK" IS FALSE, and that was the claim the
    authorisation was given against. `OTA_HEALTH_FAULT_INJECT` intercepts
    `ota_health_report()` at its entry -- it suppresses the REPORT, not the
    subsystem. By the time the WIFI_AP report is suppressed the SoftAP has
    already started (`wifi_network.c:149` reports from the
    `WIFI_EVENT_AP_START` handler) and the HTTP server is already listening
    (`config_server.c:2445`). A fault-injected image is reachable over HTTP for
    its whole 60 s gate window and would stay reachable if the rollback never
    happened. Serial is a fallback, not the only one. The rehearsal is strictly
    safer than it was described as being.

2.  THE PLAN HAD NO POSITIVE CONTROL, and without one "it rolled back" does not
    distinguish a working gate from a gate that always trips. That matters in
    the direction nobody would notice: a gate that trips on every OTA means the
    truck silently can never take one. So the `control` phase OTAs a GOOD image
    first and requires the gate to MARK IT VALID, in the same conditions, on
    the same device, differing from the fault image only in the injected bit.
    This is not the weaker variant the user declined -- that was offered as a
    SUBSTITUTE for the rollback branch; this is a control inside the full
    rehearsal, and the rollback branch still runs.
    (The second point came from the interposer-board session, which had twice
    been one step from a bench arm that could not have failed.)

WHY ONE BIT AND NOT BOTH. The fault mask is 1 = OTA_HEALTH_WIFI_AP only, so the
gate logs `have 0x02, need 0x03 (missing 0x01)`. The `0x02` is the load-bearing
part: it proves HTTPD DID report, so the gate was alive and waiting on a named
missing bit. Suppress both and `have 0x00` is equally consistent with an event
group that never worked at all, and the rehearsal loses its discriminator.

EVERY PHASE'S PASS IS CONDITIONED ON ENGAGEMENT, not just on outcome. A run
that reports the right end state without the mechanism having fired did not
test anything, so the specific console lines are required and their absence is
a FAIL. The engagement conditions are listed per phase in PHASES below and
checked in `judge_*`.

LOG LEVEL NONE, FROM PORT STEP 1a2 (gen-inhibit reviewer, 2026-10-30). The
release and bench _man images log nothing (app none, bootloader error: shared/
tools R28), and the unit runs whatever bootloader was last flashed serially. On
the good image neither the bootloader's `Loaded app from partition` nor
ota_health's `gate armed`, `subsystem up` or `marking image valid` appears. So:
  - a BOOT is found from the ROM banner (`ESP-ROM:` and `rst:0x..`), which the
    chip's ROM prints whatever the bootloader and the app are built at. The
    bootloader's slot line is recorded when the console carries it, never
    required;
  - the GOOD image's gate is judged by its effect, from /gen_inhibit's
    elf_sha256, never the console: the new boot runs the good image, has been
    up past the gate's timeout, and after a reboot (wican_reboot.py) is still
    the running image, so it was marked valid. An image left PENDING_VERIFY is
    rolled back by the bootloader at that reboot;
  - the FAULT phase's rollback is shown the same way, back on the good image
    afterwards, with the 55-75 s gap timed between ROM banners. The _flt image
    is built at info (its sdkconfig layering), so its own lines -- gate armed
    PENDING_VERIFY, FAULT INJECT, HTTPD up and WIFI_AP not -- are still read
    and required;
  - the bootloader phase records the unit's bootloader: the sha256 of its image
    in flash, and the log level its strings show.

WHICH SLOT, FROM THREE PATHS -- and the first draft of this paragraph named two
that do not exist. The bootloader prints `Loaded app from partition at offset`
(NOT "Loading app partition at offset", which is what the parser first looked
for, so no boot was ever matched), and the OTA handler's `Writing to partition
subtype N` is logged after app_main has silenced logging and so never appears at
all. What actually works:

  1.  the bootloader's `Loaded app from partition at offset 0x...`, cross-
      referenced against the partition table it prints on the same boot;
  2.  the firmware's `gate armed: running <label>, state <state>`;
  3.  a raw esptool read of otadata at 0xd000, which does not trust the
      firmware at all.

They must agree, and in this run they did. Path 3 is the one that caught a bug
in path 3's own interpreter -- see `parse_otadata`. From port step 1a2 paths 1
and 2 are silent on the good image, and the slot is no longer judged: the image
is (LOG LEVEL NONE, above).

PHASES

  images    offline. Verify the good and faulty images are the pair we think:
            size, md5, esp_app_desc.project_name, and whether the ELF beside
            each defines the fault hook, ota_health_fault_inject_suppress
            (shared/wican W7). The last is the direct static proof the fault
            is compiled in. Until port step 1a2 it was the "FAULT INJECT"
            format string, which a build at release log levels (shared/tools
            R28) drops whatever the switch; the faulty image (info level)
            must still carry it, for the fault phase's line.
  baseline  HTTP /gen_inhibit, and the connected AP's BSSID against the expected
            unit (value-blind). Refuses to continue unless it is the unit and the
            device is in mode 0 and not live. Never /check_status (2026-10-25).
  otadata   esptool read of the otadata partition, parsed. Needs COM4 free and
            RESETS the device; run it only between phases.
  control   OTA the GOOD image onto a device running another. Engagement: one
            boot (ROM banner); the page names the good image, up past the
            gate's timeout; a reboot; the page still names it.
  fault     OTA the FAULTY image onto the good one. Engagement, from the _flt
            image's own lines: gate armed PENDING_VERIFY, a FAULT INJECT line,
            HTTPD reporting while WIFI_AP does not; then a SECOND boot (ROM
            banner) 55-75 s later with no FAULT INJECT line, and the page back
            on the good image. It does NOT require `UNHEALTHY` or `rolling
            back`: those are emitted 60 s in, after logging is off, so
            requiring them would fail a working rollback. See the note above
            RE_GATE_ARMED.
  restore   OTA the GOOD image into the slot the faulty one used, so both slots
            end holding a good image. Judged as control, except that the device
            already runs the good image, so the page cannot show the OTA took.
  verify    End state from HTTP, including the 60 s wait that bring-up row 1.5b
            turns on.
  bootloader  WHICH bootloader is in flash -- this project's or MeatPi's --
            by comparing 0x0..0x8000 against `build/bootloader/bootloader.bin`.
            Added 2026-10-05 because spec 9.1 says the WiCAN runs MeatPi's and
            that it cannot be tested directly, and on a serially flashed unit
            that is not so: `idf.py flash` writes bootloader.bin at 0x0. Either
            answer is a legitimate state, so this phase RECORDS rather than
            passing or failing the device. Since port step 1a2 it also records
            the device bootloader's sha256 and the log level its strings show.

    py -3.14 gen-inhibit/tools/bench/ota_rollback_rehearsal.py \
        --phase images
    ... --phase baseline
    ... --phase control
    ... --phase fault
    ... --phase restore --phase verify

Run from the repo root. The host must be joined to the WiCAN's SoftAP.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zlib
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)
import image_elf_sha  # noqa: E402  the app descriptor's fields


HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = gi_paths.runs()

HOST = "192.168.80.1"
PORT = "COM4"
#
# THE MAC IS THE UNIT'S SERIAL NUMBER (user, 2026-10-05). There has only ever
# been one WiCAN on this bench under the current setup; earlier testing used a
# WiCAN Pro. So every run file should name its unit, and both identities below
# are checked rather than recorded.
#
# THE TWO VALUES DIFFER BY ONE AND THAT IS CORRECT. `EXPECT_MAC` is the BASE
# MAC, which is what esptool prints. `device_id` on /check_status, the SoftAP
# SSID and the SoftAP's BSSID all carry the SoftAP MAC, which is base + 1; the
# BSSID is what unit_check compares (2026-10-25). Written out here because
# two values one apart read exactly like an off-by-one bug, and the repair
# someone would reach for is to make them match.
#
def expected_ids():
    """(the WiCAN's base MAC as esptool prints it, its SoftAP device id as HTTP reports),
    from the chain settings file GI_CHAIN_SETTINGS names, keys wican.base_mac and
    wican.device_id. No board identity is committed (reviewer, 2026-10-22: the repo is
    public), so a missing file or key is refused."""
    p = os.environ.get("GI_CHAIN_SETTINGS", "").strip()
    if not p:
        raise SystemExit("GI_CHAIN_SETTINGS is not set: the WiCAN's MAC and device id come from the "
                         "chain settings file")
    with open(p, encoding="utf-8") as fh:
        w = json.load(fh).get("wican") or {}
    if not w.get("base_mac") or not w.get("device_id"):
        raise SystemExit("%s has no wican.base_mac and wican.device_id" % p)
    return w["base_mac"].lower(), w["device_id"].lower()


EXPECT_MAC, EXPECT_DEVICE_ID = expected_ids()   # base MAC; SoftAP MAC = base + 1

# COM3 is the S3 interposer and belongs to another session;
# closing its port RESETS it. Every esptool call here checks the MAC it printed
# against EXPECT_MAC, so a wrong --port is caught before it can matter rather
# than after someone notices a board rebooting mid-arm.
RE_ESPTOOL_MAC = re.compile(r"MAC:\s*([0-9a-fA-F:]{17})")

FW_DEFAULT = os.environ.get("GEN_INHIBIT_FW") or gi_paths.firmware_root()   # the WiCAN tree, its build/ folders

# subtype -> label, from ESP_PARTITION_SUBTYPE_APP_OTA_0 = 0x10.
SUBTYPE_LABEL = {16: "ota_0", 17: "ota_1"}
# LAST-RESORT FALLBACK ONLY: `slot_map()` reads the bootloader's own printed
# partition table out of each capture and overrides these, so they fill gaps
# only for a slot the capture never named.
#
# THESE ARE MEASURED VALUES, NOT COMPUTED ONES, and that is the point. The CSV
# gives ota_0 at 0x10000 and each slot as 1740K, so the arithmetic says ota_1
# is at 0x1C3000 -- which is what the first version of this file used, and it
# is wrong. The CSV leaves ota_1's offset blank and app partitions must be
# 64K-aligned, so the real table says 0x1D0000. Every slot comparison was
# silently against None until a capture was read instead.
SLOT_OFFSET = {"ota_0": 0x10000, "ota_1": 0x1D0000}
OFFSET_SLOT = dict((v, k) for k, v in SLOT_OFFSET.items())

OTA_STATE = {0: "NEW", 1: "PENDING_VERIFY", 2: "VALID", 3: "INVALID",
             4: "ABORTED", 0xFFFFFFFF: "UNDEFINED"}

GATE_TIMEOUT_MS = 60000
# The good image must have been up this long before the control's reboot: the gate
# arms a few seconds into the boot and resolves GATE_TIMEOUT_MS after that.
GATE_SETTLED_MS = GATE_TIMEOUT_MS + 15000


# ---------------------------------------------------------------- plumbing

def ts():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    print(msg, flush=True)


def http_json(ep, timeout=8.0, host=HOST):
    url = "http://%s%s" % (host, ep)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("ascii", "replace"))


def save(phase, record):
    """One file per phase, plus an immutable timestamped copy.

    Phase-keyed, never merged at top level. A previous bench script in this
    folder merged whole-run dicts and a failed run's key ended up sitting
    beside the passes under a later run's timestamp, which is worse than
    losing it.
    """
    if not os.path.isdir(RUNS):
        os.makedirs(RUNS)
    stamp = ts()
    record["_phase"] = phase
    record["_run"] = stamp
    live = os.path.join(RUNS, "rollback_%s.json" % phase)
    frozen = os.path.join(RUNS, "rollback_%s_%s.json" % (phase, stamp))
    for p in (live, frozen):
        with open(p, "w", encoding="ascii", errors="replace") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
    log("  wrote %s" % os.path.relpath(live, "."))
    log("  wrote %s" % os.path.relpath(frozen, "."))
    return live


# ---------------------------------------------------------------- images

FAULT_MARKER = b"FAULT INJECT"


def fault_hook(path):
    """(defined, why): whether the ELF beside image `path` defines the fault-injection hook
    (build_output.FAULT_HOOK_SYM, shared/wican W7), read with nm by build_output's own
    elf_symbols_beside(); defined is None, with the reason, when there is nothing to judge by."""
    sys.path.insert(0, os.path.join(gi_paths.project_root(), "test", "build_checks"))
    import build_output
    defined, why = build_output.elf_symbols_beside(path)
    return (None if defined is None else build_output.FAULT_HOOK_SYM in defined), why


def image_facts(path):
    """Everything about an image that can be established without flashing it."""
    if not os.path.exists(path):
        return {"path": path, "exists": False}
    data = open(path, "rb").read()
    i = data.find(b"wican-fw_obd_")
    name = None
    if i >= 0:
        name = data[i:i + 32].split(b"\x00")[0].decode("ascii", "replace")
    try:
        desc = image_elf_sha.read_desc(data)
        version, elf = desc["version"], desc["elf_sha256"]
    except ValueError:
        version = elf = None
    hook, hook_why = fault_hook(path)
    return {
        "path": path,
        "exists": True,
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
        "project_name": name,
        "version": version,
        "elf_sha256": elf,
        "has_fault_string": FAULT_MARKER in data,
        "has_fault_symbol": hook,
        "fault_symbol_why": hook_why,
    }


def judge_images(good, faulty):
    """The pair must be distinguishable four independent ways.

    md5 alone would do for telling two FILES apart, but the one that survives
    into the device is project_name, and the one that proves the fault is
    actually COMPILED IN rather than merely requested on a command line is the
    hook's symbol in the ELF. `idf.py -D` sets a CMake cache variable, so a fault
    build that was asked for and not wired produces a byte-for-byte ordinary
    image -- which is the failure this row exists to catch.

    The good image's row was "good has NO fault string" until port step 1a2
    (gen-inhibit reviewer, 2026-10-30, ruling 4). The good image is built at
    release log levels (shared/tools R28), where the fault branch's log string
    compiles to nothing even with the switch on, so that row passed on any good
    image. Both images are now judged by the hook's own function
    (build_output.FAULT_HOOK_SYM), defined only with the switch on, in the ELF
    beside each; no ELF or no nm fails both rows. The faulty image keeps its
    string row as well: a test-only build is at info level, and the fault phase
    reads its FAULT INJECT line.
    """
    rows = []
    rows.append(("good image present", bool(good.get("exists"))))
    rows.append(("faulty image present", bool(faulty.get("exists"))))
    if not (good.get("exists") and faulty.get("exists")):
        return rows
    rows.append(("md5 differs", good["md5"] != faulty["md5"]))
    rows.append(("project_name differs",
                 good["project_name"] != faulty["project_name"]))
    rows.append(("good ELF does NOT define the fault hook", good.get("has_fault_symbol") is False))
    rows.append(("faulty ELF defines the fault hook", faulty.get("has_fault_symbol") is True))
    rows.append(("faulty HAS fault string", faulty["has_fault_string"]))
    rows.append(("faulty name carries _flt",
                 bool(faulty["project_name"]
                      and faulty["project_name"].endswith("_flt"))))
    rows.append(("good name does not carry _flt",
                 bool(good["project_name"]
                      and not good["project_name"].endswith("_flt"))))
    # The commit is the app descriptor's version (shared/tools R4). This row read it out of
    # project_name until port step 0c took it out of there (2026-10-25): both names then gave
    # "man" and the row passed on any two images.
    rows.append(("same commit in both versions",
                 bool(good.get("version")) and good.get("version") == faulty.get("version")))
    return rows


# ---------------------------------------------------------------- serial

def start_serial(out_name, seconds):
    """Spawn serial_boot_log.py. It attaches WITHOUT resetting the chip.

    A separate process rather than inline pyserial, deliberately: there is one
    implementation of the no-reset attach in this folder and it is that script.
    On the ESP32-C3 the console is USB-serial/JTAG with DTR and RTS wired to
    reset and boot select, so an ordinary pyserial open reboots the device --
    and rebooting the device whose boot you came to observe loses the event.
    """
    out = os.path.join(RUNS, out_name)
    if not os.path.isdir(RUNS):
        os.makedirs(RUNS)
    if os.path.exists(out):
        os.rename(out, out + ".prev")
    cmd = [sys.executable, os.path.join(HERE, "serial_boot_log.py"),
           "--port", PORT, "--out", out, "--seconds", "%.0f" % seconds]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    # Give it time to open the port and write its header; if it cannot get
    # COM4 we must find out now and not after the OTA has been pushed.
    for _ in range(40):
        time.sleep(0.25)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return proc, out
        if proc.poll() is not None:
            raise RuntimeError("serial_boot_log.py exited at once: %s"
                               % (proc.stdout.read() if proc.stdout else "?"))
    raise RuntimeError("serial_boot_log.py produced nothing in 10 s; "
                       "is COM4 held by another process?")


def read_serial(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = re.match(r"^\[\s*([0-9.]+)\]\s(.*)$", line)
            if m:
                out.append((float(m.group(1)), m.group(2)))
            elif line.startswith("#"):
                out.append((None, line))
    return out


# ---------------------------------------------------------- console parsing

#
# WHAT THE CONSOLE CAN AND CANNOT SHOW, measured 2026-10-04 and NOT what the
# first version of this file assumed.
#
# `main.c:623` -- the LAST statement of app_main -- is
# `esp_log_level_set("*", ESP_LOG_NONE)`. It is stock wican-fw (c941591e "disable loging",
# upstream), not ours. Everything the firmware logs after app_main returns is
# therefore suppressed at runtime. Established with
# `console_liveness_probe.py`: a POST that definitely logs (mode 1 enters
# OBSERVE and `gen_inhibit.c` logs `OBSERVE: re-opening bus listen-only` at
# WARN, and every failure path there logs too) produced 200 OK and ZERO console
# lines, with DTR deasserted and again with DTR asserted.
#
# So these lines EXIST IN THE SOURCE AND WILL NEVER BE SEEN:
#   - `Starting OTA`, `Running partition ...`, `Writing to partition subtype N`
#     (config_server.c, during the upload)
#   - `UNHEALTHY: after 60000 ms ...` and `rolling back to the previous slot`
#     (ota_health.c, SIXTY SECONDS after boot)
# and these DO appear, because they land before app_main returns:
#   - the bootloader banner, partition table and `Loaded app from partition`
#   - `gate armed: running <slot>, state <state>`
#   - `subsystem up: 0x..`, `FAULT INJECT: ...`, and in the healthy case
#     `marking image valid, rollback cancelled`
#
# The rollback's own announcement is the one casualty, so the rollback is
# established from the SLOT FLIP BETWEEN TWO BOOTS plus the otadata state,
# not from a log line. The interval between the two boots is also the
# measurement that the gate waited its full timeout.
#
# I CALLED THIS A DEFECT AND IT IS NOT ONE. ota_health.c's third branch says
# "Staying up degraded; recover over USB if unreachable", so being LOUD reads as
# its designed fallback -- and it is mute. From that I concluded a real rollback
# on the truck would leave no record of why.
#
# RULED 2026-10-05 (user): a bench nuisance, not a truck risk. No logging fix,
# no spec change. The rollback branch is guarded by
# `state == ESP_OTA_IMG_PENDING_VERIFY`, which only an OTA produces, and the
# flight build goes onto the same physical unit AT THE BENCH -- so a rollback
# cannot happen in the truck. The console is unreachable there anyway without
# opening the case, and the degraded branch only fires once the device is
# already unreachable, with CAN deliberately ungated.
#
# The measurement below is right and the conclusion I drew from it was wrong:
# I reasoned about the gate as code without asking WHERE THE DEVICE IS when
# each branch runs. The guard was in front of me -- `judge_fault` exists because
# branch 3 needs PENDING_VERIFY -- and I still generalised from "the gate is
# mute" to "the truck is exposed". What replaced the fix is better than it:
# bring-up row 1.5b waits 60 s after any update and then confirms HTTP
# `git_version` names the new build, which catches a silent rollback through the
# one channel the truck actually has. `phase_verify` now enforces that wait.
#
RE_GATE_ARMED = re.compile(
    r"gate armed: running (\S+), state (\S+), waiting up to (\d+) ms "
    r"for 0x([0-9a-fA-F]+)")
RE_RST = re.compile(r"rst:0x([0-9a-fA-F]+) \(([^)]*)\)")
RE_LOADED = re.compile(r"Loaded app from partition at offset 0x?0*([0-9a-fA-F]+)")
RE_PARTROW = re.compile(
    r"boot:\s+\d+\s+(\S+)\s+.*?\s([0-9a-f]{8})\s+([0-9a-f]{8})\s*$")
RE_UNHEALTHY = re.compile(
    r"UNHEALTHY: after (\d+) ms have 0x([0-9a-fA-F]+), "
    r"need 0x([0-9a-fA-F]+) \(missing 0x([0-9a-fA-F]+)\)")
RE_FAULT = re.compile(r"FAULT INJECT: suppressing report of 0x([0-9a-fA-F]+)")
RE_SUBSYS = re.compile(r"subsystem up: 0x([0-9a-fA-F]+)")
# The ROM's banner, printed on every boot whatever the bootloader and the app are
# built at (port step 1a2): "ESP-ROM:esp32c3-api1-20210207", then "rst:0x.. (..)".
RE_ROM = re.compile(r"ESP-ROM:")
# A bootloader log line, "I (31) boot: ...", colour codes allowed: counted per boot,
# as a sign of the level the unit's bootloader logs at.
RE_BOOTLOG = re.compile(r"^(?:\x1b\[[0-9;]*m)?[EWI] \(\d+\) boot")


def slot_map(lines):
    """Offset -> slot label, taken from the bootloader's OWN partition table.

    It prints the table on every boot, so the capture is self-describing and
    nothing has to be computed. The first version of this file computed
    ota_1 = 0x10000 + 1740K = 0x1C3000 and was WRONG: the CSV leaves ota_1's
    offset blank, app partitions must be 64K-aligned, and the real table says
    0x1D0000. Every slot comparison silently compared against None.
    """
    m = {}
    for _t, text in lines:
        hit = RE_PARTROW.search(text)
        if hit:
            m[int(hit.group(2), 16)] = hit.group(1)
    for label, off in SLOT_OFFSET.items():
        m.setdefault(off, label)
    return m


def console_events(lines):
    """Structured events in order, with host timestamps.

    Timestamps carry the one measurement that survives the log silencing: the
    interval between two boots. In the fault case the gate's 60 s wait is only
    observable as that gap, because the line announcing it is suppressed.
    """
    offs = slot_map(lines)
    ev = []
    for t, text in lines:
        m = RE_GATE_ARMED.search(text)
        if m:
            ev.append({"t": t, "kind": "gate_armed", "slot": m.group(1),
                       "state": m.group(2), "timeout_ms": int(m.group(3)),
                       "need": int(m.group(4), 16)})
            continue
        m = RE_UNHEALTHY.search(text)
        if m:
            ev.append({"t": t, "kind": "unhealthy",
                       "after_ms": int(m.group(1)),
                       "have": int(m.group(2), 16),
                       "need": int(m.group(3), 16),
                       "missing": int(m.group(4), 16)})
            continue
        m = RE_FAULT.search(text)
        if m:
            ev.append({"t": t, "kind": "fault_inject",
                       "mask": int(m.group(1), 16)})
            continue
        if RE_ROM.search(text):
            ev.append({"t": t, "kind": "rom"})
            continue
        m = RE_RST.search(text)
        if m:
            ev.append({"t": t, "kind": "reset", "code": m.group(1),
                       "reason": m.group(2)})
            continue
        m = RE_LOADED.search(text)
        if m:
            off = int(m.group(1), 16)
            ev.append({"t": t, "kind": "boot_load", "offset": off,
                       "slot": offs.get(off)})
            continue
        m = RE_SUBSYS.search(text)
        if m:
            ev.append({"t": t, "kind": "subsystem_up",
                       "bits": int(m.group(1), 16)})
            continue
        if RE_BOOTLOG.search(text):
            ev.append({"t": t, "kind": "bootloader_log"})
            continue
        if "marking image valid, rollback cancelled" in text:
            ev.append({"t": t, "kind": "marked_valid"})
        elif "nothing to do" in text and "image already" in text:
            ev.append({"t": t, "kind": "already_valid", "text": text})
        elif "rolling back to the previous slot" in text:
            ev.append({"t": t, "kind": "rolling_back"})
        elif "no rollback target" in text:
            ev.append({"t": t, "kind": "no_rollback_target", "text": text})
    return ev


def of_kind(ev, kind):
    return [e for e in ev if e["kind"] == kind]


def boots(ev):
    """Group events into boots, keyed on the ROM banner (port step 1a2).

    One dict per boot: its banner lines, the reset reason, which slot the
    bootloader said it loaded (when it said), how many bootloader lines it
    printed, what the gate saw, which subsystems reported, whether the fault
    fired, and how the gate resolved. Judging per boot rather than over a flat
    event list is what makes "the second boot has no fault line" expressible.

    A boot starts at an `ESP-ROM:` line, or at an `rst:0x..` line unless the
    current boot began with an `ESP-ROM:` line and has no `rst:` yet (the two
    lines of one banner). So a boot whose `ESP-ROM:` line was lost still counts,
    and one banner is one boot. Until 1a2 a boot started at the bootloader's
    `Loaded app from partition`, which a bootloader built at error never prints.
    """
    out = []
    cur = None
    for e in ev:
        k = e["kind"]
        if k == "rom" or (k == "reset" and not (cur and cur["banner"] == ["rom"])):
            cur = {"t": e["t"], "banner": [], "rst": None, "offset": None, "slot": None,
                   "bootloader_lines": 0,
                   "gate": None, "subsystems": [], "fault_masks": [],
                   "marked_valid": False, "already_valid": False,
                   "unhealthy": None, "rolling_back": False}
            out.append(cur)
        if cur is None:
            continue
        if k == "rom":
            cur["banner"].append("rom")
        elif k == "reset":
            cur["banner"].append("rst")
            cur["rst"] = "0x%s %s" % (e["code"].lower(), e["reason"])
        elif k == "boot_load":
            cur["offset"], cur["slot"] = e["offset"], e["slot"]
            cur["bootloader_lines"] += 1
        elif k == "bootloader_log":
            cur["bootloader_lines"] += 1
        elif k == "gate_armed":
            cur["gate"] = {"slot": e["slot"], "state": e["state"],
                           "timeout_ms": e["timeout_ms"], "need": e["need"]}
        elif k == "subsystem_up":
            cur["subsystems"].append(e["bits"])
        elif k == "fault_inject":
            cur["fault_masks"].append(e["mask"])
        elif k == "marked_valid":
            cur["marked_valid"] = True
        elif k == "already_valid":
            cur["already_valid"] = True
        elif k == "unhealthy":
            cur["unhealthy"] = {"after_ms": e["after_ms"], "have": e["have"],
                                "need": e["need"], "missing": e["missing"]}
        elif k == "rolling_back":
            cur["rolling_back"] = True
    return out


# ---------------------------------------------------------------- OTA push

CRLF = "\r\n"


def build_multipart(payload, boundary="b"):
    """The handler drains a trailing boundary after the image, so the body has
    to be real multipart -- a bare octet-stream body leaves that final
    httpd_req_recv with nothing to read and the handler reports
    'Failed to receive file' after writing the whole image."""
    pre = ("--" + boundary + CRLF +
           'Content-Disposition: form-data; name="f"' + CRLF +
           "Content-Type: a/b" + CRLF + CRLF).encode("ascii")
    post = (CRLF + "--" + boundary + "--" + CRLF).encode("ascii")
    return pre + payload + post, boundary


def post_ota(path, host=HOST, timeout=420.0):
    """Plain single POST. `paced_upload.py` is obsolete against this handler.

    The pacing workaround existed because the pre-fix handler advanced its recv
    pointer past the multipart preamble and never restored it, so a full-size
    recv wrote past the end of a 4096-byte scratch buffer. The fix reads into
    the start of scratch every time, so full-size reads are safe and the
    trickle is no longer needed. If this ever starts failing mid-upload again,
    suspect the handler before suspecting the uploader.

    esp_ota_begin(OTA_SIZE_UNKNOWN) erases the whole 1740K slot before reading
    a byte, so the first tens of seconds are TCP flow control with the device
    reading nothing. That is expected; the timeout has to cover it.
    """
    payload = open(path, "rb").read()
    body, boundary = build_multipart(payload)
    url = "http://%s/upload/ota.bin" % host
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type",
                   "multipart/form-data; boundary=%s" % boundary)
    req.add_header("Content-Length", str(len(body)))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status,
                    "body": r.read().decode("ascii", "replace").strip()[:200],
                    "seconds": round(time.time() - t0, 1),
                    "payload_bytes": len(payload), "body_bytes": len(body)}
    except Exception as exc:
        # The device starts a 2000 ms restart timer before replying, so a
        # truncated or reset connection here is a normal outcome, not a
        # failure. The console is the authority on what happened.
        return {"status": None, "body": "%s: %s" % (type(exc).__name__, exc),
                "seconds": round(time.time() - t0, 1),
                "payload_bytes": len(payload), "body_bytes": len(body)}


# ---------------------------------------------------------------- otadata

def parse_otadata(blob):
    """Parse the two otadata sectors from a raw 0x2000 read.

    Layout per sector (esp_ota_select_entry_t): uint32 ota_seq, 20-byte label,
    uint32 ota_state, uint32 crc.

    THE CRC CONVENTION WAS MEASURED, NOT ASSUMED, against this device's own
    sector 0 on 2026-10-04: seq 1 stores crc 0x4743989A, and
    `zlib.crc32(b"\\x01\\x00\\x00\\x00", 0xFFFFFFFF)` is exactly that -- i.e.
    `esp_rom_crc32_le(UINT32_MAX, &seq, 4)` is zlib's crc32 continued from an
    init of 0xFFFFFFFF, with no final inversion. The two forms that do NOT
    work are kept and reported beside it, because a wrong convention marks
    every sector invalid and that is indistinguishable from a blank otadata.

    A BLANK SECTOR'S CRC VALIDATES UNDER THE WRONG FORMULA, which is why the
    verdict is suppressed for blank sectors rather than reported. An erased
    sector holds seq 0xFFFFFFFF and crc 0xFFFFFFFF, and
    `zlib.crc32(b"\\xff\\xff\\xff\\xff")` happens to BE 0xFFFFFFFF -- so the
    plain form "matched" sector 1 here while failing sector 0, and taking that
    as support would have confirmed the wrong convention off the one sector
    that cannot discriminate.
    """
    import struct
    out = []
    for i in range(2):
        sec = blob[i * 0x1000: i * 0x1000 + 32]
        if len(sec) < 32:
            out.append({"index": i, "error": "short read"})
            continue
        seq, label, state, crc = struct.unpack("<I20sII", sec)
        raw = sec[0:4]
        blank = (seq == 0xFFFFFFFF)
        cand = {
            "zlib_init_ffffffff": zlib.crc32(raw, 0xFFFFFFFF) & 0xFFFFFFFF,
            "zlib_plain": zlib.crc32(raw) & 0xFFFFFFFF,
            "zlib_inverted": (zlib.crc32(raw) ^ 0xFFFFFFFF) & 0xFFFFFFFF,
        }
        matched = [k for k, v in cand.items() if v == crc]
        out.append({
            "index": i,
            "slot": "ota_%d" % i,
            "ota_seq": seq,
            "blank": blank,
            "ota_state": state,
            "ota_state_name": OTA_STATE.get(state, "0x%08X" % state),
            "crc_stored": crc,
            "crc_candidates": cand,
            "crc_matched": None if blank else matched,
            "crc_ok": None if blank else ("zlib_init_ffffffff" in matched),
        })
    # WHICH SLOT THE BOOTLOADER WILL CHOOSE, and "highest seq wins" is NOT the
    # rule. It skips entries marked INVALID or ABORTED when rollback is
    # enabled (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y here) and falls back to
    # the next candidate -- which is the entire mechanism this rehearsal
    # exercises.
    #
    # The first version of this function took the highest seq unconditionally.
    # Immediately after the rollback that is sector 0, seq 3, state INVALID --
    # so it reported "boot: ota_0 INVALID" about a device the console showed
    # booting ota_1, i.e. a confident label on the one slot the bootloader had
    # just refused. It was caught only because the console said otherwise;
    # nothing in the otadata read itself looked wrong. Both paths are kept and
    # `rejected` records what was skipped and why, so the skip is visible
    # rather than implied.
    SKIP = (3, 4)       # INVALID, ABORTED
    live = [s for s in out if not s.get("blank") and "error" not in s]
    usable = [s for s in live if s["ota_state"] not in SKIP]
    boot = None
    if usable:
        best = max(usable, key=lambda s: s["ota_seq"])
        boot = {"from_sector": best["index"], "ota_seq": best["ota_seq"],
                "slot": "ota_%d" % ((best["ota_seq"] - 1) % 2),
                "state_name": best["ota_state_name"],
                "rejected": [{"sector": s["index"], "ota_seq": s["ota_seq"],
                              "state_name": s["ota_state_name"],
                              "why": "state is INVALID or ABORTED, so the "
                                     "bootloader skips it"}
                             for s in live if s["ota_state"] in SKIP],
                "rule": "highest ota_seq among entries NOT marked "
                        "INVALID/ABORTED; slot is (seq - 1) % 2"}
    elif live:
        boot = {"slot": None, "state_name": None,
                "why": "every non-blank otadata entry is INVALID or ABORTED; "
                       "the bootloader would fall back further than this "
                       "parser models",
                "rejected": [{"sector": s["index"], "ota_seq": s["ota_seq"],
                              "state_name": s["ota_state_name"]}
                             for s in live]}
    return {"sectors": out, "boot": boot}


def read_otadata(fw_root, out_bin):
    """esptool read of 0xd000..0xf000. RESETS THE DEVICE.

    Run from the IDF environment in PowerShell: esptool is not installed under
    py -3.14 on this machine (python-executables-reference.md), and the IDF venv
    has 4.12.0. The command is returned rather than run when esptool is absent,
    so the caller can say what to run instead of failing obscurely.
    """
    cmd = ["esptool.py", "--chip", "esp32c3", "--port", PORT, "--baud",
           "460800", "read_flash", "0xd000", "0x2000", out_bin]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {"ran": True, "returncode": p.returncode,
                "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:],
                "cmd": " ".join(cmd)}
    except FileNotFoundError:
        return {"ran": False, "cmd": " ".join(cmd),
                "why": "esptool not on PATH for this interpreter; run the cmd "
                       "from the IDF PowerShell environment, then re-run this "
                       "phase with --otadata-file"}


# ---------------------------------------------------------------- phases

def phase_images(args):
    good = image_facts(args.good)
    faulty = image_facts(args.faulty)
    rows = judge_images(good, faulty)
    log("=== images ===")
    for k, v in (("good", good), ("faulty", faulty)):
        if v.get("exists"):
            log("  %-6s %-30s %8d  %s  fault_string=%s  fault_symbol=%s%s"
                % (k, v["project_name"], v["size"], v["md5"],
                   v["has_fault_string"], v["has_fault_symbol"],
                   " (%s)" % v["fault_symbol_why"] if v["fault_symbol_why"] else ""))
        else:
            log("  %-6s MISSING %s" % (k, v["path"]))
    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec = {"good": good, "faulty": faulty,
           "rows": dict(rows), "fails": fails,
           "verdict": "PASS" if not fails else "FAIL"}
    save("images", rec)
    return 0 if not fails else 1


def esptool_unit(res):
    """(seen, matches): whether esptool printed a MAC, and whether it is EXPECT_MAC; None
    when it printed none. Value-blind (2026-10-30): the MAC is the unit's serial number,
    so neither value is printed or recorded, and the MAC is cut out of the esptool output
    kept in res (stdout, stderr) before anything saves it. Until then the otadata and
    bootloader phases printed both MACs and recorded them in the run file."""
    m = RE_ESPTOOL_MAC.search(res.get("stdout") or "")
    for k in ("stdout", "stderr"):
        if res.get(k):
            res[k] = RE_ESPTOOL_MAC.sub("MAC: <redacted>", res[k])
    if not m:
        return False, None
    return True, m.group(1).lower() == EXPECT_MAC


def connected_bssid(run=subprocess.run):
    """(bssid, None): the BSSID of the access point this host is joined to, from `netsh wlan
    show interfaces`, as 12 lower-case hex digits; or (None, why) when there is not exactly
    one. It is the unit's SoftAP MAC, so it is never printed or recorded (reviewer,
    2026-10-25)."""
    try:
        out = run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                  timeout=15).stdout or ""
    except (OSError, subprocess.SubprocessError) as e:
        return None, "netsh failed (%s)" % type(e).__name__
    found = [re.sub(r"[^0-9a-f]", "", m.lower())
             for m in re.findall(r"^\s*(?:AP )?BSSID\s*:\s*(\S+)", out, re.M)]
    found = [b for b in found if len(b) == 12]
    if len(found) != 1:
        return None, "%d BSSIDs read from netsh, not one" % len(found)
    return found[0], None


def unit_check(expect_device_id, read=None):
    """(ok, why): is the access point this host is joined to the expected unit?

    The connected BSSID is the SoftAP MAC, the value `device_id` on /check_status carried
    (checked value-blind on the bench, 2026-10-25). It is compared with the settings'
    wican.device_id and neither is printed, recorded or returned. No expectation, or no
    single BSSID, fails closed. /check_status is never read (reviewer, 2026-10-20; this row
    moved off it 2026-10-25)."""
    want = re.sub(r"[^0-9a-f]", "", (expect_device_id or "").lower())
    if len(want) != 12:
        return False, "no expected device id of 12 hex digits"
    got, why = (read or connected_bssid)()
    if got is None:
        return False, why
    return (True, "the connected BSSID matches") if got == want else (False, "the connected BSSID does not match")


def device_state(expect_device_id=None):
    """The device's state from /gen_inhibit, and whether the AP this host is joined to is the
    expected unit (unit_check). NEVER /check_status (reviewer, 2026-10-20): it answers with
    the device's config, credentials included. Until 2026-10-25 this read device_id,
    git_version, fw_version, hw_version, wifi_mode and batt_voltage from it."""
    gi = http_json("/gen_inhibit")
    ok, why = unit_check(EXPECT_DEVICE_ID if expect_device_id is None else expect_device_id)
    return {
        "unit_matches": ok,
        "unit_why": why,
        "elf_sha256": gi.get("elf_sha256"),
        "uptime_ms": gi.get("uptime_ms"),
        "mode": gi.get("mode"),
        "bus_on": gi.get("bus_on"),
        "bus_ours": gi.get("bus_ours"),
        "inhibit_live": gi.get("inhibit_live"),
        "tx_ok": gi.get("tx_ok"),
        "arm_block": gi.get("arm_block"),
        "abort_reason": gi.get("abort_reason"),
        "abort_latched": gi.get("abort_latched"),
    }


def ota_precondition(state):
    """Spec 9.3 refuses OTA in any armed mode, and the handler returns 403.

    Checked here too so the refusal arrives before an image is pushed rather
    than as an HTTP error in the middle of one.

    THE UNIT IS CHECKED FIRST, because it is the one row whose failure means
    everything after it is about the wrong device. The MAC is the unit's serial
    number (user, 2026-10-05). `device_id` on /check_status was the only identity
    HTTP exposed, and that page is never read (reviewer, 2026-10-20); the row reads
    the same SoftAP MAC as the connected AP's BSSID (unit_check, 2026-10-25).
    """
    rows = [
        ("the connected AP is the expected unit (BSSID, value-blind)",
         state.get("unit_matches") is True),
        ("mode is 0", state["mode"] == 0),
        ("not live", state["inhibit_live"] is False),
        ("bus not ours", state["bus_ours"] is False),
    ]
    return rows


def phase_baseline(args):
    log("=== baseline ===")
    state = device_state(args.expect_device_id)
    for k in sorted(state):
        log("  %-14s %s" % (k, state[k]))
    rows = ota_precondition(state)
    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec = {"state": state, "rows": dict(rows), "fails": fails,
           "verdict": "PASS" if not fails else "FAIL"}
    save("baseline", rec)
    return 0 if not fails else 1


def phase_otadata(args):
    log("=== otadata ===")
    out_bin = os.path.join(RUNS, "otadata_%s.bin" % ts())
    if args.otadata_file:
        out_bin = args.otadata_file
        res = {"ran": False, "why": "parsing a file supplied with "
                                    "--otadata-file", "cmd": None}
    else:
        if not os.path.isdir(RUNS):
            os.makedirs(RUNS)
        res = read_otadata(args.fw, out_bin)
        if not res["ran"]:
            log("  esptool unavailable here. Run this from the IDF PowerShell "
                "environment:")
            log("    " + res["cmd"])
            log("  then: --phase otadata --otadata-file " + out_bin)
            save("otadata", {"esptool": res, "verdict": "NOT_RUN"})
            return 2
    seen, mac_ok = esptool_unit(res)
    if not os.path.exists(out_bin):
        log("  FAIL: no otadata dump at %s" % out_bin)
        save("otadata", {"esptool": res, "verdict": "FAIL"})
        return 1
    if not seen:
        log("  esptool printed no MAC, so the unit is unconfirmed for this read")
    else:
        log("  esptool MAC: %s the expected unit's" % ("MATCHES" if mac_ok else "DOES NOT MATCH"))
    if seen and not mac_ok:
        # Hard stop: a wrong --port most likely means COM3, which is another
        # session's interposer, and closing its port resets it mid-run.
        log("  FAIL: this is not the expected unit. Check --port.")
        save("otadata", {"esptool": res, "mac_matches": False, "verdict": "FAIL"})
        return 1
    parsed = parse_otadata(open(out_bin, "rb").read())
    for s in parsed["sectors"]:
        log("  sector %d  seq=%s blank=%s state=%-14s crc_ok=%s"
            % (s.get("index"), s.get("ota_seq"), s.get("blank"),
               s.get("ota_state_name"), s.get("crc_ok")))
    log("  boot: %s" % parsed["boot"])
    save("otadata", {"esptool": res, "dump": out_bin, "parsed": parsed,
                     "mac_matches": mac_ok, "verdict": "RECORDED"})
    return 0


def reboot_check(label, good_sha):
    """Reboot the device with wican_reboot.py, wanting the good image, and return its
    record (verdict, found_sha, checks: a new boot seen from the page's uptime, reset
    reason SW, mode 0). It is how the control and restore phases show the gate marked
    the image valid without the console (port step 1a2): a PENDING_VERIFY image is
    rolled back by the bootloader at this reboot."""
    if not (isinstance(good_sha, str) and len(good_sha) == 64):
        return {"verdict": "FAIL", "found_sha": None, "detail": "no elf_sha256 for the good image"}
    import wican_reboot
    out = os.path.join(RUNS, "rollback_%s_reboot_%s.json" % (label, ts()))
    wican_reboot.main(["--label", "rollback_%s" % label, "--out", out,
                       "--known", "good=%s" % good_sha, "--want", "good"])
    try:
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        return {"verdict": "FAIL", "found_sha": None, "detail": "no reboot record: %s" % e}


def _do_ota(args, label, image, watch_s, good_sha=None, reboot=False):
    """Shared body of the control, fault and restore phases.

    Serial is opened BEFORE the POST and stays open across the reboot. The
    device starts its restart timer before replying, so a capture attached
    afterwards misses the boot -- which is the whole event.

    good_sha is the good image's elf_sha256, which the judges compare the page
    with. With reboot, the device is rebooted after the watch window (reboot_check)
    and the record keeps what that found.
    """
    facts = image_facts(image)
    if not facts.get("exists"):
        log("  FAIL: image missing: %s" % image)
        return {"verdict": "FAIL", "why": "image missing", "image": facts}, 1

    before = device_state(args.expect_device_id)
    pre = ota_precondition(before)
    bad = [n for n, ok in pre if not ok]
    if bad:
        log("  FAIL preconditions: %s" % ", ".join(bad))
        return ({"verdict": "FAIL", "why": "preconditions", "before": before,
                 "rows": dict(pre)}, 1)

    log("  image  %s  %d bytes  md5 %s"
        % (facts["project_name"], facts["size"], facts["md5"]))
    log("  before elf_sha256=%s mode=%s"
        % ((before.get("elf_sha256") or "")[:16], before["mode"]))

    serial_name = "rollback_%s_console.log" % label
    proc, serial_path = start_serial(serial_name, watch_s)
    log("  serial capturing -> %s for %.0f s"
        % (os.path.relpath(serial_path, "."), watch_s))
    time.sleep(1.0)

    push = post_ota(image)
    log("  POST %s in %.0f s: %s"
        % (push["status"], push["seconds"], push["body"][:80]))

    # Wait out the whole capture window rather than stopping at the first
    # interesting line: the gate alone is 60 s AFTER the boot, and the rollback
    # reboot comes after that. Progress is printed from the log as it grows so
    # a long wait does not look like a hang.
    t_wait = time.time()
    seen = 0
    while proc.poll() is None:
        time.sleep(10.0)
        lines = read_serial(serial_path)
        if len(lines) != seen:
            seen = len(lines)
            log("    +%4.0f s  %d console lines" % (time.time() - t_wait, seen))
        if time.time() - t_wait > watch_s + 60:
            log("    serial_boot_log.py overran its window; terminating it")
            proc.terminate()
            break
    try:
        proc.wait(timeout=30)
    except Exception:
        proc.kill()

    lines = read_serial(serial_path)
    ev = console_events(lines)
    log("  console: %d lines, %d events" % (len(lines), len(ev)))
    for e in ev:
        if e["kind"] != "bootloader_log":
            log("    [%7s] %s" % ("%.1f" % e["t"] if e["t"] is not None else "-",
                                  json.dumps(e, sort_keys=True)))

    after = None
    for _ in range(24):
        try:
            after = device_state(args.expect_device_id)
            break
        except Exception:
            time.sleep(5.0)
    log("  after  elf_sha256=%s uptime_ms=%s"
        % (((after or {}).get("elf_sha256") or "")[:16], (after or {}).get("uptime_ms")))

    rb = None
    if reboot and after is not None:
        rb = reboot_check(label, good_sha)
        log("  reboot %s, found %s" % (rb.get("verdict"), (rb.get("found_sha") or "")[:16]))

    bs = boots(ev)
    log("  boots (ROM banners): %d, at %s, slots said %s, bootloader lines %s"
        % (len(bs), [b["t"] for b in bs], [b["slot"] for b in bs], [b["bootloader_lines"] for b in bs]))
    return ({"image": facts, "good_elf_sha256": good_sha, "before": before, "after": after,
             "reboot": rb, "push": push, "serial": os.path.relpath(serial_path, "."),
             "events": ev, "boots": bs}, 0)


HTTPD_BIT = 0x02
WIFI_AP_BIT = 0x01


def judge_control(rec, require_change=True):
    """The gate must have passed the GOOD image: judged by its effect (port step 1a2).

    The good image logs nothing (app none), so its gate lines are gone, and the
    gate is judged from the page instead (gen-inhibit reviewer, 2026-10-30):
    one boot (ROM banner); the page names the good image, up past the gate's
    timeout; after a reboot it still does. An image the gate never marked valid
    is still PENDING_VERIFY at that reboot, and the bootloader rolls it back,
    so the page would name the other slot's image. That is the whole point of
    the phase -- it shows the gate PASSES a good image, so that the fault
    phase's rollback means "the gate judged this image" rather than "the gate
    trips on everything".

    require_change (control): the device ran another image before the OTA, so
    the page can show the OTA took. The restore phase OTAs the good image onto
    itself and cannot show that; its record keeps the before image.

    Until 1a2 the rows were the gate's own lines: armed on PENDING_VERIFY,
    waiting for 0x03, both subsystems up, "marking image valid", not the
    "already VALID" branch, and the boot's slot.
    """
    ev = rec.get("events") or []
    bs = rec.get("boots") or boots(ev)
    good = rec.get("good_elf_sha256") or (rec.get("image") or {}).get("elf_sha256")
    before = rec.get("before") or {}
    after = rec.get("after") or {}
    rb = rec.get("reboot") or {}
    rows = [("exactly one boot in the window (ROM banner)", len(bs) == 1)]
    if require_change:
        rows.append(("the device ran another image before the OTA (page elf_sha256)",
                     bool(good) and bool(before.get("elf_sha256")) and before.get("elf_sha256") != good))
    rows += [
        ("the new boot runs the good image (page elf_sha256)", bool(good) and after.get("elf_sha256") == good),
        ("it was up past the gate's timeout before the reboot (page uptime_ms >= %d)" % GATE_SETTLED_MS,
         (after.get("uptime_ms") or 0) >= GATE_SETTLED_MS),
        ("the reboot passed: a new boot, reset reason SW, mode 0 (wican_reboot.py)", rb.get("verdict") == "PASS"),
        ("after the reboot the page still names the good image, so the gate marked it valid",
         bool(good) and rb.get("found_sha") == good),
        ("no fault-inject line", bool(bs) and not bs[0]["fault_masks"]),
        ("device answered afterwards", rec.get("after") is not None),
    ]
    return rows


def judge_fault(rec, expect_mask=1):
    """The rollback is established from the SLOT FLIP, not from a log line.

    `UNHEALTHY` and `rolling back to the previous slot` are emitted 60 s after
    boot, by which time app_main has run `esp_log_level_set("*", ESP_LOG_NONE)`
    -- so they cannot appear, and requiring them would fail a working rollback.
    What remains observable is strictly enough, and in two cases better:

      boot 1  loads the faulty slot, the gate arms on a PENDING_VERIFY image,
              `FAULT INJECT: suppressing report of 0x01` fires, and HTTPD
              reports while WIFI_AP does NOT.
      ~60 s   silence.
      boot 2  loads a DIFFERENT slot, with no fault line, and its gate sees an
              image that is already VALID.

    `HTTPD reported and WIFI_AP did not` carries exactly what the suppressed
    `have 0x02, need 0x03 (missing 0x01)` line would have carried, derived
    independently: the gate was alive and short of one named bit, not starved
    of everything. That is what separates a working gate from a gate that
    trips on anything, and it is why the mask suppresses one bit and not both.

    The 60 s wait survives as the interval between the two boots. A rollback
    that happened in 2 s did not happen because the gate timed out.

    PORT STEP 1a2 (gen-inhibit reviewer, 2026-10-30): the boots are the ROM
    banners, timed between banners, and the good image it rolls back to logs
    nothing. So the second boot's slot and its gate line ("a DIFFERENT slot",
    "not PENDING_VERIFY") are no longer read; the rollback is shown by the page
    naming the good image afterwards, after naming it before the OTA. The first
    boot's rows are the _flt image's own lines, still logged at info.
    """
    ev = rec.get("events") or []
    bs = rec.get("boots") or boots(ev)
    b1 = bs[0] if len(bs) >= 1 else None
    b2 = bs[-1] if len(bs) >= 2 else None
    gap = (b2["t"] - b1["t"]) if (b1 and b2 and b1["t"] is not None
                                  and b2["t"] is not None) else None
    good = rec.get("good_elf_sha256")
    faulty = (rec.get("image") or {}).get("elf_sha256")
    before = rec.get("before") or {}
    after = rec.get("after") or {}

    rows = [
        ("booted exactly twice (ROM banners)", len(bs) == 2),
        ("the faulty image is not the good one (descriptors' elf_sha256)",
         bool(good) and bool(faulty) and good != faulty),
        ("the device ran the good image before the OTA (page elf_sha256)",
         bool(good) and before.get("elf_sha256") == good),
        ("first boot's gate armed on PENDING_VERIFY",
         bool(b1) and bool(b1["gate"])
         and b1["gate"]["state"] == "PENDING_VERIFY"),
        ("fault injection fired on the first boot",
         bool(b1) and bool(b1["fault_masks"])),
        ("fault mask is 0x%02x" % expect_mask,
         bool(b1) and bool(b1["fault_masks"])
         and all(m == expect_mask for m in b1["fault_masks"])),
        ("HTTPD reported on the first boot",
         bool(b1) and HTTPD_BIT in b1["subsystems"]),
        ("WIFI_AP did NOT report on the first boot",
         bool(b1) and WIFI_AP_BIT not in b1["subsystems"]),
        ("first boot did not mark itself valid",
         bool(b1) and not b1["marked_valid"]),
        ("second boot exists", b2 is not None),
        ("no fault-inject line on the second boot",
         bool(b2) and not b2["fault_masks"]),
        ("the two boots are 55-75 s apart (ROM banners)",
         gap is not None and 55.0 <= gap <= 75.0),
        ("back on the good image afterwards (page elf_sha256)",
         bool(good) and after.get("elf_sha256") == good),
        ("device answered afterwards", rec.get("after") is not None),
    ]
    return rows, {
        "boot_gap_s": round(gap, 2) if gap is not None else None,
        "boot_slots": [b["slot"] for b in bs],
        "boot_rst": [b["rst"] for b in bs],
        "bootloader_lines": [b["bootloader_lines"] for b in bs],
        "note": "UNHEALTHY and 'rolling back' are absent BY DESIGN -- "
                "main.c:623 runs esp_log_level_set(\"*\", ESP_LOG_NONE) at the "
                "end of app_main, and the gate resolves 60 s later. Their "
                "absence is not evidence of anything; the boot gap and the "
                "page's image before and after are (the slot is not read "
                "since port step 1a2: the good image logs nothing).",
    }


def run_ota_phase(args, label, image, watch_s, judger, expect_mask=1):
    log("=== %s ===" % label)
    good_sha = image_facts(args.good).get("elf_sha256")
    rec, rc = _do_ota(args, label, image, watch_s, good_sha=good_sha, reboot=judger is judge_control)
    if rc:
        save(label, rec)
        return rc
    extra = {}
    if judger is judge_fault:
        rows, extra = judge_fault(rec, expect_mask)
    else:
        rows = judger(rec, require_change=label == "control")
    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec["rows"] = dict(rows)
    rec["fails"] = fails
    rec["measured"] = extra
    rec["verdict"] = "PASS" if not fails else "FAIL"
    log("  verdict: %s%s" % (rec["verdict"],
                             "" if not fails else "  (%d)" % len(fails)))
    save(label, rec)
    return 0 if not fails else 1


# One string per log level that a bootloader image holds only when built at that level or
# a more verbose one (ESP-IDF v5.4, bootloader_support/src/bootloader_utility.c: an
# ESP_LOGI, an ESP_LOGW, an ESP_LOGE). Port step 1a2 records the level the unit's
# bootloader shows (gen-inhibit reviewer, 2026-10-30); another IDF's bootloader may word
# them differently, and then the level reads "none of the markers".
BOOTLOADER_LEVEL_MARKERS = (
    ("info", b"Loaded app from partition at offset"),
    ("warn", b"Falling back to test app as only bootable partition"),
    ("error", b"No bootable app partitions in the partition table"),
)


def esp_image_length(data):
    """Length of the ESP image at the start of data (header, segments, the checksum at
    the next 16-byte boundary, the appended sha256 when the header says so), or None
    when data does not hold a whole one."""
    if len(data) < 24 or data[0] != 0xE9:
        return None
    off = 24
    for _ in range(data[1]):
        if off + 8 > len(data):
            return None
        off += 8 + int.from_bytes(data[off + 4:off + 8], "little")
    off += 15 - off % 16 + 1
    if data[23] == 1:
        off += 32
    return off if off <= len(data) else None


def bootloader_facts(data):
    """The bootloader image in a 0x0..0x8000 read: its length and sha256, and the most
    verbose log level whose marker string it holds."""
    n = esp_image_length(data)
    if n is None:
        return {"image_bytes": None, "sha256": None, "level": None, "markers": [],
                "why": "no whole ESP image at 0x0"}
    img = data[:n]
    found = [lvl for lvl, s in BOOTLOADER_LEVEL_MARKERS if s in img]
    return {"image_bytes": n, "sha256": hashlib.sha256(img).hexdigest(),
            "level": (found[0] if found else "none of the markers"), "markers": found}


def phase_bootloader(args):
    """WHICH BOOTLOADER is on the device -- the project's, or MeatPi's?

    Spec 9.1 SAID, until this phase disproved it on 2026-10-05, that the WiCAN
    "runs MeatPi's bootloader, which OTA cannot replace and which cannot be
    tested directly", and that the rehearsal exercised that bootloader
    directly. The second half is gone from the spec; a rewrite of 9.1 and a
    bring-up precondition requiring a full `idf.py flash` are with the user.
    Do not re-add the claim to this docstring if 9.1's wording changes again --
    re-run the phase instead, which is why it exists. For a SERIALLY FLASHED
    unit the premise fails: `flasher_args.json` puts
    `bootloader/bootloader.bin` at 0x0, so `idf.py flash` replaces MeatPi's
    bootloader every time. Measured on this unit 2026-10-05 -- the first 21,952
    bytes of flash are byte-identical to `build/bootloader/bootloader.bin`
    (md5 532fc5ed12210045be6d20d421181fb6) and the rest to 0x8000 is erased.

    So the residual gap is narrower and differently shaped than the spec says:
    it applies to a WiCAN that has only ever been OTA'd, because OTA cannot
    replace the bootloader. A unit that has been serially flashed runs the same
    bootloader binary the bench board does, and the flash procedure -- not the
    rehearsal -- is what closes that gap. Which makes the procedure
    load-bearing: `idf.py app-flash` would NOT close it.

    Needs COM4 free and RESETS the device, like the otadata phase. Pass
    --bootloader-file to parse an existing dump.
    """
    log("=== bootloader ===")
    built = os.path.join(args.fw, "build", "bootloader", "bootloader.bin")
    out_bin = args.bootloader_file or os.path.join(
        RUNS, "bootloader_on_device_%s.bin" % ts())
    res = {"ran": False, "cmd": None}
    if not args.bootloader_file:
        if not os.path.isdir(RUNS):
            os.makedirs(RUNS)
        cmd = ["esptool.py", "--chip", "esp32c3", "--port", PORT, "--baud",
               "460800", "read_flash", "0x0", "0x8000", out_bin]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            res = {"ran": True, "returncode": p.returncode,
                   "cmd": " ".join(cmd), "stdout": p.stdout[-1500:]}
        except FileNotFoundError:
            log("  esptool is not on PATH for this interpreter. Run:")
            log("    " + " ".join(cmd))
            log("  then re-run with --bootloader-file " + out_bin)
            save("bootloader", {"esptool": {"cmd": " ".join(cmd)},
                                "verdict": "NOT_RUN"})
            return 2

    seen, mac_ok = esptool_unit(res)
    if seen:
        log("  esptool MAC: %s the expected unit's" % ("MATCHES" if mac_ok else "DOES NOT MATCH"))
        if not mac_ok:
            log("  FAIL: this is not the expected unit. Check --port.")
            save("bootloader", {"esptool": res, "mac_matches": False, "verdict": "FAIL"})
            return 1

    rows = []
    rec = {"esptool": res, "dump": out_bin, "built": built, "mac_matches": mac_ok}
    if os.path.exists(out_bin):
        # the unit's bootloader, whatever it is (port step 1a2): its sha256 and level
        dev = bootloader_facts(open(out_bin, "rb").read())
        rec["device_bootloader"] = dev
        log("  device bootloader: %s bytes, sha256 %s, log level shown: %s (markers %s)"
            % (dev["image_bytes"], dev["sha256"], dev["level"], dev["markers"]))
    if not os.path.exists(built):
        log("  FAIL: no built bootloader at %s" % built)
        rows.append(("built bootloader present", False))
    elif not os.path.exists(out_bin):
        log("  FAIL: no flash dump at %s" % out_bin)
        rows.append(("flash dump present", False))
    else:
        b = open(built, "rb").read()
        d = open(out_bin, "rb").read()
        same = d[:len(b)] == b
        tail_erased = set(d[len(b):]) <= {0xFF}
        rec.update({
            "built_bytes": len(b),
            "built_md5": hashlib.md5(b).hexdigest(),
            "device_md5_over_built_length":
                hashlib.md5(d[:len(b)]).hexdigest(),
            "identical": same,
            "tail_erased": tail_erased,
        })
        log("  built  %6d bytes  md5 %s" % (len(b), rec["built_md5"]))
        log("  device %6d bytes  md5 %s"
            % (len(b), rec["device_md5_over_built_length"]))
        rows.append(("device bootloader == this project's build", same))
        rows.append(("remainder to 0x8000 is erased", tail_erased))
        if same:
            log("  -> the device runs THIS PROJECT'S bootloader, not MeatPi's; "
                "a full idf.py flash replaced it. This is the EXPECTED state "
                "for a serially flashed unit.")
        else:
            log("  -> the device bootloader DIFFERS from this build. It may be "
                "MeatPi's, or another build; compare before concluding which.")

    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec["rows"] = dict(rows)
    rec["fails"] = fails
    # NOT a pass/fail of the device: either answer is a legitimate state of a
    # WiCAN. The row says which, and the verdict records that it was measured.
    rec["verdict"] = "RECORDED"
    save("bootloader", rec)
    return 0


def rejudge(args, label):
    """Re-score a phase from its saved console log, without touching hardware.

    The console log is the evidence; the row list is an interpretation of it.
    When the interpretation turns out to be wrong -- as it did on the first
    control run, where four rows failed on a parser that was looking for
    "Loading app partition" while the bootloader prints "Loaded app from
    partition", and comparing slots against a computed ota_1 offset of
    0x1C3000 when the real one is 0x1D0000 -- the fix must not require
    re-flashing the device. Re-running hardware to correct a parser is how a
    record stops matching the run it describes.

    The existing record is kept: the re-scored one is written with the same
    phase name, so the live file is current and the frozen copies preserve
    every scoring the log has ever had.
    """
    path = os.path.join(RUNS, "rollback_%s.json" % label)
    console = os.path.join(RUNS, "rollback_%s_console.log" % label)
    if not os.path.exists(console):
        log("no console log for phase %s at %s" % (label, console))
        return 1
    rec = {}
    if os.path.exists(path):
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            rec = json.load(fh)
    lines = read_serial(console)
    ev = console_events(lines)
    rec["events"] = ev
    rec["boots"] = boots(ev)
    rec["_rejudged_from"] = os.path.relpath(console, ".")
    log("=== rejudge %s ===" % label)
    log("  %d console lines, %d events, boots %s"
        % (len(lines), len(ev), [b["slot"] for b in rec["boots"]]))
    extra = {}
    if label == "fault":
        rows, extra = judge_fault(rec, args.fault_mask)
    else:
        rows = judge_control(rec, require_change=label == "control")
    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec["rows"] = dict(rows)
    rec["fails"] = fails
    rec["measured"] = extra
    rec["verdict"] = "PASS" if not fails else "FAIL"
    log("  verdict: %s" % rec["verdict"])
    save(label, rec)
    return 0 if not fails else 1


def seconds_since_last_ota():
    """How long since an OTA phase last WROTE its record, or None.

    Taken from the phase records' own `_run` stamps rather than from a wall
    clock passed in, so the answer survives this process restarting.

    IT IS THE WRITE TIME, NOT THE OTA TIME, and the difference is real: a
    `--rejudge` rewrites the record with a fresh `_run` without any OTA having
    happened, so the stamp can be later than the flash it describes. That makes
    the reported elapsed time SMALLER than the truth, so the 60 s row gets
    harder to pass and no false pass is possible -- the error is in the safe
    direction. Named for what it measures all the same, because a label that
    says "since the last OTA" while meaning something else is how three
    separate parse errors in this file got believed.
    """
    newest = None
    for label in ("control", "fault", "restore"):
        p = os.path.join(RUNS, "rollback_%s.json" % label)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="ascii", errors="replace") as fh:
                stamp = json.load(fh).get("_run")
            when = datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except (ValueError, OSError, TypeError):
            continue
        if newest is None or when > newest:
            newest = when
    if newest is None:
        return None, None
    return (datetime.datetime.now() - newest).total_seconds(), newest


def phase_verify(args):
    """End state, with the one row the bring-up procedure turns on.

    THE 60 s WAIT IS THE CHECK, not a politeness. (The image is read from /gen_inhibit's
    elf_sha256 since 2026-10-25; this was git_version from /check_status.) A version read taken
    ten seconds after an OTA names an image that is about to be rolled back,
    and reads exactly like a successful update. That is the whole content of
    `gen-inhibit-truck-bringup.md` row 1.5b (added 2026-10-05 with the user's
    approval, after the mute-gate ruling): on the truck the HTTP version is the
    ONLY sign a silent rollback ever happened, so the reading has to be late
    enough for the gate to have resolved.

    This phase did not enforce it until that row existed. The OTA phases sit
    out their whole watch window, so every reading taken through them was late
    enough BY ACCIDENT; a `--phase verify` on its own had no protection at all.
    Unknown is a FAIL rather than a pass -- `--assume-settled` is there for an
    operator who knows the wait happened outside this tool, and it is recorded
    in the run file so the assertion is attributable.
    """
    log("=== verify ===")
    state = device_state(args.expect_device_id)
    for k in sorted(state):
        log("  %-14s %s" % (k, state[k]))
    good = image_facts(args.good)
    since, when = seconds_since_last_ota()
    if since is None:
        log("  no OTA phase record found, so the gate-settled wait is unknown")
    else:
        log("  %.0f s since an OTA phase record was last written (%s); "
            "a rejudge moves that stamp, which only understates the wait"
            % (since, when.isoformat(sep=" ")))
    settled = (since is not None and since >= GATE_TIMEOUT_MS / 1000.0)
    rows = [
        ("device reachable", True),
        ("the connected AP is the expected unit (BSSID, value-blind)",
         state.get("unit_matches") is True),
        ("reading is >= 60 s after the last OTA (bring-up row 1.5b)",
         settled or bool(args.assume_settled)),
        ("elf_sha256 matches the good image's",    # /gen_inhibit's against the image's descriptor (2026-10-25)
         bool(good.get("elf_sha256")) and state.get("elf_sha256") == good.get("elf_sha256")),
        ("mode is 0", state["mode"] == 0),
        ("not live", state["inhibit_live"] is False),
        ("no latched abort", state["abort_latched"] is False),
    ]
    fails = [n for n, ok in rows if not ok]
    for n, ok in rows:
        log("  %-4s %s" % ("ok" if ok else "FAIL", n))
    rec = {"state": state, "good_image": good, "rows": dict(rows),
           "fails": fails, "verdict": "PASS" if not fails else "FAIL",
           "seconds_since_last_ota": round(since, 1) if since else None,
           "last_ota_phase_run": when.isoformat(sep=" ") if when else None,
           "gate_settled": settled,
           "assume_settled": bool(args.assume_settled)}
    save("verify", rec)
    return 0 if not fails else 1


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", action="append", default=[],
                    choices=["images", "baseline", "otadata", "control",
                             "fault", "restore", "verify", "bootloader"])
    ap.add_argument("--fw", default=FW_DEFAULT)
    ap.add_argument("--good", default=None,
                    help="good image; default: newest build/*_man.bin")
    ap.add_argument("--faulty", default=None,
                    help="fault-injected image; default: "
                         "newest build-otafault/*_flt.bin")
    ap.add_argument("--fault-mask", type=int, default=1,
                    help="the bit the build suppresses (1 = WIFI_AP)")
    ap.add_argument("--watch", type=float, default=300.0,
                    help="serial capture window per OTA phase; must comfortably "
                         "exceed upload + 60 s gate + reboot")
    ap.add_argument("--otadata-file",
                    help="parse an existing esptool dump instead of reading")
    ap.add_argument("--expect-device-id", default=EXPECT_DEVICE_ID,
                    help="the unit this run is meant to be talking to; "
                         "compared, never printed, with the connected AP's BSSID (the SoftAP MAC)")
    ap.add_argument("--bootloader-file",
                    help="parse an existing 0x0..0x8000 dump instead of reading")
    ap.add_argument("--assume-settled", action="store_true",
                    help="assert that >= 60 s elapsed since the last OTA "
                         "outside this tool, for `verify` when no phase record "
                         "dates it. Recorded in the run file.")
    ap.add_argument("--rejudge", action="append", default=[],
                    choices=["control", "fault", "restore"],
                    help="re-score a phase from its saved console log; no "
                         "hardware, no OTA")
    args = ap.parse_args()

    if args.good is None:
        args.good = _newest(os.path.join(args.fw, "build"), "_man.bin")
    if args.faulty is None:
        args.faulty = _newest(os.path.join(args.fw, "build-otafault"),
                              "_flt.bin")

    if not args.phase and not args.rejudge:
        ap.error("give at least one --phase or --rejudge")

    rc = 0
    for label in args.rejudge:
        rc |= rejudge(args, label)
    if rc:
        log("stopping: a rejudge did not pass")
        return rc

    for ph in args.phase:
        if ph == "images":
            rc |= phase_images(args)
        elif ph == "baseline":
            rc |= phase_baseline(args)
        elif ph == "otadata":
            rc |= phase_otadata(args)
        elif ph == "control":
            rc |= run_ota_phase(args, "control", args.good, args.watch,
                                judge_control)
        elif ph == "fault":
            rc |= run_ota_phase(args, "fault", args.faulty, args.watch,
                                judge_fault, args.fault_mask)
        elif ph == "restore":
            rc |= run_ota_phase(args, "restore", args.good, args.watch,
                                judge_control)
        elif ph == "bootloader":
            rc |= phase_bootloader(args)
        elif ph == "verify":
            rc |= phase_verify(args)
        if rc:
            log("stopping: phase %s did not pass" % ph)
            break
    return rc


def _newest(d, suffix):
    """Newest matching image, and it must be unambiguous enough to name.

    The build directory keeps old images -- `build/` currently holds both
    f363674's and this commit's -- so "newest" is a convenience, and the phase
    records the md5 and project_name of whatever it picked so the choice is
    visible in the record rather than implied by a path.
    """
    if not os.path.isdir(d):
        return os.path.join(d, "MISSING" + suffix)
    cands = [os.path.join(d, f) for f in os.listdir(d)
             if f.endswith(suffix) and f.startswith("wican-fw_obd_")]
    if not cands:
        return os.path.join(d, "MISSING" + suffix)
    return max(cands, key=os.path.getmtime)


if __name__ == "__main__":
    sys.exit(main())
