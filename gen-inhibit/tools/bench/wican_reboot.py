#!/usr/bin/env python3
"""Reboot the WiCAN and confirm it: the board step at every run's start and every release.

    py -3.14 -B gen-inhibit/tools/bench/wican_reboot.py --label L --out F.json
        --known NAME=SHA [--known NAME=SHA]...  (--first NAME | --want NAME | --want-unknown)

User 2026-10-20 (port plan, board images step 4): the WiCAN is rebooted at the start of
every run and at each release, so a run killed without cleanup cannot leave it armed. The
reviewer's conditions the same day: never trust the POST alone; read the page before and
after; disarm whatever comes up armed (an auto-arm image re-arms on every boot); pass only
when every check holds. Used by the bench suite's on_start / on_end hooks and by hand at
a manual release.

  1. Read GET /gen_inhibit (up to 4 tries): the state found, recorded as page_state.py
     records it (no other field of the page is kept). No page: FAIL, nothing is sent.
     If its mode is not 0: POST /gen_inhibit_set?mode=0 and read again, both recorded
     (reviewer, 2026-10-20: a reboot that never comes must not leave the device armed).
  2. POST /system_reboot. Its reply is recorded and counts for nothing.
  3. Read the page until it shows a new boot, for up to --wait s: uptime_ms lower than
     before, or the boot time (read time minus uptime) later than before by more than
     the reads' own uncertainty. None in time: FAIL. While the page cannot be read, the
     hotspot is selected again (below), at most once every SELECT_EVERY_S.
  4. If that page's mode is not 0: POST /gen_inhibit_set?mode=0 and read again.
  5. PASS only when all hold on the last page read:
       - a new boot was seen (3);
       - reset_reason is SW (a software restart, spec: "A reboot is confirmed by uptime");
       - mode 0, and nothing latched or disabled: abort_latched false, disabled false,
         self_off and disable_reason empty;
       - the image: with --first, any elf_sha256 the page names, one of --known or not
         (the start step loads the run's first image after this; the record says whether
         the board differs from it, and an image none of --known names is recorded as
         unknown, with its hash); a page that names no elf_sha256 FAILs; with --want,
         exactly that image; with --want-unknown (the run does not know what the board is
         on), never.
The record names the image found (or the unknown hash, with found_unknown true), whether
the board came up armed and was disarmed, and every check. Reads /gen_inhibit only, never /check_status or any
config. Exit 0 on PASS, 1 otherwise. GI_WICAN_HOST overrides the device's address (the
harness test's fake). Gen-inhibit tester, 2026-10-20.

An unknown image at the start (gen-inhibit reviewer, 2026-11-18). The start step passed
only on one of this suite's images until then, so a board left on another project's image
(the charge-interposer's pinned WiCAN image, run gen-inhibit-bench_20261118T140836_1fcd)
stopped the run although the core's flasher was about to load the run's first image over
it. With --first the image no longer counts toward the verdict when every other check
holds: found_unknown and differs_from_first are true, so the flasher loads the first
image, and its own identity check stands. --want stays exact.

The hotspot's rejoin (2026-11-18). A reboot takes the WiCAN's hotspot down, and the phone
that carries madhouse-debian to it (gi_wifi's adb backend) is never left to rejoin by
itself, so with GI_OTA_SETTINGS set (the suite's settings.ota block) step 3 calls
gi_wifi.select, the backend's connect by its saved network, while the page cannot be read,
as x2_jam_cases' reset_to_off does. The record's `rejoin` says how (None without the
variable: a host whose own Wi-Fi rejoins, NICKY-XPS before the move) and `rejoins` lists
each select (after_s from the reboot POST, ok, why, masked by gi_wifi). A set but unusable
block FAILs before anything is sent. Found on the first chain bench runs on madhouse
(gen-inhibit-bench_20261118T124727_377f: page before, "no page after the reboot").

Cross-project dependency (gen-inhibit reviewer, 2026-10-24): the charge-interposer's
runner loads this file by path, read-only, from a pinned gen-inhibit/main checkout, and
relies on its options --label, --out, --known NAME=SHA, --first, --want and
--want-unknown, and on its record's verdict, detail, found_image, found_sha,
came_up_armed and disarmed. A change to any of them goes to both reviewers first (the
full list is in gen-inhibit/tools/README.md).
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from page_state import HOST, read_page, state  # noqa: E402

BOOT_MARGIN_S = 0.5
SELECT_EVERY_S = 5.0


def hotspot_select(environ=None):
    """(select or None, how): the rejoin, gi_wifi.select on the settings.ota block from
    GI_OTA_SETTINGS, or None without the variable. Raises gi_wifi.Refused for a set but
    unusable block."""
    environ = os.environ if environ is None else environ
    if not environ.get("GI_OTA_SETTINGS"):
        return None, None
    import gi_wifi as GW
    cfg = GW.from_env(environ)
    be = GW.backend(cfg)
    return (lambda: GW.select(cfg, be)), (
        "gi_wifi.select (settings.ota's %s backend) while the page cannot be read, every %.0f s"
        % (cfg.get("wifi") or "system", SELECT_EVERY_S))


def timed_read(timeout):
    """(state, t_sent, t_answered) or raises."""
    t0 = time.time()
    p = read_page(timeout)
    return state(p), t0, time.time()


def read_tries(tries, gap, timeout, errors):
    for i in range(tries):
        if i:
            time.sleep(gap)
        try:
            return timed_read(timeout)
        except Exception as e:
            errors.append(repr(e)[:120])
    return None


def post(path, timeout):
    """The reply to a POST, recorded: its status, or the error."""
    try:
        req = urllib.request.Request("http://%s%s" % (HOST, path), data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"path": path, "status": r.status}
    except Exception as e:
        return {"path": path, "error": repr(e)[:120]}


def new_boot(before, after):
    """Whether `after` is a later boot than `before`: (yes, why). Each is (state, t0, t1)."""
    (b, b0, b1), (a, a0, a1) = before, after
    ub, ua = b.get("uptime_ms"), a.get("uptime_ms")
    if not isinstance(ub, (int, float)) or not isinstance(ua, (int, float)):
        return False, "uptime_ms not a number (%r, %r)" % (ub, ua)
    if ua < ub:
        return True, "uptime_ms %d < %d before" % (ua, ub)
    latest_before = b1 - ub / 1000.0      # the boot was no later than this
    earliest_after = a0 - ua / 1000.0     # and the new one no earlier than this
    if earliest_after > latest_before + BOOT_MARGIN_S:
        return True, "boot time moved by >= %.1f s" % (earliest_after - latest_before)
    return False, "uptime_ms %d, boot time unchanged (within %.1f s)" % (ua, BOOT_MARGIN_S)


class Rejoin(object):
    """The hotspot's rejoin while the page cannot be read after a restart: select (hotspot_select's, or
    None) called at most once every SELECT_EVERY_S, each call appended to `rejoins` (after_s from the
    restart, ok, why). Used by step 3 here and by the tools that read the page across a restart of their
    own (restart_error_frames, for B12 and B19present; gen-inhibit reviewer, 2026-11-19)."""

    def __init__(self, select, rejoins):
        self.select, self.rejoins, self.last_select = select, rejoins, None

    def unreadable(self, t_post):
        """A read failed: select again if the last select is SELECT_EVERY_S old (or there was none)."""
        select, last_select = self.select, self.last_select
        if select is not None and (last_select is None or time.time() - last_select >= SELECT_EVERY_S):
            self.last_select = last_select = time.time()
            try:
                ok, how = select()
            except Exception as e:      # a select that crashes is a select that failed
                ok, how = False, repr(e)[:160]
            self.rejoins.append({"after_s": round(last_select - t_post, 1), "ok": bool(ok), "why": how})


def await_new_boot(before, t_post, wait, poll, timeout, select, errors, rejoins):
    """Step 3: read the page until it shows a later boot than `before` ((state, t0, t1)), for up to
    `wait` s, rejoining the hotspot (Rejoin) while the page cannot be read. Returns (the later read
    or None, why)."""
    after, why = None, "no page after the reboot"
    t_end = time.time() + wait
    rejoin = Rejoin(select, rejoins)
    while True:
        got = read_tries(1, 0, timeout, errors)
        if got is not None:
            yes, why = new_boot(before, got)
            if yes:
                after = got
                break
        else:
            rejoin.unreadable(t_post)
        if time.time() >= t_end:
            break
        time.sleep(poll)
    return after, why


def ota_route(tries=3, gap=1.0, timeout=8.0, environ=None):
    """(True, how) once the run's settings.ota block (GI_OTA_SETTINGS) has joined the WiCAN's hotspot
    through its backend (the phone: the host's /32 route to the device through it) and the page then
    answers; (False, why) when the block is unset or refused, the join fails, or the page does not
    answer after it. B21's guard steps reach the device this way, always joining first, never by
    whatever route happens to answer (gen-inhibit reviewer, topic (E), 2026-11-19; it replaces
    on_hotspot, which took a page answering by any route). Nothing but the page is asked for."""
    environ = os.environ if environ is None else environ
    try:
        import gi_wifi as GW
        cfg = GW.from_env(environ)
        ok, why = GW.join(cfg)
    except Exception as e:      # gi_wifi.Refused, or a core that cannot be loaded
        return False, "GI_OTA_SETTINGS: %s" % e
    wifi = cfg.get("wifi") or "system"
    if not ok:
        return False, "the ota block's %s backend did not join the hotspot: %s" % (wifi, why)
    errors = []
    if read_tries(tries, gap, timeout, errors) is None:
        return False, "joined through the ota block's %s backend (%s), but the page does not answer (%s)" % (
            wifi, why, errors[-1] if errors else "no error")
    return True, "joined through the ota block's %s backend (%s); the page answers" % (wifi, why)


def reboot_in_check(wait=60.0, poll=1.0, settle=3.0, timeout=8.0, environ=None):
    """A reboot inside a check (interlock_bench's before each case, B22's before each arm), confirmed
    as the board step confirms one, with the same hotspot rejoin (gen-inhibit reviewer, 2026-11-19:
    run cb08's in-check reboots never asked the phone back, and B20 VOIDed 28 of 29 cases). The page
    is read first; without it nothing is sent and the reboot is refused, never assumed. Then POST
    /system_reboot and await_new_boot with gi_wifi.select from GI_OTA_SETTINGS (None without it).
    Returns {ok: a new boot seen, why, s, before, after (states), uptime_ms, reset_reason, mode,
    rejoin, rejoins, errors}; no disarm and no image check, which the caller judges."""
    t = time.time()
    out = {"ok": False, "why": None, "s": None, "before": None, "after": None, "uptime_ms": None,
           "reset_reason": None, "mode": None, "rejoin": None, "rejoins": [], "errors": []}
    try:
        select, out["rejoin"] = hotspot_select(environ)
    except Exception as e:      # gi_wifi.Refused, or a core that cannot be loaded
        out.update(why="GI_OTA_SETTINGS: %s: nothing sent" % e, s=round(time.time() - t, 1))
        return out
    before = read_tries(4, 1.0, timeout, out["errors"])
    if before is None:
        out.update(why="no page before the reboot in 4 tries: nothing sent, the reboot cannot be confirmed",
                   s=round(time.time() - t, 1))
        return out
    out["before"] = before[0]
    out["reboot_post"] = post("/system_reboot", timeout)
    t_post = time.time()
    time.sleep(settle)
    after, why = await_new_boot(before, t_post, wait, poll, timeout, select, out["errors"], out["rejoins"])
    out.update(ok=after is not None, why=why, s=round(time.time() - t, 1))
    if after is not None:
        a = after[0]
        out.update(after=a, uptime_ms=a.get("uptime_ms"), reset_reason=a.get("reset_reason"), mode=a.get("mode"))
    return out


def clean(p):
    """The latch and disable checks on a page: [(name, ok, detail)]."""
    r = p.get("reasons") or {}
    return [("mode 0", p.get("mode") == 0, "mode %r" % (p.get("mode"),)),
            ("abort not latched", r.get("abort_latched") is False,
             "abort_latched %r (%s)" % (r.get("abort_latched"), r.get("abort_reason"))),
            ("not disabled", p.get("disabled") is False and r.get("disable_reason") in ("", None),
             "disabled %r, disable_reason %r" % (p.get("disabled"), r.get("disable_reason"))),
            ("not self-off", p.get("self_off") in ("", None, False), "self_off %r" % (p.get("self_off"),))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--known", action="append", default=[], help="NAME=SHA, one per image")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--first", help="start: the first image the run needs on the WiCAN")
    g.add_argument("--want", help="release: the image the WiCAN must be on")
    g.add_argument("--want-unknown", action="store_true", help="release: the run does not know")
    ap.add_argument("--wait", type=float, default=60.0)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--gap", type=float, default=3.0)
    ap.add_argument("--timeout", type=float, default=8.0)
    a = ap.parse_args(argv)
    known = {}
    for kv in a.known:
        name, _, sha = kv.partition("=")
        if not name or len(sha) != 64:
            ap.error("--known %r: NAME=<64 hex>" % kv)
        known[name] = sha.lower()
    if a.first and a.first not in known:
        ap.error("--first %s is not one of --known" % a.first)
    if a.want and a.want not in known:
        ap.error("--want %s is not one of --known" % a.want)
    rec = {"label": a.label, "host": HOST, "step": "start" if a.first else "release",
           "known": known, "first": a.first, "want": a.want, "errors": [], "checks": [],
           "before": None, "pre_disarm_post": None, "pre_disarm_page": None,
           "reboot_post": None, "after": None, "disarm_post": None,
           "final": None, "found_image": None, "found_sha": None, "differs_from_first": None,
           "found_unknown": None, "came_up_armed": None, "disarmed": None, "rejoin": None, "rejoins": [], "verdict": "FAIL"}

    def chk(name, ok, detail):
        rec["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    def finish():
        bad = [c for c in rec["checks"] if not c["ok"]]
        rec["verdict"] = "PASS" if rec["checks"] and not bad else "FAIL"
        found = rec["found_image"] or ("unknown image %s" % rec["found_sha"] if rec["found_sha"] else "no image read")
        bits = ["%s %s" % (rec["verdict"], a.label), "found %s" % found]
        if rec["before"]:
            b = rec["before"]
            bits.append("before: mode %s, latched %s" % (b.get("mode"), (b.get("reasons") or {}).get("abort_latched")))
        if rec["pre_disarm_post"]:
            pd = rec["pre_disarm_page"]
            bits.append("disarmed before the reboot: %s" % ("mode %s" % pd.get("mode") if pd else "no page after it"))
        if rec["came_up_armed"]:
            bits.append("came up armed, %s" % ("disarmed" if rec["disarmed"] else "NOT disarmed"))
        if a.first and rec["differs_from_first"]:
            bits.append("not the run's first image %s" % a.first)
        if bad:
            bits.append("failed: " + "; ".join("%s (%s)" % (c["check"], c["detail"]) for c in bad))
        rec["detail"] = ", ".join(bits)
        with open(a.out, "w") as fh:
            json.dump(rec, fh, indent=1)
        print(rec["detail"])
        return 0 if rec["verdict"] == "PASS" else 1

    try:
        select, rec["rejoin"] = hotspot_select()
    except Exception as e:      # gi_wifi.Refused, or a core that cannot be loaded
        chk("hotspot rejoin settings", False, "GI_OTA_SETTINGS: %s: nothing sent" % e)
        return finish()
    before = read_tries(4, a.gap, a.timeout, rec["errors"])
    if before is None:
        chk("page before the reboot", False, "no page in 4 tries: nothing sent")
        return finish()
    rec["before"] = before[0]
    if before[0].get("mode") != 0:
        # disarm first, so a reboot that never comes does not leave it armed (reviewer, 2026-10-20)
        rec["pre_disarm_post"] = post("/gen_inhibit_set?mode=0", a.timeout)
        got = read_tries(4, a.gap, a.timeout, rec["errors"])
        rec["pre_disarm_page"] = got[0] if got else None
    rec["reboot_post"] = post("/system_reboot", a.timeout)
    t_post = time.time()
    time.sleep(a.settle)
    after, why = await_new_boot(before, t_post, a.wait, a.poll, a.timeout, select, rec["errors"], rec["rejoins"])
    chk("a new boot", after is not None, why)
    if after is None:
        return finish()
    rec["after"] = last = after[0]
    chk("reset_reason SW", last.get("reset_reason") == "SW", "reset_reason %r" % (last.get("reset_reason"),))
    rec["came_up_armed"] = last.get("mode") != 0
    if rec["came_up_armed"]:
        rec["disarm_post"] = post("/gen_inhibit_set?mode=0", a.timeout)
        got = read_tries(4, a.gap, a.timeout, rec["errors"])
        if got is None:
            chk("page after the disarm", False, "no page in 4 tries")
            return finish()
        last = got[0]
        rec["disarmed"] = last.get("mode") == 0
    rec["final"] = last
    for c in clean(last):
        chk(*c)
    sha = (last.get("elf_sha256") or "").lower() or None
    rec["found_sha"] = sha
    names = [n for n, s in sorted(known.items()) if s == sha]
    rec["found_image"] = names[0] if names else None
    rec["found_unknown"] = sha is not None and not names
    if a.first:
        # any image the page names (gen-inhibit reviewer, 2026-11-18): an unknown one is
        # recorded as such, and differs from the first, so the flasher loads over it
        chk("an image named", sha is not None, "elf_sha256 %s is %s" % (
            sha, names[0] if names else "none of this suite's images (unknown: the start loads %s over it)"
            % a.first if sha else "not on the page"))
        rec["differs_from_first"] = rec["found_image"] != a.first
    elif a.want:
        chk("the image the run expects", sha == known[a.want], "elf_sha256 %s, want %s %s" % (
            sha, a.want, known[a.want]))
    else:
        chk("the image the run expects", False, "the run does not know which image the board is on "
            "(read %s)" % (names[0] if names else sha))
    return finish()


if __name__ == "__main__":
    sys.exit(main())
