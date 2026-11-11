"""The built-in "ota" flasher: one ESP-IDF app image into a WiCAN running
gen-inhibit's firmware, over the WiCAN's own hotspot (FLASH-SPEC, O1-O13).

THE REFERENCE GOVERNS. This is gen-inhibit's gen-inhibit/tools/bench_flash.py
and the upload() and ota() of its suite_gen_inhibit.py at gen-inhibit/main
40ecfa5e, moved. Where FLASH-SPEC does not mark a Change, their behaviour is
kept, and the verdict's code (classify, boots, watch, decided, describe_boots,
boot_bounds, the constants, VERDICT_NAMES, upload_and_judge, wait_for_device,
post_ota, CURL_WRITE_OUT, parse_curl, image_version, image_elf_sha256) is
their source text, copied unchanged, so gen-inhibit's mutant edits apply to
this file as they do to theirs. flash_once() is bench_flash's main() with
--flash; upload() and ota() inside make_flasher() are the suite's, with the
lines its mutants edit kept as they were.

The three Changes:
  O3   the image's app_elf_sha256 must equal Image.expect, byte 0 must be
       0xE9, and a pinned image's size, sha256 and md5 must equal its pin;
       otherwise it refuses and nothing is sent (check_image()).
  O4a  the Wi-Fi is wifi.py's: one module, netsh on Windows, nmcli on Linux.
  O7   an armed WiCAN is disarmed only under the caller's hold of the bench
       (disarm_if_held()); without one it is refused, as before.

How it is called. The chain runner calls a flasher as
flasher(image, params, settings, logdir); a flasher whose attribute
`takes_hold` is true is also given `hold=`, the runner's own lock check
(Engine.lock_ours: a callable returning (held, text)). It is asked at the
moment the flasher would disarm, so a hold lost since the case began is not
trusted. Called without one (a direct call, or any other caller), an armed
device is refused and nothing is disarmed. The by-hand command line below
builds its hold from the bench lock log (log_hold()).

Image.params:
    bin       the app image (.bin)
    retry     REQUIRED: "none" or "dual-slot" (O11), the suite's policy
    size, sha256, md5   a pinned image's pin (O3); each given one must match
Image.expect: the app ELF's sha256, 64 hex, as the page's elf_sha256 reads.
Settings, "ota" (machine-local, never committed):
    host          the device's address (default 192.168.80.1, a WiCAN
                  firmware default)
    iface         the Wi-Fi interface (netsh's interface name; nmcli's device)
    profile       the saved Wi-Fi profile for the hotspot (made by the user;
                  its passphrase stays with the system)
    ssid          the hotspot's SSID, or
    ssid_env      the name of an environment variable holding it. The SSID
                  embeds the WiCAN's MAC: it is replaced by <WICAN_SSID> in
                  every console line, record and result this writes. The
                  chain runner's record.json keeps the settings it ran with
                  (unless the run gives record_settings), so ssid_env keeps
                  it out of that record.
    capture       the optional Wi-Fi capture (ota_wire.py); default true
    capture_iface the capture interface's name in `dumpcap -D` (default iface)
    echo          print each upload's console as it goes (the command line's)

The policies (O11), named by params["retry"]:
    none       one upload; a failed load is the flasher's failure, recoverable
               when nothing was sent or the verdict left the device on its
               old image (a1, b, d, d', e; an a2+ verdict by its last part),
               not for other, undecided, a (c) whose strict read failed, or
               a2+c.
    dual-slot  the suite's ota(): attempt 1; else identify the running build,
               upload it over itself, try once more. It needs the suite's
               identify and its WiCAN images: the suite registers
               make_flasher(identify=<its zero-argument identify, returning
               the running elf_sha256 or raising>, images=<its {name: Image}>)
               in Suite.flashers under "ota" (the same code as the built-in,
               given those two). The built-in alone refuses "dual-slot",
               sending nothing.

By hand (a lock taken with bench_lock.py first; the log must show it):

    python -m vtrux_bench.run.ota --bin APP.bin --expect <elf sha256>
        [--size N --sha256 H --md5 H] --settings <machine settings json>
        --who "tester (charge-interposer)" --holder-pid <the take's pid>
        --logdir <folder outside any git worktree> [--retry none]

Exit 0 a load, 1 not a load, 2 refused before anything was sent.
"""

import argparse
import builtins
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import types

# bench_flash's name for the capture module, kept so upload_and_judge's text is
# bench_flash's
from . import ota_wire as bench_flash_wire
from . import wifi
from .wican_http import MODES, http, have_external_route, gi_status, set_mode

