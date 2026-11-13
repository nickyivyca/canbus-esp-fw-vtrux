"""The WiCAN as a board of the interposer's bench suite (tester, 2026-10-23;
the reviewer's plan for the bench-lock cutover; the USB load 2026-11-12;
identify and the hooks over USB, 2026-11-13).

Under the bench log (shared/bench SPEC.md section 6) the runner writes every
release and free_paused with a read of each board in bench_lock.BOARDS, the
WiCAN included, and it reads a board only through that board's identify or
end hook. So the bench suite carries the WiCAN as a board. Everything here
reaches the WiCAN over its own USB, through esptool: madhouse has no Wi-Fi
adapter, and the user ruled the USB form for the interposer's bench
(2026-11-12, "build the USB form", relayed by the reviewer 2026-11-13; it
replaces the gen-inhibit reviewer's HTTP conditions for this bench). An
HTTP or OTA form comes back as its own change when the adapter arrives.

  image     "wican_man" on board "wican": the WiCAN's pinned image,
            wican_pin.json (wican_pin.py), a gen-inhibit build pinned as a
            tool of this harness (AGENTS.md: never anything built from an
            interposer tree; user, 2026-11-11: the madhouse rebuild of
            gen-inhibit 7f2d5032 _man). Its expected identity is the pin's
            image hash, the app descriptor's elf_sha256. Its files are read
            from the local settings' wican_image_dir, which must be a
            folder when the suite is built.
  the port  every esptool call goes to the WiCAN's own port: on Linux
            settings.wican_esptool_device, its USB IDs {vid, pid, serial,
            interface}, resolved by the core (vtrux_bench.serial_dev through
            flash.device_port, DEVICES-SPEC S1-S3) when the call is about to
            run; on Windows settings.wican_esptool_port. It never falls back
            to settings.esptool.device or .port, which are the X2's: with no
            device of its own nothing runs. Before anything else each step
            runs `esptool chip_id` there and refuses unless the chip is the
            pin's (ESP32-C3) with settings.wican_mac.
  flasher   "wican-esptool": the USB load. It first checks every pinned
            file in wican_image_dir (size, sha256, md5: wican_pin.check),
            then calls the core's esptool flasher
            (vtrux_bench.run.flash.esptool_flash) with the WiCAN's own params
            (wican_params): the pinned files at their offsets with their
            md5s, the pin's chip with the WiCAN's MAC, and the WiCAN's
            device (port on Windows), which take precedence over the X2's
            settings.esptool (flash.device_port: params first). esptool's
            command and baud are the core flasher's, settings.esptool.argv
            and .baud.
  identify  the elf_sha256 of the app the WiCAN boots, read off its flash
            (read_identity): after the chip_id check, a read_flash of the
            partition table (0x8000, 0xC00 bytes) and one of the otadata
            partition it names (two sectors); the boot slot from otadata as
            ESP-IDF's
            bootloader picks it (the highest ota_seq of the two entries
            whose CRC holds and whose state is not invalid or aborted, slot
            (seq - 1) mod the number of OTA app partitions; with no valid
            entry the factory app, or else ota_0); then one read_flash of
            that slot's first 256 bytes, the image header (magic 0xE9), the
            first segment's header and the app descriptor (magic
            0xABCD5432, elf_sha256 at descriptor offset 0x90). The slot
            matters: an OTA by anyone leaves the WiCAN booting ota_1, not
            the 0x10000 the load writes. A chip that is not the WiCAN, a
            read that fails, or a table, entry, header or descriptor that
            does not parse RAISES IdentifyError, so the runner stops without
            flashing. Each read's output and bytes stay in the log folder,
            and identify.json holds the port, the IDs matched, the chip, the
            partitions, both otadata entries, the slot and the hash.
  hooks     on_start and on_end (the release) each give the WiCAN one USB
            hard reset: `esptool --after hard_reset chip_id` on its port,
            with the chip checked. Nothing about
            the WiCAN's state (armed or not) is read: identify is what reads
            the image. The X2 has no step.

The gen-inhibit tools the HTTP form ran (wican_reboot.py, page_state.py,
settings.gi_tools) are no longer read. A missing settings key or folder
refuses the suite, naming it.
"""

import json
import os
import re
import struct
import time
import zlib

import wican_pin

