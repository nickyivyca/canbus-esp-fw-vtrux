#!/usr/bin/env python3
"""Gen-inhibit's two suites for the shared chain runner (shared/bench, vtrux_bench.run,
pinned at 8fdcec5 on charge-interposer/main: 5fa86c6, whose on_start / on_end board
steps reboot the WiCAN at each run's start and release, and which refuses an esptool write
unless settings.esptool.chip names the X2's type and MAC, the MAC living only in the local
settings, never in a published file; plus --resume's carry rules, f60ebf3 / 45a67ba,
cleared by both reviewers 2026-10-20).

Gen-inhibit tester, 2026-10-18. Two suites on the one core (user, 2026-10-17):
    bench     part B only. Holds the bench lock from start to finish. The core runs the
              groups in order, puts each board on the images a group names (flashing
              only when it is not already there), judges each check with
              suite_judges.py, and restores the WiCAN to _man and the X2 to its own
              image at the end.
    emulated  parts H and C: no hardware, no lock.
The core's rules are in its engine.py; nothing here repeats them.

    PYTHONPATH="<core>/shared/bench;<interposer clone>/charge-interposer" \
    py -3.14 -B -m vtrux_bench.run plan --suite tools/gen_inhibit/suite_gen_inhibit.py:bench \
        --settings <settings.json>
    ... run [--group G] [--check "man_app/B1"]
    ... --suite tools/gen_inhibit/suite_gen_inhibit.py:emulated

Checks run from the reverse-it root ({reverse_it}) through the recorded runners in
the harness folder (baseline_b_*.sh), so a check is the same command the e3d9437
baseline ran; a check's records go to its own folder (GI_OUT, GI_RUNS_DIR).

Settings this suite reads, besides the core's (shared/bench/README.md):
    reverse_it       the reverse-it root, forward slashes
    gi_tools         this folder (tools/gen_inhibit of the tester clone)
    bash             Git Bash's bash.exe, by full path: on Windows a bare "bash" goes
                     through CreateProcess, which looks in System32 before PATH and
                     finds WSL's bash.exe
    gi_commit        the short commit the images were built from
    gi_images        the worktree holding build-tester-<name>/ (part C's builds)
    gi_prod_dir      the prod build's build/ folder
    gi_fw            the gen-inhibit clone whose commit the emulated suite builds and tests
    gi_checks_clone  the clone whose test/build_checks/ c_checks_run.ps1 runs (the tester clone)
    gi_components    the -Components argument of c_checks_run.ps1: a managed_components
                     tree its prod build verified (12.3 item 3)
    gi_host_ssh      the ssh host part H and B15host run on (madhouse-debian, user, 2026-10-22)
    gi_host_root     the reverse-it root on that host (H's RROOT; a leading ~/ is that host's home)
    interposer_code  a charge-interposer/ clone (for x2_flash_custody and the X2's identify)
    x2_images        {"x2_app": {"files": [[off, path]...], "md5": {...}, "expect": ...},
                      "x2_slcan": {...}}: the X2 images the charge-interposer tester names
The WiCAN's SSID is read from firmware/bench-holder.md (its only home) when the suite
is loaded, passed to the checks in their environment, and redacted from every
declared output. It is never put in the settings, which the record keeps.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from vtrux_bench.run.model import Check, Figure, Group, Image, Result, Suite

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import suite_judges as SJ   # noqa: E402

ROLE = "tester (gen-inhibit)"
HOST = "192.168.80.1"
# The bench harness: bench/ of the gi_tools folder the settings name (moved from
# SeaDrive, goal 20, 2026-10-22). In a check's argv it is this placeholder, which the
# core fills from the settings, so no local path is recorded; the hooks join gi_tools
# themselves (harness_dir). Its data stays in SeaDrive, found through REVERSE_IT_DIR.
HARNESS = "{gi_tools}/bench"


def harness_dir(settings):
    return os.path.join(need(settings, "gi_tools"), "bench")
# What B15host copies to gi_host_ssh, from this clone's bench/, to run there: the check,
# the paths module it imports and the pins that module checks the DBCs against.
B15_FILES = ("diag_decode_check.py", "gi_paths.py", "pins.json")
JAMMER_MD5 = "a89ece8c467f90723a3ac26e72924aa8"            # baseline_b_jam.sh's build
SSID_RX = re.compile(r"SoftAP `(WiCAN_[0-9a-fA-F]*)`")
WICAN_BUILDS = {   # image name -> build-tester-<dir> in gi_images
    "wican_man": "man", "wican_man2": "man2", "wican_rt": "rt", "wican_txabort": "txabort",
    "wican_abortwin": "abortwin", "wican_rxq": "rxq",
}
SHA_RX = re.compile(r"^[0-9a-f]{64}$")
TEXT_EXT = (".txt", ".json", ".log", ".csv", ".md")
PORT_RX = re.compile(r"could not open port|PermissionError|FileNotFoundError", re.I)


class IdentifyError(Exception):
    """identify could not look; not the same as "on none of the images"."""


B27_SKIP ="needs the user at the bench, USB plugged in (deferred by the user 2026-10-17, paired with the WiCAN config reset)"


def need(settings, key):
    v = settings.get(key)
    if not v:
        raise ValueError("settings.%s is not set" % key)
    return v


def wican_ssid(bench_holder):
    with open(bench_holder, encoding="utf-8") as f:
        m = SSID_RX.search(f.read())
    if not m:
        raise ValueError("no WiCAN SoftAP SSID in %s" % bench_holder)
    return m.group(1)


def image_bin(folder):
    got = sorted(glob.glob(os.path.join(folder, "wican-fw_obd_*.bin")))
    if len(got) != 1:
        raise ValueError("%d images in %s, want 1" % (len(got), folder))
    return got[0]


def elf_sha256_of_bin(path):
    """The app descriptor's ELF SHA-256 (offset 0xB0 of an ESP32 app image)."""
    with open(path, "rb") as f:
        head = f.read(0xD0)
    if len(head) < 0xD0 or head[0] != 0xE9 or head[0x20:0x24] != b"\x32\x54\xcd\xab":
        raise ValueError("%s is not an ESP32 app image with a descriptor" % path)
    return head[0xB0:0xD0].hex()