DEFAULT_HOST = "192.168.80.1"
SSID_MASK = "<WICAN_SSID>"
ESP_IMAGE_MAGIC = 0xE9

# esp_app_desc_t, at offset 0x20 of an ESP-IDF app image -- the same structure
# test/build_checks/build_output.py reads, and for the same reason: the filename
# is fixed at configure time and can name a different build entirely.
APP_DESC_OFF = 0x20
APP_DESC_MAGIC = 0xABCD5432


# ---- the console: every line an upload prints, the SSID masked ------------
#
# bench_flash printed to its own process's stdout, which the suite kept as the
# upload's .txt. Here the upload runs in the runner's process, so print() in
# this module writes to the calling thread's console when one is set (the
# chain runner's lanes are threads), and to stdout otherwise. The copied code
# keeps its print() and say() calls unchanged.

_sink = threading.local()


def mask(text, ssid):
    """text with the WiCAN's SSID replaced by <WICAN_SSID>."""
    if ssid and isinstance(text, str):
        return text.replace(ssid, SSID_MASK)
    return text


class Console:
    def __init__(self, ssid, echo=False):
        self.ssid, self.echo, self.buf = ssid, echo, io.StringIO()

    def write(self, s):
        s = mask(s, self.ssid)
        self.buf.write(s)
        if self.echo:
            sys.stdout.write(s)

    def flush(self):
        if self.echo:
            sys.stdout.flush()

    def text(self):
        return self.buf.getvalue()


def print(*args, **kw):     # noqa: A001 -- this module's print goes to the console
    w = getattr(_sink, "w", None)
    if w is not None and kw.get("file") is None:
        kw["file"] = w
    builtins.print(*args, **kw)


def say(ok, what, detail=""):
    mark = "  ok  " if ok else " FAIL "
    if ok is None:
        mark = " .... "
    print("[%s] %-38s %s" % (mark, what, detail))
    return bool(ok)


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


def check_image(path, expect, pin=None):
    """FLASH-SPEC O3: [(what, ok, detail)] for every check of the image file.
    Every one must hold, or nothing is sent."""
    out = []
    if not path or not os.path.isfile(path):
        return [("image exists", False, "no file %s" % path)]
    with open(path, "rb") as fh:
        data = fh.read()
    out.append(("image starts with 0xE9", data[:1] == bytes([ESP_IMAGE_MAGIC]),
                "byte 0 is %s" % ("0x%02X" % data[0] if data else "missing")))
    ver, err = image_version(path)
    out.append(("image carries a version", ver is not None, err or "embedded version %r" % ver))
    got = image_elf_sha256(path)
    want = (expect or "").lower()
    out.append(("image is the identity it names", bool(want) and got == want,
                "app_elf_sha256 %s, Image.expect %s" % (got, want or "(none)")))
    pin = pin or {}
    if pin.get("size") is not None:
        out.append(("image size is its pin", len(data) == int(pin["size"]),
                    "%d bytes, pinned %s" % (len(data), pin["size"])))
    for algo in ("sha256", "md5"):
        if pin.get(algo):
            h = hashlib.new(algo, data).hexdigest()
            out.append(("image %s is its pin" % algo, h == str(pin[algo]).lower(),
                        "%s, pinned %s" % (h, pin[algo])))
    return out


def pin_of(params):
    return {k: params[k] for k in ("size", "sha256", "md5") if params.get(k) is not None}


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


# -------------------------------------------------- the capture's one line --
#
# A Wi-Fi capture around each upload (bench_flash_wire.py; reviewer, 2026-10-17,
# for the open 128 KiB upload-reset observation). MESSAGE ONLY: the capture is
# started before the post and stopped after the watch, and nothing it returns
# reaches classify() or the pass/fail. Every call into it is guarded, so a host
# without Wireshark, or a capture that fails, flashes exactly as before and says
# the diagnostic was skipped.

def _guarded(fn, *a):
    """(ok, value-or-why) from fn(*a), which may also raise."""
    try:
        return fn(*a)
    except Exception as e:      # noqa: BLE001 -- the diagnostic never fails a flash
        return False, "%s: %s" % (type(e).__name__, e)


def upload_failed(up):
    """When the capture's line is printed: the upload did not end with the 303."""
    return up.get("http_code") != 303


