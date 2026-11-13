"""Flashes one archived X2 test-image build onto the bench X2, from its
manifest row (x2_test_images SPEC T9-T11).

TEST EQUIPMENT. Shared harness of x2_test_images (owner: the testers, the
gen-inhibit reviewer's ruling of 2026-11-12), generic over the images.

    python3.13 -B flash_row.py IMAGE BUILD --settings FILE --role ROLE \\
        --log-dir DIR [--dev] [--check-only] [--archive DIR] [--lock-log FILE]

IMAGE is the image's folder name (slcan_spi, slcan_can1, jammer). BUILD is
the app's SHA-256 as archive.py names its build folder: at least its first
16 hex digits (the folder name), or all 64. The rows are read from
<archive>/manifest.jsonl, the archive being
$REVERSE_IT_DIR/projects/vtrux/notes/artifacts/x2_test_images/<image>/
unless --archive names another copy laid out the same way. Run it from a
checkout of the shared tag you pin (T9), not one newer by chance: the
flasher, the lock code and the app reader it uses are that checkout's own
shared/bench and shared/tools, and nothing else is imported for them.

In order, stopping at the first refusal, with nothing written:
  1. the row: every manifest row whose app_sha256 starts with BUILD. None,
     or rows naming different builds or different files, is refused. A
     row not pinnable (SPEC T7: a dev build, for its builder's own bench
     work) is refused unless --dev is given.
  2. the files (T9: refuse files that differ): each of the row's flash
     files, by its bare name in <archive>/<the row's folder>/, must have
     the row's size, SHA-256 and MD5; their offsets must not repeat or
     overlap; the app at the row's app_offset must have the row's
     app_sha256; and the app, read by shared/tools' reader (vtrux_build,
     its checksum and SHA verified), must name the row's project, ELF hash
     (image_hash, R1) and code hash (R2), for the chip in the image's own
     x2_image.json.
  3. --check-only stops here, exit 0.
  4. the chip the flasher must find (T11, never the WiCAN): its type from
     the image's x2_image.json chip, its MAC from the local settings,
     esptool.chip.mac or else the top-level x2_mac. A settings
     esptool.chip.type naming another chip is refused. The repo is public,
     so the MAC is only ever read from the settings file at run time.
  5. the bench lock (AGENTS.md "The shared bench"): the log's holder must
     be ROLE, with its pid alive in the bench environment.
  6. canx2 (shared/bench SPEC section 8: "If canx2 is attached as SLCAN,
     it is detached before a flash"): netdev canx2, or an slcand attached
     as canx2, is refused with the canctl command that detaches it. This
     tool does not detach it itself.
  7. the flash: shared/bench's esptool flasher (vtrux_bench.run.flash.
     esptool_flash) with the row's files at their offsets and their MD5s,
     which checks them again, finds the X2's port from its USB IDs
     (settings esptool.device), checks auto-reset and the chip before and
     after, and needs "Hash of data verified" for every file.

Every step's result goes to DIR/flash_row.json beside the flasher's own
logs; DIR must not exist yet, and must be outside the checkout this tool
runs from (a pinned checkout counts its ignored files, shared/tools R7). The FLASHED line
printed before the record's path is the one a release note carries (shared/bench SPEC section 8: "each image
it flashed, with its pin or build and app sha256"). The identity read
after the flash is the consumer's (T10).

Exit 0 flashed (or checked, with --check-only), 1 the flash failed (see
recoverable in the result), 2 refused, nothing written.
"""

import argparse
import datetime
import json
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", ".."))
CHECKOUT = os.path.dirname(SHARED)      # the tree holding shared/
sys.path.insert(0, HERE)
import archive  # noqa: E402
import check_image  # noqa: E402  (puts this checkout's shared/tools on the path)
from vtrux_build.image import AppImage, ImageError  # noqa: E402

BENCH = os.path.join(SHARED, "bench")
sys.path.insert(0, BENCH)
import vtrux_bench  # noqa: E402
from vtrux_bench import bench_lock as BL  # noqa: E402
from vtrux_bench.run import flash as FL  # noqa: E402

SHA_PREFIX_RE = re.compile(r"[0-9a-f]{16,64}\Z")
CANX2 = "canx2"
SLCAND = "slcand"
SYSFS_NET = "/sys/class/net"
PROC = "/proc"
DETACH = "sudo -n /usr/local/sbin/canctl slcan detach canx2"


class Refused(Exception):
    pass