# ---------------------------------------------------------------- the judge adapter

def judged(*ids):
    """A core judge running suite_judges' judges on the check's own folder. Each result
    is named by its judge (sub), which is the id in the baseline index."""
    def judge(run):
        out = []
        for cid in ids:
            for r in SJ.judge_one(cid, run.outdir):
                out.append(Result(r["verdict"], sub=cid, detail=r["detail"],
                                  figures={k: Figure(v["value"], v["unit"]) for k, v in r["figures"].items()}))
        if run.rc != 0:
            out.append(Result("ERROR", sub="rc", detail="the runner script exited %s (see check.log)" % run.rc))
        return out
    judge.ids = ids
    return judge


# ---------------------------------------------------------------- flashers and identify

def _py(settings):
    return list(((settings.get("hosts") or {}).get("local") or {}).get("python") or ["py", "-3.14", "-B"])


def _run(argv, logpath, env_extra, cwd, timeout):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_extra)
    with open(logpath, "w", encoding="utf-8") as f:
        try:
            p = subprocess.run(argv, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            return p.returncode
        except subprocess.TimeoutExpired:
            f.write("\nTIMED OUT after %s s\n" % timeout)
            return None


def wican_page():
    with urllib.request.urlopen("http://%s/gen_inhibit" % HOST, timeout=8) as r:
        return json.load(r)


_sleep = time.sleep


def new_records(folder, pattern, before):
    """The records a tool wrote in this call: an older one must not stand in for it."""
    return sorted(set(glob.glob(os.path.join(folder, pattern))) - before)


def _text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def make_hooks(settings, ssid, images=None):
    """images: the suite's {name: Image}, filled in after this is called; the OTA's
    dual-slot restore looks up the build the WiCAN is running in it."""
    images = {} if images is None else images
    root = need(settings, "reverse_it")
    tools = need(settings, "gi_tools")
    harness = harness_dir(settings)
    # GI_FW_TOOLS is retired (2026-10-22): the bench tools find tools/gen_inhibit/ as
    # their parent, and refuse it if set. REVERSE_IT_DIR is where their data is.
    base_env = {"GI_WICAN_SSID": ssid, "REVERSE_IT_DIR": root, "INTERPOSER_CODE": settings.get("interposer_code") or ""}

    def redact(paths):
        """The SSID out of every text file named; anything else (a flash dump) untouched."""
        for p in paths:
            if os.path.splitext(p)[1].lower() not in TEXT_EXT:
                continue
            try:
                s = _text(p)
            except OSError:
                continue
            if ssid in s:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(s.replace(ssid, "<GI_WICAN_SSID>"))

    def run(argv, logpath, env_extra, timeout):
        """A hook's tool, from the reverse-it root; then the SSID out of its log and of
        every text record it left in the log folder."""
        before = set(os.listdir(os.path.dirname(logpath)))
        rc = _run(argv, logpath, env_extra, root, timeout)
        d = os.path.dirname(logpath)
        redact([logpath] + [os.path.join(d, n) for n in sorted(set(os.listdir(d)) - before)])
        return rc

    def upload(label, name, bin_path, settings_, logdir):
        """One OTA through bench_flash: (ok, its VERDICT line). ok only on (c) TOOK."""
        stamp = time.strftime("%Y%m%dT%H%M%S")
        txt = os.path.join(logdir, "ota_%s_%s_%s.txt" % (label, name, stamp))
        rec = os.path.join(logdir, "ota_%s_%s_%s.json" % (label, name, stamp))
        rc = run(_py(settings_) + [os.path.join(tools, "bench_flash.py"), "--bin", bin_path, "--flash",
                                   "--record", rec], txt, base_env, 600)
        t = _text(txt)
        v = re.search(r"VERDICT: .*", t)
        ok = rc == 0 and "VERDICT: (c) TOOK" in t
        return ok, "%s %s: %s (rc %s)" % (label, name, v.group(0) if v else "no VERDICT line", rc)

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

    def x2_hold(image, params, settings_, logdir):
        txt = os.path.join(logdir, "x2_hold_hold_%s.txt" % time.strftime("%Y%m%dT%H%M%S"))
        rc = run(_py(settings_) + [os.path.join(harness, "x2_hold.py"), "hold"], txt,
                 dict(base_env, GI_RUNS_DIR=logdir), 180)
        t = _text(txt)
        ok = rc == 0 and "TX (GPIO7) not driven" in t
        line = [ln for ln in t.splitlines() if "TX (GPIO7)" in ln]
        return dict(ok=ok, phase="hold", detail="%s (rc %s)" % (line[-1] if line else "no pin line", rc),
                    recoverable=True)

    def x2_jammer(image, params, settings_, logdir):
        """Custody first (x2_flash_custody: the dump, twice, must carry the X2's own image),
        then the jammer from its manifest row. The X2's own image comes back through
        x2_app's flasher at the restore."""
        env = dict(base_env, GI_RUNS_DIR=logdir)
        py = _py(settings_)
        dump = os.path.join(logdir, "custody_dump.txt")
        before = set(glob.glob(os.path.join(logdir, "x2_flash_custody_dump_*.json")))
        rc = run(py + [os.path.join(harness, "x2_flash_custody.py"), "dump", "--expect-elf",
                       params.get("expect_elf", "756d6714")], dump, env, 900)
        recs = new_records(logdir, "x2_flash_custody_dump_*.json", before)
        verdict = None
        if len(recs) == 1:
            with open(recs[0], encoding="utf-8") as f:
                verdict = json.load(f).get("verdict")
        if rc != 0 or verdict != "PASS":
            return dict(ok=False, phase="custody-dump", detail="dump rc %s, %d records, verdict %s"
                        % (rc, len(recs), verdict), recoverable=True)
        fl = os.path.join(logdir, "custody_flash_jammer.txt")
        rc = run(py + [os.path.join(harness, "x2_flash_custody.py"), "flash-jammer", "--dump-record", recs[-1],
                       "--build", params.get("build", JAMMER_MD5)], fl, env, 900)
        if rc != 0:
            return dict(ok=False, phase="flash-jammer", detail="flash-jammer rc %s" % rc, recoverable=False)
        return dict(ok=True, phase="done", detail="custody dump %s; jammer %s" % (os.path.basename(recs[-1]),
                                                                                   params.get("build", JAMMER_MD5)),
                    recoverable=True)

    def wican_sha():
        """The WiCAN's elf_sha256 off its page, or IdentifyError (could not look)."""
        for attempt in range(3):
            try:
                page = wican_page()
                break
            except Exception as e:
                last = e
                if attempt == 0:
                    if tools not in sys.path:
                        sys.path.insert(0, tools)
                    import bench_flash as BF
                    BF.join_ap(ssid)
                _sleep(3)
        else:
            raise IdentifyError("the WiCAN's /gen_inhibit could not be read: %r" % (last,))
        sha = page.get("elf_sha256") if isinstance(page, dict) else None
        if not (isinstance(sha, str) and SHA_RX.match(sha)):
            raise IdentifyError("the WiCAN's page has no elf_sha256 (%r)" % (sha,))
        return sha

    def identify(image, ctx, logdir):
        """What the board is on, whatever image is wanted: the engine compares it with
        image.expect. None means "on none of these"; a tool that could not look RAISES,
        and the engine then stops the bench without flashing (core df5d2ec on)."""
        stamp = time.strftime("%Y%m%dT%H%M%S")
        if image.board == "wican":
            return wican_sha()
        # The X2, in an order that cannot disturb what it finds. 1: the pins, read
        # without a reset, find a hold (on a running image esptool cannot sync).
        txt = os.path.join(logdir, "x2_hold_pins_%s.txt" % stamp)
        before = set(glob.glob(os.path.join(logdir, "x2_hold_pins_*.json")))
        rc = run(_py(settings) + [os.path.join(harness, "x2_hold.py"), "pins"], txt,
                 dict(base_env, GI_RUNS_DIR=logdir), 120)
        t = _text(txt)
        recs = new_records(logdir, "x2_hold_pins_*.json", before)
        if len(recs) != 1:
            raise IdentifyError("x2_hold pins wrote %d records, want 1 (rc %s)" % (len(recs), rc))
        if PORT_RX.search(t + _text(recs[-1])):     # esptool's errors are in the record
            raise IdentifyError("x2_hold pins could not open the X2's port (rc %s)" % rc)
        if rc == 0 and "TX (GPIO7) not driven" in t:
            return "held:not driven"
        # 2: the charge-interposer tester's identify (resets the board): its own image, slcan.
        import bench_identify as BI
        got = BI.identify(image, ctx, logdir, settings)
        if got:
            return got
        # 3: the jammer answers ID with its banner. Sent only to a board 1 and 2 do not know.
        txt = os.path.join(logdir, "x2_jammer_id_%s.txt" % stamp)
        before = set(glob.glob(os.path.join(logdir, "x2_jammer_id_*.json")))
        rc = run(_py(settings) + [os.path.join(harness, "x2_jammer.py"), "id"], txt,
                 dict(base_env, GI_RUNS_DIR=logdir), 120)
        recs = new_records(logdir, "x2_jammer_id_*.json", before)
        if len(recs) != 1:
            raise IdentifyError("x2_jammer id wrote %d records, want 1 (rc %s)" % (len(recs), rc))
        with open(recs[-1], encoding="utf-8") as f:
            return "jammer:banner" if json.load(f).get("banner_ok") else None

    def board_step(step):
        """The core's on_start / on_end for the WiCAN (port plan, board images step 4; user
        2026-10-20): wican_reboot.py reboots it, reads the page until a new boot shows,
        disarms whatever comes up armed, and passes only on a SW reset, mode 0, nothing
        latched or disabled, and a known image (start: any of this suite's WiCAN images;
        end: the one the core believes the board is on). The X2 has no such step."""
        def hook(board, image, ctx, logdir):
            if board != "wican":
                return dict(ok=True, detail="no %s step for the %s" % (step, board))
            stamp = time.strftime("%Y%m%dT%H%M%S")
            rec_path = os.path.join(logdir, "wican_reboot_%s_%s.json" % (step, stamp))
            known = []
            for n, im in sorted(images.items()):
                if im.board == "wican":
                    known += ["--known", "%s=%s" % (n, im.expect)]
            if step == "start":
                want = ["--first", image.name]
            else:
                want = ["--want", image.name] if image is not None else ["--want-unknown"]
            try:
                wican_sha()          # joins the WiCAN's AP when its page cannot be read
            except IdentifyError:
                pass                 # wican_reboot.py finds no page and fails, saying so
            rc = run(_py(settings) + [os.path.join(harness, "wican_reboot.py"), "--label", "%s %s" % (step, board),
                                      "--out", rec_path] + known + want,
                     rec_path[:-len(".json")] + ".txt", base_env, 300)
            try:
                with open(rec_path, encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                return dict(ok=False, detail="wican_reboot.py left no record (rc %s)" % rc)
            before = rec.get("before") or {}
            found = {k: rec.get(k) for k in ("found_image", "found_sha", "differs_from_first",
                                             "came_up_armed", "disarmed")}
            found.update(before_mode=before.get("mode"),
                         before_latched=(before.get("reasons") or {}).get("abort_latched"),
                         record=os.path.basename(rec_path))
            return dict(ok=rc == 0 and rec.get("verdict") == "PASS",
                        detail=rec.get("detail") or "wican_reboot.py rc %s" % rc, found=found)
        return hook

    return dict(redact=redact, ota=ota, x2_hold=x2_hold, x2_jammer=x2_jammer, identify=identify,
                on_start=board_step("start"), on_end=board_step("release"))


# ---------------------------------------------------------------- the suite

def suite(settings):
    root = need(settings, "reverse_it")
    commit = need(settings, "gi_commit")
    images_dir = need(settings, "gi_images")
    prod_dir = need(settings, "gi_prod_dir")
    ssid = wican_ssid(need(settings, "bench_holder"))
    need(settings, "gi_can1_termination")      # NONODE's --termination: read on the bench by a person
    images = {}
    hooks = make_hooks(settings, ssid, images)
    H = HARNESS

    desc = {}
    for name, d in WICAN_BUILDS.items():
        b = image_bin(os.path.join(images_dir, "build-tester-" + d))
        images[name] = Image(name, "ota", {"bin": b}, elf_sha256_of_bin(b), board="wican")
        desc[name] = "image %s %s (elf %s)" % (commit, d, images[name].expect[:8])
    b = image_bin(prod_dir)
    images["wican_prod"] = Image("wican_prod", "ota", {"bin": b}, elf_sha256_of_bin(b), board="wican")
    desc["wican_prod"] = "image %s prod (elf %s)" % (commit, images["wican_prod"].expect[:8])
    x2 = settings.get("x2_images") or {}
    for name in ("x2_app", "x2_slcan"):
        if name not in x2:
            raise ValueError("settings.x2_images.%s is not set" % name)
        images[name] = Image(name, "esptool", {"files": x2[name]["files"], "md5": x2[name].get("md5") or {},
                                               "port": x2[name].get("port", "COM3")},
                             x2[name]["expect"], board="x2")
    images["x2_held"] = Image("x2_held", "x2-hold", {}, "held:not driven", board="x2",
                              note="in ROM download mode, CAN1 TX not driven (x2_hold.py)")
    images["x2_jammer"] = Image("x2_jammer", "x2-jammer", {"build": JAMMER_MD5, "expect_elf": x2["x2_app"]["expect"]},
                                "jammer:banner", board="x2", note="x2_flash_custody: dump, then the jammer")

    env0 = {"GI_OUT": "{outdir}", "GI_RUNS_DIR": "{outdir}", "GI_COMMIT": commit, "REVERSE_IT_DIR": "{reverse_it}",
            "GI_IMAGES": "{gi_images}", "GI_WICAN_SSID": ssid, "INTERPOSER_CODE": "{interposer_code}"}

    def chk(cid, script, args, judge=None, outputs=(), timeout=3600, pw="wican_man", env=None, cls="wallclock"):
        e = dict(env0, GI_PW_IMAGE=desc[pw])
        e.update(env or {})
        kw = dict(id=cid, argv=["{bash}", "%s/%s" % (H, script)] + list(args), cwd="{reverse_it}", env=e,
                  timeout_s=timeout, cls=cls, outputs=list(outputs) + ["check.log"])
        if judge:
            kw["judge"] = judge
        return Check(**kw)

    def desk(cid, step, inputs, judge, outputs):
        return chk(cid, "baseline_b_desk.sh", [step] + ["{run_dir}/%s" % i for i in inputs], judge, outputs,
                   timeout=1800, cls="light")

    C = commit
    # argv names the images through the settings, so a plan line carries no local path
    man_bin = "{gi_images}/build-tester-man/" + os.path.basename(images["wican_man"].params["bin"])
    groups = [
        Group("man_app", needs_lock=True, image=["wican_man", "x2_app"], checks=[
            chk("B1", "baseline_b_hand.sh", ["B1"], judged("B1"), ["B1_slcan_refusal_stdout.txt"], 1200),
            chk("B2app", "baseline_b_hand.sh", ["B2app"], None, timeout=1200),
            chk("B3arms", "baseline_b3_golive.sh", [], None, timeout=3600),
            desk("B3", "B3", ["man_app/B3arms"], judged("B3"), ["B3_golive_stale_check.txt", "B3_mode_entry_analysis.txt"]),
            desk("B24", "B24", ["man_app/B2app", "man_app/B3arms"], judged("B24"), ["B24_frame_content_%s.txt" % C]),
            chk("B4", "baseline_b_noX2.sh", ["B4"], judged("B4"), ["B4_skip_method_stdout.txt"], 1800),
            chk("B11arms", "baseline_b_noX2.sh", ["B11"], None, timeout=1800),
            desk("B11", "B11", ["man_app/B11arms"], judged("B11"), ["B11_quiesce_analysis.txt"]),
            chk("B11rawarms", "baseline_b_noX2.sh", ["B11raw"], None, timeout=1800),
            desk("B11raw", "B11raw", ["man_app/B11rawarms"], judged("B11raw"),
                 ["B11raw_rawpost_judge.txt", "B11raw_quiesce_analysis.txt"]),
            chk("B13arms", "baseline_b_noX2.sh", ["B13"], None, timeout=1800),
            chk("B15", "baseline_b_hand.sh", ["B15"], judged("B15"), ["B15_wican_segment_probe.txt"], 900),
            chk("B16", "baseline_b_hand.sh", ["B16"], judged("B16"), ["B16_page_only_baseline.txt"], 900),
            chk("B17a", "baseline_b_hand.sh", ["B17a"], judged("B17a"), ["B17_spec11_page_%s_man.txt" % C], 600),
            chk("B18", "baseline_b_hand.sh", ["B18"], judged("B18"), ["B18_diag_build_page_check.txt"], 600),
            chk("B20", "baseline_b_hand.sh", ["B20"], judged("B20"), ["B20_interlock_bench_run.txt"], 5400),
            desk("B25", "B25", ["man_app/B20"], judged("B25"), ["B25_keystate_rekey-long_%s.txt" % C]),
            chk("B21", "baseline_b21.sh", [man_bin], judged("B21"),
                ["B21_bench_flash_guard.txt", "B21_bench_ota_guard.txt",
                 "B21_wire_multipart_%s.txt" % C, "B21_wire_image_%s.txt" % C], 3600),
        ]),
        Group("man_held", needs_lock=True, image=["wican_man", "x2_held"], checks=[
            chk("B2held", "baseline_b_hand.sh", ["B2held"], None, timeout=1200),
            desk("B2", "B2", ["man_app/B2app", "man_held/B2held"], judged("B2"), ["B2_x2_presence_count.txt"]),
            chk("B9", "baseline_b_hand.sh", ["B9"], judged("B9"), ["B9_wrong_bitrate_calibrate.txt"], 900),
            chk("B10", "baseline_b_hand.sh", ["B10"], judged("B10"), ["B10_trip6_rearm_check.txt"], 1200),
            chk("B14", "baseline_b_hand.sh", ["B14"], judged("B14"), ["B14_observe_ack_probe.txt"], 900),
            chk("NONODE", "baseline_b_hand.sh", ["NONODE"], judged("NONODE"), ["NONODE_nonode_bench.txt"], 1200,
                env={"GI_TERMINATION": "{gi_can1_termination}", "GI_MAN_SHA": images["wican_man"].expect}),
            desk("B13", "B13", ["man_app/B13arms", "man_app/B2app", "man_held/B2held"], judged("B13"),
                 ["B13_passive_analysis.txt", "B13_respond_booking_check.txt"]),
        ]),
    ]
    ota_env = {"GI_NO_OTA": "1"}
    otag = lambda cid, step, judge, outputs, pw, t=3600: chk(
        cid, "baseline_b_otagroup.sh", ["{gi_images}", "{gi_prod_dir}"] + step.split(), judge, outputs, t, pw, ota_env)
    groups += [
        Group("rt", needs_lock=True, image=["wican_rt", "x2_app"], checks=[
            otag("B12", "rt", judged("B12"), ["B12_%d_%s.txt" % (k, n) for k, n in (
                (1, "live_reboot"), (2, "live_crash"), (3, "live_crash"), (4, "live_reboot"), (5, "live_reboot"),
                (6, "live_crash"), (7, "off_reboot"))] + ["B19_present_rt.txt"], "wican_rt")]),
        Group("txabort", needs_lock=True, image=["wican_txabort", "x2_app"], checks=[
            otag("B23", "txabort", judged("B23"), ["B23_txabort_bench.txt", "B19_present_txabort.txt"], "wican_txabort")]),
        Group("abortwin", needs_lock=True, image=["wican_abortwin", "x2_app"], checks=[
            otag("abortwin", "abortwin", None, ["B19_present_abortwin.txt"], "wican_abortwin")]),
        Group("rxq", needs_lock=True, image=["wican_rxq", "x2_app"], checks=[
            otag("B26", "rxq", judged("B26"), ["B26_rxq_idle_a.txt", "B26_rxq_idle_b.txt", "B26_rxq_loaded.txt",
                                               "B19_present_rxq.txt"], "wican_rxq"),
            desk("B19present", "B19present", ["rt/B12", "txabort/B23", "abortwin/abortwin", "rxq/B26"],
                 judged("B19present"), [])]),
        Group("prod", needs_lock=True, image=["wican_prod", "x2_app"], checks=[
            otag("B19absent", "prod prod_rxq30", judged("B19absent", "B19page"),
                 ["B19_absent_prod.txt", "B19_absent_prod_txabort.txt", "B19_absent_prod_rxq_gap30.txt",
                  "B19_spec11_page_prod.txt"], "wican_prod")]),
        Group("man2", needs_lock=True, image=["wican_man2", "x2_app"], checks=[
            otag("B17b", "man2", judged("B17b"), ["B17_spec11_page_%s_man2.txt" % C], "wican_man2")]),
        Group("man_jammer", needs_lock=True, image=["wican_man", "x2_jammer"], checks=[
            chk("B5_B8", "baseline_b_jam.sh", ["jam"], judged("B5", "B6", "B7", "B8"),
                ["B5_livetrain_p5000.txt"] + ["B5_livetrain_p2000_run%d.txt" % r for r in (1, 2, 3)]
                + ["B6_threshold_n10_lead%s.txt" % l for l in ("1.5", "5", "8.5")]
                + ["B6_threshold_n9_lead%s.txt" % l for l in ("1.5", "5")] + ["B7_busoff_rearm.txt", "B8_busoff.txt"],
                5400)]),
        Group("man_slcan", needs_lock=True, image=["wican_man", "x2_slcan"], checks=[
            chk("B22", "baseline_b22.sh", [], judged("B22"),
                ["B22_case3_%s_man_a.txt" % C, "B22_case3_%s_man_b.txt" % C], 1800),
            Check(id="B27", argv=["true"], skip=B27_SKIP)]),
    ]

    return Suite(
        name="gen-inhibit-bench", role=ROLE, groups=groups, images=images, code_info=code_info_for(settings, images),
        requires="gen-inhibit-emulated",   # user, 2026-10-17: the emulated run on this code first
        standing_images={"wican": "wican_man", "x2": "x2_app"},
        flashers={"ota": hooks["ota"], "x2-hold": hooks["x2_hold"], "x2-jammer": hooks["x2_jammer"]},
        identify=hooks["identify"], redact=hooks["redact"],
        on_start=hooks["on_start"], on_end=hooks["on_end"])


bench = suite


def reads(s):
    """{"group/check": ["group/check", ...]}: the checks whose folders a check's argv
    names ({run_dir}/<group>/<check>, the desk steps). A re-run of a check takes these
    with it, since a new run's folder holds only what that run ran."""
    out = {}
    for g in s.groups:
        for c in g.checks:
            out["%s/%s" % (g.name, c.id)] = [a[len("{run_dir}/"):] for a in c.argv if a.startswith("{run_dir}/")]
    return out


def app_version(path):
    """The app descriptor's version field (the commit the image says it was built from)."""
    with open(path, "rb") as f:
        head = f.read(0x50)
    return head[0x30:0x50].split(b"\0", 1)[0].decode("ascii", "replace")


def git_state(path):
    def g(*a):
        try:
            return subprocess.run(["git", "-C", path] + list(a), capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except Exception:
            return None
    head = g("rev-parse", "HEAD")
    st = g("status", "--porcelain")
    return {"head": head or None, "clean": None if st is None else st == ""}


def tree_at(path, commit):
    """{"head", "clean", "at"}: the tree's HEAD, whether it is clean, and whether HEAD
    is the commit (a short commit is a prefix of HEAD)."""
    st = git_state(path)
    st["at"] = bool(st["head"] and commit and st["head"].startswith(commit))
    return st


def code_info_for(settings, images):
    """What the record keeps about the code (user, 2026-10-17), and what the core's
    Suite.requires compares: commit and dirty. Until the code-hash tool exists the
    code is the commit, and only from a clean tree; so dirty is False only when the
    image worktree and the prod tree are both clean at gi_commit and every WiCAN
    image's descriptor names that commit. Read off the trees, never from settings."""
    commit = settings.get("gi_commit")

    def code_info(ctx):
        trees = {"gi_images": tree_at(need(settings, "gi_images"), commit),
                 "gi_prod_dir": tree_at(need(settings, "gi_prod_dir"), commit)}
        per = {}
        for n, im in images.items():
            b = im.params.get("bin")
            if not b:
                per[n] = {"board": im.board, "expect": im.expect, "code_hash": None,
                          "note": "an X2 state named by the charge-interposer tester's settings"}
                continue
            tree = trees["gi_prod_dir" if n == "wican_prod" else "gi_images"]
            v = app_version(b)
            per[n] = {"board": im.board, "elf_sha256": im.expect, "code_hash": None,
                      "commit_clean": "%s clean=%s" % (v, tree["clean"] and tree["at"] and v == commit)}
        clean = all(t["clean"] and t["at"] for t in trees.values()) and all(
            app_version(im.params["bin"]) == commit for im in images.values() if im.params.get("bin"))
        return {"commit": commit, "dirty": not clean, "code_hash": None, "trees": trees, "images": per,
                "gi_tools": git_state(need(settings, "gi_tools"))}
    return code_info


def emulated_code_info(settings):
    """The emulated suite's code: gi_fw, clean at gi_commit (same rule as the bench's)."""
    commit = settings.get("gi_commit")

    def code_info(ctx):
        t = tree_at(need(settings, "gi_fw"), commit)
        return {"commit": commit, "dirty": not (t["clean"] and t["at"]), "code_hash": None, "gi_fw": t,
                "images": {}, "gi_tools": git_state(need(settings, "gi_tools"))}
    return code_info


def emulated(settings):
    """Parts H and C: no hardware, no bench lock (user, 2026-10-17: two suites per
    project). H runs on the host gi_host_ssh names from a git archive of the commit
    (baseline_host_run.sh); C's checks run idf.py here and are build-class, so the
    core throttles them while a wall-clock test runs."""
    commit = need(settings, "gi_commit")
    C = commit
    # H and B15host run this clone's harness (host-run commit, 2026-10-22), never the
    # host's SeaDrive copy: H sends its remote half over ssh, and B15host copies its
    # files to ~/gi_bench_<commit> first. The data they read is the host's reverse-it
    # root (gi_host_root); gi_paths checks the DBCs there against this clone's pins.
    # (a subshell, not braces, round both: the core reads braces as placeholders)
    host_half = ("set -o pipefail; (tar cf - -C \"%s\" %s | ssh -o BatchMode=yes {gi_host_ssh} "
                 "'rm -rf ~/gi_bench_%s && mkdir -p ~/gi_bench_%s && tar xf - -C ~/gi_bench_%s' && "
                 "ssh -o BatchMode=yes {gi_host_ssh} 'cd {gi_host_root} && REVERSE_IT_DIR=\"$PWD\" python3 -B "
                 "~/gi_bench_%s/diag_decode_check.py ~/gi_baseline_%s/test/gen_inhibit_host 2>&1; echo \"exit $?\"') "
                 "> \"{outdir}/B15_diag_decode_check_host_%s.txt\" 2>&1"
                 % (HARNESS, " ".join(B15_FILES), C, C, C, C, C, C))
    groups = [
        Group("host", checks=[
            Check(id="H", argv=["{bash}", "%s/baseline_host_run.sh" % HARNESS, C], cwd="{reverse_it}",
                  env={"GI_OUT": "{outdir}", "FW": "{gi_fw}", "REMOTE": "{gi_host_ssh}", "RROOT": "{gi_host_root}"},
                  timeout_s=7200, outputs=["host_run.log"], judge=judged("H")),
            Check(id="B15host", argv=["{bash}", "-c", host_half], cwd="{reverse_it}", timeout_s=900,
                  outputs=["B15_diag_decode_check_host_%s.txt" % C], judge=judged("B15host")),
        ]),
        Group("build_checks", checks=[
            Check(id="C5_C6_scan", argv=["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                         "%s/c_checks_run.ps1" % HARNESS, "-Clone", "{gi_checks_clone}", "-Commit", C,
                                         "-Components", "{gi_components}", "-OutDir", "{outdir}"],
                  cwd="{reverse_it}", timeout_s=7200, cls="build",
                  outputs=["%s_%s.txt" % (c, C) for c in ("stale_name_check", "name_once_check", "scan_scope_check")],
                  judge=judged("C5", "C6", "C2scan")),
            Check(id="C1C7", argv=["true"], skip="its command (tester_rows_on_images.py over the built images) is not yet in the suite"),
            Check(id="C3", argv=["true"], skip="its command (test_refusal_pairs.py) is not yet in the suite"),
        ]),
    ]
    return Suite(name="gen-inhibit-emulated", role=ROLE, groups=groups,
                 code_info=emulated_code_info(settings))