def upload_and_judge(post, read, before, image_sha, image_bytes, now, sleep, capture=None):
    """Post the image, watch the device and judge it: the same steps, prints and
    verdict as before the capture was added. Returns a dict with up, t_start,
    t_end, readings, verdict, names, detail and wire (the capture's account)."""
    wire = {"capture": None}
    if capture is not None:
        ok, why = _guarded(capture.start)
        wire.update(capture=os.path.basename(getattr(capture, "path", "") or ""), started=bool(ok), start=why)
        if not ok:
            say(None, "wire capture", "skipped, the flash goes on without it: %s" % why)
            capture = None
    t_start = now()
    up = post()
    t_end = now()
    code = up.get("http_code")
    say(None, "upload ended", "HTTP %s; curl sent %s of %d bytes in %s s (wall %.2f s); %s"
        % (code, up.get("size_upload"), image_bytes, up.get("time_total"),
           t_end - t_start, up.get("curl_error") or "no curl error"))
    if up.get("body"):
        say(None, "reply body", up["body"][:200].replace("\n", " | "))

    readings = watch(read, code, t_end, before, image_sha, now, sleep)
    verdict, detail = classify(code, t_end, before, readings, image_sha)
    names = " + ".join(VERDICT_NAMES.get(v, v.upper()) for v in verdict.split("+"))
    print("\nVERDICT: (%s) %s -- %s" % (verdict, names, detail))
    if capture is not None:
        wire["stop"] = _guarded(capture.stop)[1]
        if upload_failed(up):
            res = _guarded(capture.summary, t_start, t_end)
            if res[0] and isinstance(res[0], dict):
                wire["summary"] = res[0]
                line = _guarded(lambda s: (True, bench_flash_wire.wire_line(s)), res[0])
                wire["line"] = line[1] if line[0] else None
                print(line[1] if line[0] else "wire: diagnostic skipped: %s" % line[1])
            else:
                print("wire: diagnostic skipped: %s" % res[1])
    return {"up": up, "t_start": t_start, "t_end": t_end, "readings": readings,
            "verdict": verdict, "names": names, "detail": detail, "wire": wire}


def wait_for_device(host, timeout=60.0, now=None, sleep=None):
    """(page, error): the first /gen_inhibit page, read leniently, that carries
    elf_sha256 and uptime_ms.

    NEVER /check_status (reviewer, 2026-10-20): it answers with the device's
    config, credentials included, and that answer reached Wi-Fi captures and
    console logs. /gen_inhibit names the image (elf_sha256) and the boot
    (uptime_ms), which is all the preflight needs.
    """
    now, sleep = now or time.time, sleep or time.sleep
    deadline = now() + timeout
    last = ""
    while now() < deadline:
        page, err = gi_status(host, strict=False)
        if page is not None:
            missing = [k for k in ("elf_sha256", "uptime_ms") if page.get(k) is None]
            if not missing:
                return page, None
            err = "/gen_inhibit has no %s" % " or ".join(missing)
        last = err or "no answer"
        sleep(2.0)
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


# ---- O7: disarming under the bench's hold --------------------------------
#
# Exactly as gen-inhibit's bench/wican_reboot.py does at 40ecfa5e (user,
# 2026-11-11): POST /gen_inhibit_set?mode=0 with an empty body, its reply
# recorded and trusted for nothing; then up to 4 reads of the page, 3 s apart,
# 8 s each, read leniently as page_state.read_page reads it; the first that
# parses must be clean as wican_reboot's clean() defines it.

DISARM_TRIES = 4
DISARM_GAP_S = 3.0
DISARM_TIMEOUT_S = 8.0
# page_state.py's fields: the only ones of the page the record keeps
STATE_FIELDS = ("mode", "uptime_ms", "reset_reason", "elf_sha256", "inhibit_live", "disabled", "self_off")
REASON_FIELDS = ("arm_block", "abort_reason", "abort_latched", "disable_reason", "err_n", "err_age_ms", "worker")


def read_page(host, timeout):
    """page_state.read_page: the page as a dict, the leading JSON document by
    raw_decode (trailing bytes tolerated); raises on no 200 or no object."""
    st, body = http(host, "/gen_inhibit", timeout=timeout)
    if st != 200:
        raise IOError("GET /gen_inhibit returned %s" % st)
    p, _ = json.JSONDecoder().raw_decode(body.lstrip())
    if not isinstance(p, dict):
        raise ValueError("the page is not an object")
    return p


def state(p):
    """page_state.state."""
    out = {f: p.get(f) for f in STATE_FIELDS}
    out["reasons"] = {f: p.get(f) for f in REASON_FIELDS}
    return out


