"""The WiCAN as a board of the interposer's bench suite (tester, 2026-10-23;
the reviewer's plan for the bench-lock cutover, with the gen-inhibit
reviewer's conditions).

Under the bench log (shared/bench SPEC.md section 6) the runner writes every
release and free_paused with a read of each board in bench_lock.BOARDS, the
WiCAN included, and it reads a board only through that board's identify or
end hook. So the bench suite carries the WiCAN as a board it never flashes:

  image     "wican_man" on board "wican". Its expected identity is resolved
            when the suite is built, read-only, from gen-inhibit's own source
            for its standing image (both reviewers, 2026-10-23):
            settings.gi_chain_settings names gen-inhibit's chain settings
            JSON, whose key gi_images names the folder; <gi_images>/
            build-tester-man/ must hold exactly one wican-fw_obd_*.bin; the
            identity is build_lookup.elf_sha256_of_bin() of it. Never a
            copied hash: the 25f50ac0... in bench-holder.md and the run
            records are copies.
  flasher   "wican-never" always refuses, unrecoverable: an interposer
            worktree never flashes WiCAN firmware (AGENTS.md). The runner
            then stops the bench (on_flash_failure "stop-bench"), and the
            refusal names what identify read.
  identify  the elf_sha256 off the WiCAN's page, through gen-inhibit's
            page_state.read_page. A page that cannot be read RAISES, so the
            runner stops without flashing.
  hooks     on_start runs wican_reboot.py --first wican_man; on_end runs
            --want wican_man (--want-unknown when the run does not know).
            --known names wican_man alone, so a WiCAN on any other image
            fails the start hook and the runner stops the bench before it
            identifies or flashes anything (the gen-inhibit reviewer: no
            fallback, no retry, no flash). The X2 has no step.

wican_reboot.py and page_state.py are gen-inhibit's, taken read-only from
settings.gi_tools (gen-inhibit's tools/gen_inhibit in a checkout on this
machine), never copied onto this branch: a project branch holds only its own
folder. A missing settings key, file or folder refuses the suite, naming it.

This module joins no Wi-Fi network: the PC must be on the WiCAN's AP. A page
that cannot be read fails the start hook, saying so.
"""

import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import build_lookup

IMAGE = "wican_man"
BOARD = "wican"
FLASHER = "wican-never"
BUILD_DIR = "build-tester-man"
# Mirrors gen-inhibit's image_bin() in tools/gen_inhibit/suite_gen_inhibit.py
# (exactly one match, else refuse). A change to that glob there needs the
# same change here: it is part of the cross-project dependency (both
# reviewers, 2026-10-23).
BIN_GLOB = "wican-fw_obd_*.bin"
GI_FILES = ("wican_reboot.py", "page_state.py")
SHA_RX = re.compile(r"^[0-9a-f]{64}$")
PAGE_TIMEOUT_S = 8.0
REBOOT_TIMEOUT_S = 300


class WicanSourceError(ValueError):
    """The WiCAN's identity or gen-inhibit's tools cannot be resolved."""


class IdentifyError(RuntimeError):
    """identify could not look; not the same as "on another image"."""


def _need(settings, key):
    v = settings.get(key)
    if not v:
        raise WicanSourceError("settings.%s is not set" % key)
    return v


def gi_bench_dir(settings):
    """gen-inhibit's bench/ folder, holding wican_reboot.py and
    page_state.py (read-only)."""
    d = os.path.join(_need(settings, "gi_tools"), "bench")
    for f in GI_FILES:
        p = os.path.join(d, f)
        if not os.path.isfile(p):
            raise WicanSourceError(
                "gen-inhibit's %s is not at %s (settings.gi_tools, read-only "
                "from gen-inhibit's tree)" % (f, p))
    return d


def standing_bin(settings):
    """The one wican-fw_obd_*.bin in <gi_images>/build-tester-man/."""
    path = _need(settings, "gi_chain_settings")
    if not os.path.isfile(path):
        raise WicanSourceError("gen-inhibit's chain settings %s is missing "
                               "(settings.gi_chain_settings)" % path)
    try:
        with open(path, encoding="utf-8") as f:
            gs = json.load(f)
    except ValueError as e:
        raise WicanSourceError("gen-inhibit's chain settings %s is not JSON: "
                               "%s" % (path, e))
    gi = gs.get("gi_images") if isinstance(gs, dict) else None
    if not gi:
        raise WicanSourceError("%s has no gi_images" % path)
    folder = os.path.join(gi, BUILD_DIR)
    if not os.path.isdir(folder):
        raise WicanSourceError("%s (gi_images in %s) is not a folder"
                               % (folder, path))
    got = sorted(glob.glob(os.path.join(folder, BIN_GLOB)))
    if len(got) != 1:
        raise WicanSourceError("%d files match %s in %s, want exactly 1%s"
                               % (len(got), BIN_GLOB, folder,
                                  (": %s" % ", ".join(os.path.basename(g)
                                                      for g in got))
                                  if got else ""))
    return got[0]