IMAGE = "wican_man"
BOARD = "wican"
FLASHER = "wican-esptool"
SHA_RX = re.compile(r"^[0-9a-f]{64}$")
MAC_RX = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
ESPTOOL_TIMEOUT_S = 120
HARD_RESET = ("--after", "hard_reset")      # the hooks' reset

# the flash layout ESP-IDF writes (v5.4: esp_flash_partitions.h,
# esp_app_format.h, esp_ota_ops / bootloader_utility.c)
PTABLE_OFFSET = 0x8000
PTABLE_SIZE = 0xC00
PART_MAGIC = 0x50AA
PART_MD5_MAGIC = 0xEBEB
APP, DATA = 0x00, 0x01
FACTORY, OTA_0, OTA_LAST, OTADATA = 0x00, 0x10, 0x1F, 0x00
OTA_STATE_INVALID, OTA_STATE_ABORTED = 3, 4
SECTOR = 0x1000
HEAD_READ = 0x100
IMAGE_MAGIC = 0xE9
DESC_AT = 0x20          # image header (24 bytes) + first segment header (8)
DESC_MAGIC = 0xABCD5432
ELF_SHA_AT = 0x90       # in esp_app_desc_t


class WicanSourceError(ValueError):
    """The WiCAN's identity or settings cannot be resolved."""


class IdentifyError(RuntimeError):
    """identify could not look; not the same as "on another image"."""


def _need(settings, key):
    v = settings.get(key)
    if not v:
        raise WicanSourceError("settings.%s is not set" % key)
    return v


def image_dir(settings):
    """settings.wican_image_dir, which must be a folder."""
    d = _need(settings, "wican_image_dir")
    if not os.path.isdir(d):
        raise WicanSourceError("settings.wican_image_dir %s is not a folder" % d)
    return d


def wican_chip(pin, settings):
    """{"type", "mac"}: the pin's chip and the WiCAN's MAC from the local
    settings. WicanSourceError on a missing or malformed MAC."""
    mac = settings.get("wican_mac")
    if not (isinstance(mac, str) and MAC_RX.match(mac)):
        raise WicanSourceError("settings.wican_mac is %r, not the WiCAN's MAC "
                               "(aa:bb:cc:dd:ee:ff)" % (mac,))
    return {"type": pin["image"]["chip"], "mac": mac.lower()}


def wican_port_params(settings, env):
    """{"device": ...} (env "linux") or {"port": ...} (env "windows"): the
    WiCAN's own, never the X2's settings.esptool. WicanSourceError without
    one."""
    if env == "windows":
        port = settings.get("wican_esptool_port")
        if not port:
            raise WicanSourceError("settings.wican_esptool_port is not set: the "
                                   "WiCAN is never reached through the X2's port")
        return {"port": port}
    dev = settings.get("wican_esptool_device")
    if not (isinstance(dev, dict) and dev.get("serial")):
        raise WicanSourceError("settings.wican_esptool_device is %r, not "
                               "{vid, pid, serial, interface}: the WiCAN is "
                               "never reached through the X2's device" % (dev,))
    return {"device": dict(dev)}


def wican_params(pin, folder, settings, env):
    """The esptool flasher's params for the pinned image: files, md5, chip
    and the WiCAN's own device (env "linux") or port (env "windows").
    WicanSourceError when a setting it needs is missing or malformed."""
    files, md5 = [], {}
    for f in pin["image"]["files"]:
        path = os.path.join(folder, *f["name"].split("/"))
        files.append([f["offset"], path])
        md5[path] = f["md5"]
    out = {"files": files, "md5": md5, "chip": wican_chip(pin, settings)}
    out.update(wican_port_params(settings, env))
    return out