def read_tries(host, tries, gap, timeout, errors):
    """wican_reboot.read_tries: the first page that parses, as state(), or None."""
    for i in range(tries):
        if i:
            time.sleep(gap)
        try:
            return state(read_page(host, timeout))
        except Exception as e:      # noqa: BLE001 -- any failed read is one try
            errors.append(repr(e)[:120])
    return None


def clean(p):
    """wican_reboot.clean: the latch and disable checks on a page: [(name, ok, detail)]."""
    r = p.get("reasons") or {}
    return [("mode 0", p.get("mode") == 0, "mode %r" % (p.get("mode"),)),
            ("abort not latched", r.get("abort_latched") is False,
             "abort_latched %r (%s)" % (r.get("abort_latched"), r.get("abort_reason"))),
            ("not disabled", p.get("disabled") is False and r.get("disable_reason") in ("", None),
             "disabled %r, disable_reason %r" % (p.get("disabled"), r.get("disable_reason"))),
            ("not self-off", p.get("self_off") in ("", None, False), "self_off %r" % (p.get("self_off"),))]


def is_held(hold):
    """(held, text) from the caller's hold: a callable returning (held, text),
    asked now. None, or one that raises, is not held."""
    if hold is None:
        return False, "no hold was given (a direct call)"
    try:
        got = hold()
    except Exception as e:      # noqa: BLE001 -- a hold that cannot be read is not held
        return False, "the hold could not be read: %s: %s" % (type(e).__name__, e)
    if isinstance(got, tuple) and len(got) == 2:
        return bool(got[0]), str(got[1])
    return False, "the hold answered %r, not (held, text)" % (got,)


def disarm_if_held(args, hold, res, mode):
    """The device read mode != 0. Under the caller's hold: disarm it as
    wican_reboot does and return 0 if the page then reads clean; else return
    `mode` (the caller's mode check then refuses). A disarm that does not read
    clean sets res["stop"]: the flasher stops, recoverable False (O7)."""
    held, why = is_held(hold)
    res["hold"] = why
    if not held:
        say(None, "not disarmed", "the bench is not held by this flasher's caller "
            "(%s); an armed device is refused" % why)
        return mode
    say(None, "bench held", why)
    st, _body = set_mode(args.host, 0, offset=None, timeout=DISARM_TIMEOUT_S)
    res["disarm_post"] = {"path": "/gen_inhibit_set?mode=0", "status": st}
    say(None, "disarm posted", "POST /gen_inhibit_set?mode=0 -> %s (trusted for nothing; "
        "the page decides)" % st)
    errors = []
    page = read_tries(args.host, DISARM_TRIES, DISARM_GAP_S, DISARM_TIMEOUT_S, errors)
    res["disarm_page"], res["disarm_errors"] = page, errors
    if page is None:
        res["stop"] = "no page in %d tries after the disarm: %s" % (DISARM_TRIES, "; ".join(errors)[:200])
        say(False, "disarmed under the bench's hold", res["stop"])
        return mode
    bad = [(n, d) for n, ok, d in clean(page) if not ok]
    if bad:
        res["stop"] = "the page after the disarm is not clean: %s" % "; ".join(
            "%s (%s)" % b for b in bad)
        say(False, "disarmed under the bench's hold", res["stop"])
        return mode
    res["disarmed"] = True
    say(True, "disarmed under the bench's hold", "came up in mode %s (%s); the page now reads "
        "mode 0, not latched, not disabled, not self-off" % (mode, MODES.get(mode, "?")))
    return 0


# ---- one upload: bench_flash's main() with --flash ------------------------

def ap_prefix(host):
    """The hotspot's /24, from the device's address: a "working internet"
    reading whose local address is in here is the WiCAN answering. bench_flash
    had it as AP_PREFIX = "192.168.80.", the default host's."""
    return host.rsplit(".", 1)[0] + "."


def write_masked(path, text, ssid):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(mask(text, ssid))


def flash_once(args, hold=None):
    """One preflight and upload, as bench_flash.py --flash --record did.
    Returns {rc, console, verdict, sent, stop, came_up_armed, disarmed, ...}:
    rc 0 only when every precondition held, the verdict ends in c and the
    strict read parsed (bench_flash's exit); console is everything it printed,
    the SSID masked; stop is set when a disarm under the hold failed."""
    con = Console(args.ssid, echo=getattr(args, "echo", False))
    prev = getattr(_sink, "w", None)
    _sink.w = con
    res = {"rc": 1, "verdict": None, "sent": False, "stop": None, "came_up_armed": None,
           "disarmed": None, "record": None}
    try:
        res["rc"] = _flash_once(args, hold, res)
    except Exception as e:      # noqa: BLE001 -- bench_flash crashing was a non-zero exit
        print("\nCRASHED: %s: %s" % (type(e).__name__, e))
        res["rc"] = 1
    finally:
        _sink.w = prev
    res["console"] = con.text()
    return res