def standing_expect(settings):
    """The ELF SHA-256 the WiCAN's standing image carries."""
    b = standing_bin(settings)
    sha = build_lookup.elf_sha256_of_bin(b)
    if not sha:
        raise WicanSourceError("%s carries no app descriptor "
                               "(build_lookup.elf_sha256_of_bin: None)" % b)
    return sha


def _load_page_state(bench_dir):
    spec = importlib.util.spec_from_file_location(
        "_gi_page_state", os.path.join(bench_dir, "page_state.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _python(settings):
    py = ((settings.get("hosts") or {}).get("local") or {}).get("python")
    return list(py) if py else [sys.executable]


def make(settings):
    """{image, flasher, identify, on_start, on_end} for the bench suite.
    Raises WicanSourceError when anything it rests on is missing."""
    from vtrux_bench.run import Image
    bench_dir = gi_bench_dir(settings)
    bin_path = standing_bin(settings)
    expect = standing_expect(settings)
    image = Image(IMAGE, FLASHER, {"source_bin": bin_path}, expect,
                  board=BOARD, note="never flashed by this suite; identity "
                  "from gen-inhibit's build-tester-man (bench_wican.py)")
    last = {"read": None}

    def wican_sha():
        try:
            page = _load_page_state(bench_dir).read_page(PAGE_TIMEOUT_S)
        except Exception as e:                             # noqa: BLE001
            raise IdentifyError("the WiCAN's page could not be read (is the "
                                "PC on its AP?): %r" % (e,))
        sha = page.get("elf_sha256") if isinstance(page, dict) else None
        if not (isinstance(sha, str) and SHA_RX.match(sha)):
            raise IdentifyError("the WiCAN's page has no elf_sha256 (%r)"
                                % (sha,))
        last["read"] = sha
        return sha

    def identify(img, ctx, logdir):
        return wican_sha()

    def flasher(img, params, settings_, logdir):
        return dict(ok=False, phase="refused", recoverable=False,
                    detail="this suite never flashes the WiCAN (AGENTS.md); "
                    "it read %s, not %s (%s)" % (
                        last["read"] or "nothing", img.name, img.expect))

    def board_step(step):
        def hook(board, img, ctx, logdir):
            if board != BOARD:
                return dict(ok=True, detail="no %s step for the %s"
                            % (step, board))
            stamp = time.strftime("%Y%m%dT%H%M%S")
            rec_path = os.path.join(logdir, "wican_reboot_%s_%s.json"
                                    % (step, stamp))
            argv = _python(settings) + [
                os.path.join(bench_dir, "wican_reboot.py"),
                "--label", "%s %s" % (step, board), "--out", rec_path,
                "--known", "%s=%s" % (IMAGE, expect)]
            if step == "start":
                argv += ["--first", IMAGE]
            elif img is not None:
                argv += ["--want", img.name]
            else:
                argv += ["--want-unknown"]
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            with open(rec_path[:-len(".json")] + ".txt", "w",
                      encoding="utf-8") as log:
                try:
                    rc = subprocess.run(argv, stdout=log,
                                        stderr=subprocess.STDOUT, env=env,
                                        timeout=REBOOT_TIMEOUT_S).returncode
                except subprocess.TimeoutExpired:
                    return dict(ok=False, detail="wican_reboot.py timed out "
                                "after %d s" % REBOOT_TIMEOUT_S)
            try:
                with open(rec_path, encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                return dict(ok=False, detail="wican_reboot.py left no record "
                            "(rc %s)" % rc)
            found = {k: rec.get(k) for k in (
                "found_image", "found_sha", "came_up_armed", "disarmed")}
            found["record"] = os.path.basename(rec_path)
            return dict(ok=rc == 0 and rec.get("verdict") == "PASS",
                        detail=rec.get("detail") or "wican_reboot.py rc %s"
                        % rc, found=found)
        return hook

    return dict(image=image, flasher=flasher, identify=identify,
                on_start=board_step("start"), on_end=board_step("release"))