def chip_type(chip):
    """x2_image.json's chip (an ESP-IDF target) as esptool names it:
    esp32s3 -> ESP32-S3."""
    if not chip or not chip.startswith("esp32"):
        raise Refused("x2_image.json names no ESP32 chip (%r)" % (chip,))
    rest = chip[len("esp32"):]
    return "ESP32" + ("-" + rest.upper() if rest else "")


def own_bench():
    """The flasher and lock are this checkout's shared/bench, never another
    vtrux_bench imported first."""
    got = os.path.realpath(os.path.dirname(vtrux_bench.__file__))
    want = os.path.realpath(os.path.join(BENCH, "vtrux_bench"))
    if got != want:
        raise Refused("vtrux_bench was imported from %s, not this checkout's "
                      "%s" % (got, want))


def read_rows(archive_dir):
    """[(line number, row)] of archive_dir/manifest.jsonl."""
    p = os.path.join(archive_dir, "manifest.jsonl")
    if not os.path.isfile(p):
        raise Refused("no manifest %s" % p)
    out = []
    with open(p, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append((n, json.loads(line)))
            except ValueError:
                raise Refused("%s line %d is not JSON" % (p, n))
    return out


def _files_key(row):
    return [(f.get("offset"), f.get("name"), f.get("size"), f.get("sha256"),
             f.get("md5")) for f in row.get("files") or []]


def select_row(rows, image, build, dev):
    """-> (row, line numbers, pinnable). Step 1."""
    build = (build or "").lower()
    if not SHA_PREFIX_RE.match(build):
        raise Refused("BUILD must be 16 to 64 lowercase hex digits of the "
                      "app's SHA-256, not %r" % build)
    hits = [(n, r) for n, r in rows
            if str(r.get("app_sha256", "")).startswith(build)]
    if not hits:
        raise Refused("no manifest row for build %s" % build)
    first = hits[0][1]
    for n, r in hits:
        if r.get("image") != image:
            raise Refused("manifest line %d is for image %r, not %r"
                          % (n, r.get("image"), image))
        if r.get("app_sha256") != first.get("app_sha256"):
            raise Refused("build %s matches more than one app (lines %s): "
                          "give more digits" % (build, [h[0] for h in hits]))
        for k in ("folder", "app_offset", "image_hash", "code_hash",
                  "project_name"):
            if r.get(k) != first.get(k):
                raise Refused("manifest lines %s disagree on %s"
                              % ([h[0] for h in hits], k))
        if _files_key(r) != _files_key(first):
            raise Refused("manifest lines %s disagree on the files"
                          % [h[0] for h in hits])
    pinnable = any(r.get("pinnable") is True for _n, r in hits)
    if not pinnable and not dev:
        raise Refused("build %s has no pinnable row (lines %s): a dev build, "
                      "for its builder's own bench work (SPEC T7); --dev "
                      "flashes it anyway" % (build, [h[0] for h in hits]))
    # the row reported: a pinnable one if there is one, else the last
    row = next((r for _n, r in reversed(hits) if r.get("pinnable") is True),
               hits[-1][1])
    return row, [h[0] for h in hits], pinnable


def check_files(row, archive_dir, chip):
    """-> [(offset int, path, md5)] in flash order. Step 2. Raises Refused
    for anything that differs from the row."""
    folder = row.get("folder") or ""
    if not re.match(r"builds/[0-9a-f]{16}\Z", folder) or \
            folder != "builds/%s" % str(row.get("app_sha256", ""))[:16]:
        raise Refused("the row's folder %r is not builds/<the app sha256's "
                      "first 16 digits>" % folder)
    base = os.path.join(archive_dir, folder)
    files = row.get("files") or []
    if not files:
        raise Refused("the row names no flash files")
    out = []
    for f in files:
        name = f.get("name") or ""
        if os.path.basename(name) != name or name in ("", ".", ".."):
            raise Refused("the row's file name %r is not a bare name" % name)
        try:
            off = int(str(f.get("offset")), 16)
        except ValueError:
            raise Refused("%s: offset %r is not hex" % (name, f.get("offset")))
        p = os.path.join(base, name)
        if not os.path.isfile(p):
            raise Refused("missing %s" % p)
        h = archive._hashes(p)
        for k in ("size", "sha256", "md5"):
            if h[k] != f.get(k):
                raise Refused("%s: %s is %s, the row says %s"
                              % (name, k, h[k], f.get(k)))
        out.append((off, p, h["md5"], h["size"], h["sha256"]))
    out.sort()
    for a, b in zip(out, out[1:]):
        if a[0] == b[0]:
            raise Refused("two files at offset 0x%x" % a[0])
        if a[0] + a[3] > b[0]:
            raise Refused("%s at 0x%x (%d bytes) overlaps %s at 0x%x"
                          % (os.path.basename(a[1]), a[0], a[3],
                             os.path.basename(b[1]), b[0]))
    try:
        app_off = int(str(row.get("app_offset")), 16)
    except ValueError:
        raise Refused("app_offset %r is not hex" % row.get("app_offset"))
    app = [o for o in out if o[0] == app_off]
    if not app:
        raise Refused("no file at the row's app_offset 0x%x" % app_off)
    if app[0][4] != row.get("app_sha256"):
        raise Refused("the file at 0x%x has sha256 %s, the row's app_sha256 "
                      "is %s" % (app_off, app[0][4], row.get("app_sha256")))
    try:
        img = AppImage.read(app[0][1])
    except (ImageError, OSError) as e:
        raise Refused("the app %s does not read as an ESP-IDF image: %s"
                      % (app[0][1], e))
    for what, have, want in (
            ("project_name", img.project_name, row.get("project_name")),
            ("image_hash (R1, the ELF's)", img.image_hash, row.get("image_hash")),
            ("code_hash (R2)", img.code_hash, row.get("code_hash")),
            ("chip", img.chip, chip)):
        if have != want:
            raise Refused("the app's %s is %s, the row (or x2_image.json) "
                          "says %s" % (what, have, want))
    return [(o, p, m) for o, p, m, _s, _h in out]


def want_chip(settings, typ):
    """{"type", "mac"} for the flasher. Step 4."""
    et = settings.get("esptool") or {}
    sc = et.get("chip") or {}
    if sc.get("type") and sc["type"] != typ:
        raise Refused("settings esptool.chip.type is %s, but the image is for "
                      "%s" % (sc["type"], typ))
    mac = sc.get("mac") or settings.get("x2_mac")
    if not mac:
        raise Refused("the settings name no X2 MAC (esptool.chip.mac or "
                      "x2_mac): the flasher writes nothing without it")
    return {"type": typ, "mac": mac}


def check_lock(log, role):
    """Step 5: the log's holder is role, alive."""
    if not BL.ROLE_RE.match(role or ""):
        raise Refused("%r is not a bench role" % role)
    try:
        state = BL.read_state(log)
    except (BL.Refused, OSError) as e:
        raise Refused("the bench lock cannot be read: %s" % e)
    h = state["holder"]
    if h is None or h["who"] != role:
        raise Refused("the bench lock is not held by %s (%s)"
                      % (role, BL.holder_text(state)))
    if not BL.bench_impl(state).pid_alive(h["pid"]):
        raise Refused("the bench lock's holder pid %d is not running"
                      % h["pid"])
    return BL.holder_text(state)


def canx2_attached(net_root=None, proc_root=None):
    """Step 6: a line naming what holds canx2, or None."""
    net_root = net_root or SYSFS_NET
    proc_root = proc_root or PROC
    found = []
    if os.path.lexists(os.path.join(net_root, CANX2)):
        found.append("netdev %s" % CANX2)
    if os.path.isdir(proc_root):
        for name in os.listdir(proc_root):
            if not name.isdigit():
                continue
            try:
                with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
            if len(argv) >= 2 and os.path.basename(argv[0]) == SLCAND \
                    and argv[-1] == CANX2:
                found.append("slcand pid %s" % name)
    return ", ".join(found) or None


def note_line(row, pinnable, lines):
    return ("FLASHED x2 %s %s build %s app_sha256 %s (%s, manifest line%s %s)"
            % (row["image"], row.get("variant"), row["folder"],
               row["app_sha256"],
               "shared tag %s" % row.get("shared_tag") if pinnable
               else "dev build, not pinnable",
               "s" if len(lines) > 1 else "", ",".join(map(str, lines))))


def run(image, build, settings, role, logdir, dev=False, check_only=False,
        archive_dir=None, lock_log=None, flasher=None):
    """The whole of flash_row.py. Returns (exit code, result dict). Raises
    nothing for a refusal: it is the result."""
    res = {"tool": "flash_row", "image": image, "build": build, "role": role,
           "dev": dev, "check_only": check_only,
           "utc": datetime.datetime.now(datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        own_bench()
        if not re.match(r"[A-Za-z0-9_]+\Z", image or ""):
            raise Refused("IMAGE %r is not an image folder name" % image)
        data = check_image.load_data(os.path.join(HERE, "..", image))
        if data is None or data.get("image") != image:
            raise Refused("this checkout has no image %s (%s)"
                          % (image, os.path.normpath(os.path.join(HERE, "..",
                                                                  image))))
        if archive_dir is None:
            try:
                archive_dir = archive.archive_root(image)
            except archive.Refused as e:
                raise Refused(str(e))
        res["archive"] = archive_dir
        row, lines, pinnable = select_row(read_rows(archive_dir), image,
                                          build, dev)
        res.update(row={k: row.get(k) for k in (
            "image", "variant", "project_name", "folder", "app_sha256",
            "image_hash", "code_hash", "shared_tag", "shared_commit",
            "version", "pinnable")}, manifest_lines=lines, pinnable=pinnable)
        files = check_files(row, archive_dir, data.get("chip"))
        res["files"] = [["0x%x" % o, os.path.basename(p), m]
                        for o, p, m in files]
        if check_only:
            res.update(ok=True, phase="checked")
            return 0, res
        chip = want_chip(settings, chip_type(data.get("chip")))
        res["holder"] = check_lock(lock_log or BL.default_log(), role)
        why = canx2_attached()
        if why:
            raise Refused("canx2 is attached as SLCAN (%s): detach it first, "
                          "%s" % (why, DETACH))
    except (Refused, BL.Refused, check_image.Refused) as e:
        res.update(ok=False, phase="refused", detail=str(e))
        return 2, res
    params = {"files": [["0x%x" % o, p] for o, p, _m in files],
              "md5": {p: m for _o, p, m in files},
              "chip": chip}
    img = types.SimpleNamespace(name="%s_%s" % (image, row["folder"][-16:]))
    out = (flasher or FL.esptool_flash)(img, params, settings, logdir)
    res["flash"] = {k: v for k, v in out.items() if k != "device"}
    res.update(ok=bool(out.get("ok")), phase="flash-" + str(out.get("phase")))
    if res["ok"]:
        res["note"] = note_line(row, pinnable, lines)
        return 0, res
    res["detail"] = out.get("detail")
    return 1, res


def _outside_checkout(path):
    """Is path outside the checkout this tool runs from (CHECKOUT)? A
    pinned checkout's digest counts its ignored files too (R7)."""
    p = os.path.realpath(os.path.abspath(path))
    top = os.path.realpath(CHECKOUT)
    return p != top and not p.startswith(top + os.sep)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image")
    ap.add_argument("build")
    ap.add_argument("--settings", required=True,
                    help="local settings JSON: esptool {argv, device, baud, "
                    "chip}, or x2_mac")
    ap.add_argument("--role", required=True,
                    help="the bench lock holder, e.g. 'tester (charge-interposer)'")
    ap.add_argument("--log-dir", required=True,
                    help="a new folder outside this checkout")
    ap.add_argument("--dev", action="store_true",
                    help="allow a build with no pinnable row (SPEC T7)")
    ap.add_argument("--check-only", action="store_true",
                    help="check the row and files, flash nothing")
    ap.add_argument("--archive", help="the image's archive folder (default "
                    "from REVERSE_IT_DIR)")
    ap.add_argument("--lock-log", help="the bench lock log (default from "
                    "REVERSE_IT_DIR)")
    a = ap.parse_args(argv)
    if os.path.exists(a.log_dir):
        print("REFUSED: %s exists; give a new folder" % a.log_dir)
        return 2
    if not _outside_checkout(a.log_dir):
        print("REFUSED: %s is inside this checkout, %s" % (a.log_dir,
                                                           CHECKOUT))
        return 2
    try:
        with open(a.settings, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, ValueError) as e:
        print("REFUSED: settings %s: %s" % (a.settings, e))
        return 2
    os.makedirs(a.log_dir)
    rc, res = run(a.image, a.build, settings, a.role, a.log_dir, a.dev,
                  a.check_only, a.archive, a.lock_log)
    with open(os.path.join(a.log_dir, "flash_row.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    if rc == 2:
        print("REFUSED: %s" % res["detail"])
    elif rc == 1:
        print("FLASH FAILED (%s): %s; recoverable over USB: %s"
              % (res["phase"], res["detail"],
                 res["flash"].get("recoverable")))
    elif a.check_only:
        print("CHECKED %s build %s: %d files match manifest line(s) %s%s"
              % (a.image, res["row"]["folder"], len(res["files"]),
                 ",".join(map(str, res["manifest_lines"])),
                 "" if res["pinnable"] else " (dev build, not pinnable)"))
    else:
        print(res["note"])
    print("record: %s" % os.path.join(a.log_dir, "flash_row.json"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