def _flash_once(args, hold, res):
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
            # O3 (Change): its identity, its first byte, a pinned image's pin
            for what, ok, detail in check_image(args.bin, args.expect, args.pin):
                if what != "image carries a version":     # needed just above
                    need(ok, what, detail)
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
    ap = ap_prefix(args.host)
    ok, local_before = have_external_route()
    res["route_before"] = local_before
    need(ok, "internet before the join",
         "reachable from %s" % local_before if ok else
         "no route to 1.1.1.1 or 8.8.8.8 -- joining an AP with no internet "
         "would leave this machine with none")
    if ok:
        need(not str(local_before).startswith(ap),
             "that internet is not the device",
             "local address %s" % local_before)

    ssid_before, state_before = wifi.current(args.iface)
    res["wifi_before"] = {"ssid": ssid_before, "state": state_before}
    say(True, "WiFi before", "%s / %s" % (ssid_before or "(none)", state_before))

    if fails:
        print("\nPREFLIGHT FAILED before touching the network: %s"
              % ", ".join(fails))
        return 1

    # ------------------------------------------------------------- the join --
    ok, why = wifi.join(args.profile, args.iface, args.ssid)
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
    res["route_after"] = local_after
    need(ok, "internet after the join",
         "still reachable from %s" % local_after if ok else
         "internet was LOST by joining the device's AP -- the default route "
         "followed WiFi. Stop and give this machine a second path.")
    if ok:
        need(not str(local_after).startswith(ap),
             "internet is still not the device", "local address %s" % local_after)
        res["same_path"] = local_after == local_before
        say(local_after == local_before, "the same path as before",
            "%s -> %s" % (local_before, local_after))

    # ------------------------------------------------------------ the device --
    # LENIENT HERE, STRICT AFTER THE OTA. This page belongs to the image being
    # replaced; the mode is what preflight actually needs from it, and a device
    # whose status page is malformed is the very device that wants reflashing.
    # The device is reached through /gen_inhibit only (2026-10-20): no config
    # read, so no wifi_mode check and no git/fw/hw_version line.
    st, err = wait_for_device(args.host)
    need(st is not None, "device answers",
         err or "over its AP at %s%s" % (args.host, ("; " + st["_trailing"]) if st.get("_trailing") else ""))
    if st is None:
        print("\nPREFLIGHT FAILED: %s" % ", ".join(fails))
        return 1
    say(True, "device reports", "elf_sha256 %s... uptime %s ms"
        % (str(st.get("elf_sha256"))[:16], st.get("uptime_ms")))
    if st is not None:
        mode = st.get("mode")
        say(True, "gen_inhibit mode", "%s (%s)" % (mode, MODES.get(mode, "?")))
        # SPEC 9.3. O7 (Change): disarmed only under the caller's hold of the
        # bench, as wican_reboot does; without it, refused as before.
        res["came_up_armed"] = mode != 0
        if mode != 0:
            mode = disarm_if_held(args, hold, res, mode)
        need(mode == 0, "device is OFF, so OTA is permitted",
             "mode is %s (%s); spec 9.3 refuses OTA in any non-OFF mode. "
             "Disarmed only under the bench's hold, as wican_reboot does."
             % (mode, MODES.get(mode, "?")))

    if args.status:
        print("\n--status: reported only, nothing written.")
        return 1 if fails else 0

    if fails:
        print("\nPREFLIGHT FAILED, nothing written: %s" % ", ".join(fails))
        return 1

    print("\nevery precondition holds")
    if not args.flash:
        print("NO WRITE: this was a preflight.")
        print("Still joined to %s so the device tests can follow." % args.ssid)
        return 0

    # ----------------------------------------------------------------- flash --
    #
    # The verdict is classify()'s, from elf_sha256 and boot instants -- never
    # git_version, which builds of one commit share (2026-10-16).
    image_sha = image_elf_sha256(args.bin)
    need(image_sha is not None, "image carries an elf_sha256", image_sha or "")

    def read():
        # The association usually survives the reboot; if it does not, rejoin
        # (20 s), and only when the interface is then off the WiCAN's SSID. A
        # failed rejoin is a missed reading (O12).
        page, _ = gi_status(args.host, strict=False)
        if page is None:
            if not wifi.on_ssid(args.iface, args.ssid):
                wifi.rejoin(args.profile, args.iface, args.ssid, timeout=20.0)
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
    capture = None
    if not args.no_capture and args.record:
        # Beside the record: never in this repo, never the system temp folder.
        cap_path = os.path.splitext(args.record)[0] + ".pcapng"
        capture = bench_flash_wire.Capture(args.host, cap_path, args.capture_iface,
                                           ceiling_s=bench_flash_wire.CEILING_S)

    def post():
        res["sent"] = True
        return post_ota(args.host, args.bin)
    out = upload_and_judge(post, read, before, image_sha,
                           os.path.getsize(args.bin), time.time, time.sleep, capture)
    up, t_start, t_end, readings = out["up"], out["t_start"], out["t_end"], out["readings"]
    verdict, names, detail = out["verdict"], out["names"], out["detail"]
    res["verdict"] = verdict
    if args.record:
        rec = {"image": os.path.basename(args.bin), "image_elf_sha256": image_sha,
               "image_expect": args.expect, "image_version": img_ver,
               "image_bytes": os.path.getsize(args.bin), "host": args.host,
               "wifi_before": res.get("wifi_before"), "route_before": res.get("route_before"),
               "route_after": res.get("route_after"), "same_path": res.get("same_path"),
               "came_up_armed": res.get("came_up_armed"), "disarmed": res.get("disarmed"),
               "disarm_post": res.get("disarm_post"), "disarm_page": res.get("disarm_page"),
               "before": before, "t_start": t_start, "t_end": t_end,
               "wall_s": round(t_end - t_start, 3), "upload": up, "readings": readings,
               "boots": boots(before, readings), "settle_uptime_ms": SETTLE_UPTIME_MS,
               "verdict": verdict, "verdict_name": names, "detail": detail,
               "wire": out["wire"]}
        write_masked(args.record, json.dumps(rec, indent=1), args.ssid)
        res["record"] = args.record
        print("wrote %s" % os.path.basename(args.record))
    need(verdict.split("+")[-1] == "c", "the OTA took", "(%s) %s" % (verdict, names))
    if fails:
        print("\nFLASH NOT CONFIRMED: %s" % ", ".join(fails))
        return 1

    # THE STATUS PAGE MUST STILL PARSE. See bench_flash's history: an image can
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


