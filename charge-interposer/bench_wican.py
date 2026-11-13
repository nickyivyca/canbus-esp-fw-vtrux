"""The WiCAN as a board of the interposer's bench suite (tester, 2026-10-23;
the reviewer's plan for the bench-lock cutover, with the gen-inhibit
reviewer's conditions; the USB load 2026-11-12).

Under the bench log (shared/bench SPEC.md section 6) the runner writes every
release and free_paused with a read of each board in bench_lock.BOARDS, the
WiCAN included, and it reads a board only through that board's identify or
end hook. So the bench suite carries the WiCAN as a board:

  image     "wican_man" on board "wican": the WiCAN's pinned image,
            wican_pin.json (wican_pin.py), a gen-inhibit build pinned as a
            tool of this harness (AGENTS.md: never anything built from an
            interposer tree; user, 2026-11-11: the madhouse rebuild of
            gen-inhibit 7f2d5032 _man). Its expected identity is the pin's
            image hash, the app descriptor's elf_sha256. Its files are read
            from the local settings' wican_image_dir, which must be a
            folder when the suite is built.
  flasher   "wican-esptool": the USB load, the shared flasher's esptool path,
            until madhouse has its Wi-Fi adapter (AGENTS.md; user,
            2026-11-11). It first checks every pinned file in wican_image_dir
            (size, sha256, md5: wican_pin.check), then calls the core's
            esptool flasher (vtrux_bench.run.flash.esptool_flash) with the
            WiCAN's own params (wican_params): the pinned files at their
            offsets with their md5s, the chip the pin names (ESP32-C3) and
            the WiCAN's MAC from the local settings (wican_mac), and the
            WiCAN's own USB device, settings.wican_esptool_device (Linux;
            on Windows its own port, settings.wican_esptool_port). The
            image's device takes precedence over settings.esptool.device,
            which is the X2's (flash.device_port: params first). With no
            device of its own the flasher runs nothing: it never falls back
            to the X2's. esptool's command and baud are the core flasher's,
            settings.esptool.argv and .baud. The reviewer, 2026-11-12: the
            load first ((b)), identify and the hooks staying on HTTP.
  identify  the elf_sha256 off the WiCAN's page, through gen-inhibit's
            page_state.read_page. A page that cannot be read RAISES, so the
            runner stops without flashing. madhouse has no Wi-Fi adapter
            yet, so there a bench run stops at the WiCAN's start hook.
  hooks     on_start runs wican_reboot.py --first wican_man; on_end runs
            --want wican_man (--want-unknown when the run does not know).
            --known names wican_man alone, so a WiCAN on any other image
            fails the start hook and the runner stops the bench before it
            identifies or flashes anything (the gen-inhibit reviewer: no
            fallback, no retry, no flash). The flasher is reached only when
            the WiCAN reads another image after a clean start hook. The X2
            has no step.

wican_reboot.py and page_state.py are gen-inhibit's, taken read-only from
settings.gi_tools (gen-inhibit's tools/gen_inhibit in a checkout on this
machine), never copied onto this branch: a project branch holds only its own
folder. A missing settings key, file or folder refuses the suite, naming it.

This module joins no Wi-Fi network: the PC must be on the WiCAN's AP. A page
that cannot be read fails the start hook, saying so.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import wican_pin

IMAGE = "wican_man"
BOARD = "wican"
FLASHER = "wican-esptool"
GI_FILES = ("wican_reboot.py", "page_state.py")
SHA_RX = re.compile(r"^[0-9a-f]{64}$")
MAC_RX = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
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


def image_dir(settings):
    """settings.wican_image_dir, which must be a folder."""
    d = _need(settings, "wican_image_dir")
    if not os.path.isdir(d):
        raise WicanSourceError("settings.wican_image_dir %s is not a folder" % d)
    return d


def wican_params(pin, folder, settings, env):
    """The esptool flasher's params for the pinned image: files, md5, chip
    and the WiCAN's own device (env "linux") or port (env "windows").
    WicanSourceError when a setting it needs is missing or malformed."""
    files, md5 = [], {}
    for f in pin["image"]["files"]:
        path = os.path.join(folder, *f["name"].split("/"))
        files.append([f["offset"], path])
        md5[path] = f["md5"]
    mac = settings.get("wican_mac")
    if not (isinstance(mac, str) and MAC_RX.match(mac)):
        raise WicanSourceError("settings.wican_mac is %r, not the WiCAN's MAC "
                               "(aa:bb:cc:dd:ee:ff)" % (mac,))
    out = {"files": files, "md5": md5,
           "chip": {"type": pin["image"]["chip"], "mac": mac.lower()}}
    if env == "windows":
        port = settings.get("wican_esptool_port")
        if not port:
            raise WicanSourceError("settings.wican_esptool_port is not set: the "
                                   "WiCAN is never loaded through the X2's port")
        out["port"] = port
    else:
        dev = settings.get("wican_esptool_device")
        if not (isinstance(dev, dict) and dev.get("serial")):
            raise WicanSourceError("settings.wican_esptool_device is %r, not "
                                   "{vid, pid, serial, interface}: the WiCAN is "
                                   "never loaded through the X2's device" % (dev,))
        out["device"] = dict(dev)
    return out


def _load_page_state(bench_dir):
    spec = importlib.util.spec_from_file_location(
        "_gi_page_state", os.path.join(bench_dir, "page_state.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _python(settings):
    py = ((settings.get("hosts") or {}).get("local") or {}).get("python")
    return list(py) if py else [sys.executable]


def _core_flash():
    from vtrux_bench.run import flash
    return flash


def make(settings, pin=None):
    """{image, flasher, identify, on_start, on_end} for the bench suite.
    pin: the pin as wican_pin.load() returns it (default: the committed
    wican_pin.json; tests pass their own). Raises WicanSourceError when
    anything it rests on is missing."""
    from vtrux_bench.run import Image
    pin = pin or wican_pin.load()
    bench_dir = gi_bench_dir(settings)
    folder = image_dir(settings)
    expect = pin["image"]["image_hash"]
    image = Image(IMAGE, FLASHER, {"pin": pin["pin"]}, expect, board=BOARD,
                  note="the pinned gen-inhibit image %s (wican_pin.json), loaded "
                  "over USB by the core's esptool flasher (bench_wican.py)"
                  % pin["pin"])
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
        bad = wican_pin.check(folder, pin)
        if bad:
            return dict(ok=False, phase="pin", recoverable=True,
                        detail="the pinned image %s's files in %s are not the "
                        "pin's: %s; nothing run" % (pin["pin"], folder,
                                                    "; ".join(bad)))
        core = _core_flash()
        try:
            p = wican_params(pin, folder, settings_, core.this_env())
        except WicanSourceError as e:
            return dict(ok=False, phase="device", recoverable=True,
                        detail="%s; nothing run" % e)
        return core.esptool_flash(img, dict(params, **p), settings_, logdir)

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