# ---- the flash layout --------------------------------------------------
def parse_ptable(data):
    """[{type, subtype, offset, size, label}] from the partition table's
    bytes, up to its end marker or MD5 entry. IdentifyError if an entry
    has another magic before either, or there is no app partition."""
    parts = []
    for at in range(0, len(data) - 31, 32):
        e = data[at:at + 32]
        magic = struct.unpack_from("<H", e)[0]
        if magic in (0xFFFF, PART_MD5_MAGIC):
            break
        if magic != PART_MAGIC:
            raise IdentifyError("partition table entry %d has magic 0x%04x"
                                % (at // 32, magic))
        typ, sub, off, size = struct.unpack_from("<BBII", e, 2)
        label = e[12:28].split(b"\0")[0].decode("ascii", "replace")
        parts.append({"type": typ, "subtype": sub, "offset": off,
                      "size": size, "label": label})
    if not any(p["type"] == APP for p in parts):
        raise IdentifyError("the partition table has no app partition (%d "
                            "entries)" % len(parts))
    return parts


def otadata_entry(data):
    """One esp_ota_select_entry_t (32 bytes): {seq, state, crc_ok,
    valid}."""
    seq = struct.unpack_from("<I", data, 0)[0]
    state, crc = struct.unpack_from("<II", data, 24)
    crc_ok = crc == zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)
    valid = (seq != 0xFFFFFFFF and crc_ok
             and state not in (OTA_STATE_INVALID, OTA_STATE_ABORTED))
    return {"seq": seq, "state": state, "crc_ok": crc_ok, "valid": valid}


def boot_slot(parts, otadata):
    """(partition, entries): the app partition the bootloader boots, and
    the two otadata entries read. otadata is the otadata partition's bytes
    (two sectors), or None if the table has none."""
    apps = {p["subtype"]: p for p in parts if p["type"] == APP}
    otas = [apps[s] for s in sorted(apps) if OTA_0 <= s <= OTA_LAST]
    entries = []
    if otadata is not None:
        entries = [otadata_entry(otadata[k * SECTOR:k * SECTOR + 32])
                   for k in (0, 1)]
    good = [e for e in entries if e["valid"]]
    if good and otas:
        seq = max(e["seq"] for e in good)
        return otas[(seq - 1) % len(otas)], entries
    if FACTORY in apps:
        return apps[FACTORY], entries
    if OTA_0 in apps:
        return apps[OTA_0], entries
    raise IdentifyError("no valid otadata entry, and no factory or ota_0 "
                        "app partition")


def descriptor_sha(head):
    """The elf_sha256 in an app image's first bytes. IdentifyError unless
    the image header and the app descriptor both have their magic."""
    if len(head) < DESC_AT + ELF_SHA_AT + 32:
        raise IdentifyError("only %d bytes of the app read" % len(head))
    if head[0] != IMAGE_MAGIC:
        raise IdentifyError("no app image at the boot slot (first byte "
                            "0x%02x, not 0xe9)" % head[0])
    magic = struct.unpack_from("<I", head, DESC_AT)[0]
    if magic != DESC_MAGIC:
        raise IdentifyError("no app descriptor at the boot slot (magic "
                            "0x%08x, not 0x%08x)" % (magic, DESC_MAGIC))
    at = DESC_AT + ELF_SHA_AT
    return head[at:at + 32].hex()


# ---- esptool on the WiCAN's port --------------------------------------
def _core_flash():
    from vtrux_bench.run import flash
    return flash


def _procs():
    from vtrux_bench.run import procs
    return procs


class _Esptool:
    """esptool calls on the WiCAN's port, each logged in logdir. The port is
    resolved once, by the core, from the WiCAN's own IDs (Linux) or port
    (Windows); raises WicanSourceError or the core's serial_dev.Refused
    when there is none."""

    def __init__(self, pin, settings, logdir, tag):
        core = _core_flash()
        argv = (settings.get("esptool") or {}).get("argv")
        if not argv:
            raise WicanSourceError("settings.esptool.argv is not set")
        self.argv, self.logdir, self.tag = list(argv), logdir, tag
        self.chip = wican_chip(pin, settings)
        # {} for et: the X2's settings.esptool.device and .port never reach
        # the lookup
        self.port, self.device = core.device_port(
            wican_port_params(settings, core.this_env()), {})
        self.n = 0

    def run(self, args, what):
        self.n += 1
        log = os.path.join(self.logdir, "wican_%s_%d_%s.txt"
                           % (self.tag, self.n, what))
        rc, to = _procs().run_local(
            self.argv + ["--port", self.port] + args, None,
            {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            ESPTOOL_TIMEOUT_S, log)
        with open(log, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return rc, to, text, log

    def chip_check(self, before=()):
        """chip_id on the port, after the esptool options `before`; (type,
        mac). Raises IdentifyError unless it ran and the chip is the
        WiCAN's."""
        core = _core_flash()
        rc, to, text, log = self.run(list(before) + [core.COMMANDS["chip_id"]],
                                     "chip_id")
        if rc != 0:
            raise IdentifyError("esptool chip_id on %s failed (rc=%s%s; %s)"
                                % (self.port, rc, ", timeout" if to else "",
                                   os.path.basename(log)))
        why = core.chip_mismatch(text, self.chip)
        if why:
            raise IdentifyError("chip_id on %s: %s; not the WiCAN, nothing "
                                "read" % (self.port, why))
        return core.chip_read(text)

    def read(self, offset, size, what):
        out = os.path.join(self.logdir, "wican_%s_%s_0x%x.bin"
                           % (self.tag, what, offset))
        rc, to, _t, log = self.run(
            ["read_flash", "0x%x" % offset, "0x%x" % size, out], what)
        if rc != 0 or not os.path.isfile(out):
            raise IdentifyError("esptool read_flash 0x%x 0x%x on %s failed "
                                "(rc=%s%s; %s)" % (offset, size, self.port, rc,
                                                   ", timeout" if to else "",
                                                   os.path.basename(log)))
        with open(out, "rb") as f:
            data = f.read()
        if len(data) != size:
            raise IdentifyError("read_flash 0x%x gave %d bytes, not %d"
                                % (offset, len(data), size))
        return data


def read_identity(pin, settings, logdir):
    """The WiCAN's boot-slot elf_sha256 over USB, and the record (also
    written to <logdir>/identify.json). Raises IdentifyError (or
    WicanSourceError) when it cannot look."""
    rec = {"step": "identify"}
    try:
        try:
            et = _Esptool(pin, settings, logdir, "identify")
        except Exception as e:                             # noqa: BLE001
            raise IdentifyError("the WiCAN's port: %s" % e)
        rec.update(port=et.port, device=et.device)
        rec["chip"] = list(et.chip_check())
        # the table and what follows it, through the otadata partition
        # (one read when otadata is next to the table, as on the WiCAN)
        head = et.read(PTABLE_OFFSET, PTABLE_SIZE, "ptable")
        parts = parse_ptable(head)
        rec["partitions"] = parts
        od = [p for p in parts if p["type"] == DATA and p["subtype"] == OTADATA]
        otadata = None
        if od:
            otadata = et.read(od[0]["offset"], 2 * SECTOR, "otadata")
        slot, entries = boot_slot(parts, otadata)
        rec.update(otadata=entries, slot=slot)
        sha = descriptor_sha(et.read(slot["offset"], HEAD_READ, "app"))
        rec["elf_sha256"] = sha
        return sha, rec
    except IdentifyError as e:
        rec["error"] = str(e)
        raise
    finally:
        with open(os.path.join(logdir, "identify.json"), "w",
                  encoding="utf-8") as f:
            json.dump(rec, f, indent=1)


def make(settings, pin=None):
    """{image, flasher, identify, on_start, on_end} for the bench suite.
    pin: the pin as wican_pin.load() returns it (default: the committed
    wican_pin.json; tests pass their own). Raises WicanSourceError when
    anything it rests on is missing."""
    from vtrux_bench.run import Image
    pin = pin or wican_pin.load()
    folder = image_dir(settings)
    expect = pin["image"]["image_hash"]
    image = Image(IMAGE, FLASHER, {"pin": pin["pin"]}, expect, board=BOARD,
                  note="the pinned gen-inhibit image %s (wican_pin.json), loaded "
                  "and identified over USB through the core's esptool "
                  "(bench_wican.py)" % pin["pin"])

    def identify(img, ctx, logdir):
        sha, _rec = read_identity(pin, settings, logdir)
        return sha

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
            tag = "%s_%s" % (step, time.strftime("%Y%m%dT%H%M%S"))
            try:
                et = _Esptool(pin, settings, logdir, tag)
                found = {"port": et.port, "device": et.device}
                typ, mac = et.chip_check(HARD_RESET)
            except Exception as e:                         # noqa: BLE001
                return dict(ok=False, detail="the WiCAN's USB hard reset "
                            "(%s) failed: %s" % (step, e))
            found.update(chip=typ, mac=mac)
            return dict(ok=True, detail="the WiCAN hard-reset over USB on %s "
                        "(chip_id, %s %s)" % (et.port, typ, mac), found=found)
        return hook

    return dict(image=image, flasher=flasher, identify=identify,
                on_start=board_step("start"), on_end=board_step("release"))