# ---- the flasher and its policies (O1, O11) -------------------------------

POLICIES = ("none", "dual-slot")
# the verdicts that leave the device on its old image (O11, "none")
OLD_IMAGE = ("a1", "b", "d", "d'", "e")


class IdentifyError(Exception):
    """The suite's identify could not look; not the same as "on none of the images"."""


class Stopped(Exception):
    """A disarm under the hold that did not read clean: the flasher stops,
    recoverable False (O7), whatever the policy."""

    def __init__(self, phase, detail):
        Exception.__init__(self, detail)
        self.phase, self.detail = phase, detail


def ota_settings(settings):
    """(cfg, None) or (None, why) from settings["ota"]."""
    s = settings.get("ota") or {}
    ssid = s.get("ssid") or (os.environ.get(s["ssid_env"]) if s.get("ssid_env") else None)
    miss = [k for k, v in (("iface", s.get("iface")), ("profile", s.get("profile")),
                           ("ssid or ssid_env", ssid)) if not v]
    if miss:
        return None, "settings.ota has no %s" % ", ".join(miss)
    return {"host": s.get("host") or DEFAULT_HOST, "iface": s["iface"], "profile": s["profile"],
            "ssid": ssid, "capture": s.get("capture", True) is not False,
            "capture_iface": s.get("capture_iface") or s["iface"],
            "echo": bool(s.get("echo"))}, None


def none_recoverable(res):
    """O11 "none": may another flash be tried? Yes when nothing was sent, or
    the verdict left the device on its old image (by its last part)."""
    if not res.get("sent"):
        return True
    return (res.get("verdict") or "").split("+")[-1] in OLD_IMAGE


def make_flasher(identify=None, images=None):
    """The "ota" flasher. identify: the suite's zero-argument identify of the
    WiCAN (its running elf_sha256, or it raises: could not look); images: the
    suite's {name: Image}, which it may fill in after this call, where the
    dual-slot restore looks up the build the WiCAN runs. The built-in has
    neither, and so refuses "dual-slot"."""
    images = {} if images is None else images
    call = {}       # the call in progress: its settings, hold and images' identities

    def upload(label, name, bin_path, settings_, logdir):
        """One OTA through flash_once: (ok, its VERDICT line). ok only on (c) TOOK
        and a strict read that parsed (bench_flash's exit 0)."""
        cfg = call["cfg"]
        expect, pin = call["want"].get(name, ("", {}))
        stamp = time.strftime("%Y%m%dT%H%M%S")
        txt = os.path.join(logdir, "ota_%s_%s_%s.txt" % (label, name, stamp))
        rec = os.path.join(logdir, "ota_%s_%s_%s.json" % (label, name, stamp))
        args = types.SimpleNamespace(
            bin=bin_path, host=cfg["host"], ssid=cfg["ssid"], profile=cfg["profile"],
            iface=cfg["iface"], expect=expect, pin=pin, flash=True, status=False,
            record=rec, no_capture=not cfg["capture"], capture_iface=cfg["capture_iface"],
            echo=cfg["echo"])
        res = flash_once(args, call.get("hold"))
        call["last"] = res
        write_masked(txt, res["console"], cfg["ssid"])
        rc, t = res["rc"], res["console"]
        if res.get("stop"):
            raise Stopped("disarm", "%s %s: %s" % (label, name, res["stop"]))
        v = re.search(r"VERDICT: .*", t)
        ok = rc == 0 and "VERDICT: (c) TOOK" in t
        return ok, "%s %s: %s (rc %s)" % (label, name, v.group(0) if v else "no VERDICT line", rc)

    def wican_sha():
        """The WiCAN's elf_sha256, from the suite's identify, or IdentifyError."""
        try:
            return identify()
        except Exception as e:      # noqa: BLE001 -- could not look, whatever it raised
            raise IdentifyError(str(e) or repr(e)) from e

    def none(image, params, settings_, logdir):
        """O11 "none": one upload; a failed load is the flasher's failure."""
        ok, d1 = upload("attempt1", image.name, params["bin"], settings_, logdir)
        if ok:
            return dict(ok=True, phase="ota", detail=d1, recoverable=True)
        last = call.get("last") or {}
        return dict(ok=False, phase="ota" if last.get("sent") else "preflight", detail=d1,
                    recoverable=none_recoverable(last))

    def ota(image, params, settings_, logdir):
        """The WiCAN's OTA. A failed upload is never a reason to skip the image's checks
        (user, 2026-10-19): the dual-slot restore (the build the board runs now, OTA'd
        over itself so both slots hold it; the 2026-10-16 procedure), then one more
        attempt. A second failure in a row stops the run (reviewer's bound): two in a
        row points at the build, not the Wi-Fi. Every attempt's VERDICT line is kept."""
        ok, d1 = upload("attempt1", image.name, params["bin"], settings_, logdir)
        if ok:
            return dict(ok=True, phase="ota", detail=d1, recoverable=True)
        try:
            sha = wican_sha()
        except IdentifyError as e:
            return dict(ok=False, phase="restore", detail="%s; then %s: no restore on an unknown state" % (d1, e),
                        recoverable=False)
        cur = [im for im in images.values() if im.board == "wican" and im.expect == sha]
        if not cur:
            return dict(ok=False, phase="restore", recoverable=False,
                        detail="%s; the WiCAN reads %s, no image of this suite: no restore" % (d1, sha[:8]))
        ok, d2 = upload("restore", cur[0].name, cur[0].params["bin"], settings_, logdir)
        if not ok:
            return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=False)
        ok, d3 = upload("attempt2", image.name, params["bin"], settings_, logdir)
        return dict(ok=ok, phase="ota" if ok else "ota-twice", detail="%s; %s; %s" % (d1, d2, d3),
                    recoverable=True)

    def refuse(phase, detail):
        return dict(ok=False, phase=phase, detail=detail + ": nothing sent", recoverable=True)

    def flasher(image, params, settings, logdir, hold=None):
        policy = params.get("retry")
        if policy not in POLICIES:
            return refuse("policy", "image %s: params['retry'] is %r, not one of %s"
                          % (image.name, policy, ", ".join(POLICIES)))
        if policy == "dual-slot" and identify is None:
            return refuse("policy", "image %s: the dual-slot policy needs the suite's identify "
                          "and images; register make_flasher(identify=..., images=...) in "
                          "Suite.flashers" % image.name)
        cfg, why = ota_settings(settings)
        if cfg is None:
            return refuse("settings", why)
        bad = [(w, d) for w, ok, d in check_image(params.get("bin"), image.expect, pin_of(params))
               if not ok]
        if bad:
            return refuse("image", "image %s refused (O3): %s" % (
                image.name, "; ".join("%s (%s)" % b for b in bad)))
        want = {im.name: (im.expect, pin_of(im.params or {})) for im in images.values()}
        want[image.name] = (image.expect, pin_of(params))
        call.clear()
        call.update(cfg=cfg, hold=hold, want=want)
        try:
            out = (none if policy == "none" else ota)(image, params, settings, logdir)
        except Stopped as e:
            out = dict(ok=False, phase=e.phase, recoverable=False,
                       detail="%s: stopped, the device not shown disarmed (O7)" % e.detail)
        finally:
            call.clear()
        out["detail"] = mask(out.get("detail"), cfg["ssid"])
        return out

    flasher.takes_hold = True
    return flasher


ota_flash = make_flasher()


# ---- by hand ---------------------------------------------------------------

def log_hold(log, who, pid):
    """A hold read from the bench lock log (vtrux_bench.bench_lock): held when
    the log, read on its bench host and environment, shows the lock held by
    `who` under `pid`, and that pid is running."""
    from .. import bench_lock as BL

    def hold():
        st = BL.read_state(log)
        h = st["holder"]
        if h is None:
            return False, "the lock is free"
        if h["who"] != who or h["pid"] != pid:
            return False, "not this caller's: %s" % BL.holder_text(st)
        if not BL.bench_impl(st).pid_alive(pid):
            return False, "the holder's pid %d is not running" % pid
        return True, BL.holder_text(st)
    return hold


def in_worktree(path):
    d = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def main(argv=None):
    from .model import Image
    ap = argparse.ArgumentParser(prog="python -m vtrux_bench.run.ota",
                                 description="Load one app image into the WiCAN over its hotspot, "
                                             "under a hold of the bench taken with bench_lock.py.")
    ap.add_argument("--bin", required=True)
    ap.add_argument("--expect", required=True, help="the app ELF's sha256 (64 hex)")
    ap.add_argument("--size", type=int)
    ap.add_argument("--sha256")
    ap.add_argument("--md5")
    ap.add_argument("--settings", required=True, help="the machine's settings JSON (its \"ota\" block)")
    ap.add_argument("--retry", default="none", choices=["none"])
    ap.add_argument("--logdir", required=True, help="where the console and record go; outside any git worktree")
    ap.add_argument("--who", required=True, help="the role holding the bench lock")
    ap.add_argument("--holder-pid", type=int, required=True, help="the pid the lock was taken under")
    ap.add_argument("--lock-log", help="the bench lock log (default: the settings' bench_lock_log)")
    a = ap.parse_args(argv)
    with open(a.settings, encoding="utf-8") as fh:
        settings = json.load(fh)
    log = a.lock_log or settings.get("bench_lock_log")
    if not log:
        builtins.print("refused: no bench lock log (--lock-log or settings bench_lock_log)")
        return 2
    wt = in_worktree(a.logdir)
    if wt:
        builtins.print("refused: --logdir is inside the git worktree %s; run records are never "
                       "published" % wt)
        return 2
    hold = log_hold(log, a.who, a.holder_pid)
    held, why = is_held(hold)
    if not held:
        builtins.print("refused: the bench is not held by %s under pid %d: %s"
                       % (a.who, a.holder_pid, why))
        return 2
    _cfg, why = ota_settings(settings)
    if why:
        builtins.print("refused: %s: nothing sent" % why)
        return 2
    os.makedirs(a.logdir, exist_ok=True)
    settings = dict(settings, ota=dict(settings.get("ota") or {}, echo=True))
    params = {"bin": os.path.abspath(a.bin), "retry": a.retry}
    for k in ("size", "sha256", "md5"):
        if getattr(a, k) is not None:
            params[k] = getattr(a, k)
    image = Image(os.path.splitext(os.path.basename(a.bin))[0], "ota", params, a.expect.lower(),
                  board="wican")
    out = ota_flash(image, params, settings, a.logdir, hold=hold)
    builtins.print("\nRESULT: ok=%s phase=%s recoverable=%s\n  %s"
                   % (out["ok"], out["phase"], out["recoverable"], out["detail"]))
    if out["ok"]:
        return 0
    return 2 if out["detail"].endswith("nothing sent") else 1


if __name__ == "__main__":
    sys.exit(main())
