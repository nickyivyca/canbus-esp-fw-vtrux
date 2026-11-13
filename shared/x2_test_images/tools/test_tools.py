"""Tests of check_image.py, archive.py and flash_row.py on stand-in builds:
the harness's own test, each check shown failing as well as passing.
flash_row.py runs shared/bench's real esptool flasher against a stand-in
esptool (a script that prints what esptool prints), a lock log of its own,
and made-up /sys/class/net and /proc trees, never this machine's.

TEST EQUIPMENT. Shared harness of x2_test_images (owner: the testers).

    python3.13 -B tools/test_tools.py        from x2_test_images/, or anywhere

The stand-ins are real formats, small: an ESP-IDF app image the shared
tooling's reader parses and verifies (vtrux_build.image), an ELF32 with a
symbol table, a build folder laid out as ESP-IDF lays it out, and a git
tree with a tag. The image data is the jammer's own x2_image.json, read
from ../jammer/. Everything is written under
<worktree>/scratch/x2-tools-tests/<pid>/ and removed at the end, pass or
fail; REVERSE_IT_DIR points into it, never at the real archive.
Exit 0: ALL PASS.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archive  # noqa: E402
import check_image  # noqa: E402

JAMMER_DATA = os.path.join(HERE, "..", "jammer", "x2_image.json")
FAILED = []
COUNT = [0]


def CHECK(cond, what):
    COUNT[0] += 1
    if not cond:
        FAILED.append(what)
        print("FAIL: %s" % what)


# ---- stand-in formats ----

def make_elf(symbols):
    """An ELF32 little-endian file with one symbol table."""
    strtab = b"\0" + b"".join(s.encode() + b"\0" for s in symbols)
    syms = [b"\0" * 16]
    off = 1
    for s in symbols:
        syms.append(struct.pack("<IIIBBH", off, 0x42000000, 4, 0x12, 0, 1))
        off += len(s) + 1
    symtab = b"".join(syms)
    shstr = b"\0.strtab\0.symtab\0.shstrtab\0"
    body_off = 52
    strtab_off = body_off
    symtab_off = strtab_off + len(strtab)
    shstr_off = symtab_off + len(symtab)
    shoff = shstr_off + len(shstr)
    shoff += (4 - shoff % 4) % 4
    sh = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (1, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1, 0),
        (9, 2, 0, 0, symtab_off, len(symtab), 1, 1, 4, 16),
        (17, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0),
    ]
    ident = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    hdr = ident + struct.pack("<HHIIIIIHHHHHH", 2, 94, 1, 0, 0, shoff, 0, 52,
                              0, 0, 40, len(sh), 3)
    data = hdr + strtab + symtab + shstr
    data += b"\0" * (shoff - len(data))
    for s in sh:
        data += struct.pack("<IIIIIIIIII", *s)
    return data


def make_app(project_name, version, elf_sha, payload=b"", chip_id=9,
             corrupt=False):
    """An ESP-IDF app image: one segment holding the descriptor and the
    payload, the checksum and the appended SHA-256."""
    desc = bytearray(256)
    struct.pack_into("<I", desc, 0, 0xABCD5432)
    desc[0x10:0x10 + len(version)] = version.encode()[:31]
    desc[0x30:0x30 + len(project_name)] = project_name.encode()[:31]
    desc[0x50:0x58] = b"12:00:00"
    desc[0x60:0x6B] = b"Nov 13 2026"
    desc[0x70:0x76] = b"v5.4.2"
    desc[0x90:0xB0] = bytes.fromhex(elf_sha)
    seg = bytes(desc) + payload
    hdr = bytearray(24)
    hdr[0] = 0xE9
    hdr[1] = 1
    struct.pack_into("<H", hdr, 12, chip_id)
    hdr[23] = 1
    b = bytearray(hdr) + struct.pack("<II", 0x3C000020, len(seg)) + seg
    o = len(b)
    cks_off = o + (15 - o % 16)
    b += b"\0" * (cks_off - o)
    x = 0xEF
    for v in seg:
        x ^= v
    b.append(x ^ (1 if corrupt else 0))
    b += hashlib.sha256(bytes(b)).digest()
    return bytes(b)


def jammer_data():
    with open(JAMMER_DATA, encoding="utf-8") as f:
        return json.load(f)


def good_symbols(data, extra=()):
    return (list(data["required_symbols"]) + ["sym_%d" % i for i in range(150)]
            + list(extra))


def make_build(build_dir, project_name, version="2026.01.01", symbols=None,
               strings=None, config=None, chip_id=9, elf_sha=None,
               corrupt=False):
    data = jammer_data()
    if symbols is None:
        symbols = good_symbols(data)
    if strings is None:
        strings = data["required_strings"]
    os.makedirs(os.path.join(build_dir, "bootloader"), exist_ok=True)
    os.makedirs(os.path.join(build_dir, "partition_table"), exist_ok=True)
    os.makedirs(os.path.join(build_dir, "config"), exist_ok=True)
    elf = make_elf(symbols)
    with open(os.path.join(build_dir, project_name + ".elf"), "wb") as f:
        f.write(elf)
    sha = elf_sha or hashlib.sha256(elf).hexdigest()
    payload = b"\0".join(s.encode() for s in strings) + b"\0"
    with open(os.path.join(build_dir, project_name + ".bin"), "wb") as f:
        f.write(make_app(project_name, version, sha, payload, chip_id, corrupt))
    for rel, content in (("bootloader/bootloader.bin", b"BOOT" * 64),
                         ("partition_table/partition-table.bin", b"PT" * 64),
                         ("ota_data_initial.bin", b"\xff" * 64)):
        with open(os.path.join(build_dir, rel), "wb") as f:
            f.write(content)
    fa = {"flash_files": {"0x0": "bootloader/bootloader.bin",
                          "0x8000": "partition_table/partition-table.bin",
                          "0xe000": "ota_data_initial.bin",
                          "0x10000": project_name + ".bin"},
          "app": {"offset": "0x10000", "file": project_name + ".bin"}}
    with open(os.path.join(build_dir, "flasher_args.json"), "w") as f:
        json.dump(fa, f)
    with open(os.path.join(build_dir, "project_description.json"), "w") as f:
        json.dump({"project_name": project_name, "app_elf": project_name + ".elf",
                   "app_bin": project_name + ".bin"}, f)
    cfg = {k[len("CONFIG_"):]: v for k, v in data["required_config"].items()}
    cfg.update(config or {})
    with open(os.path.join(build_dir, "config", "sdkconfig.json"), "w") as f:
        json.dump(cfg, f)


def image_folder(root, data=None):
    d = os.path.join(root, "img")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "x2_image.json"), "w", encoding="utf-8") as f:
        json.dump(data or jammer_data(), f, indent=2)
    with open(os.path.join(d, ".gitignore"), "w") as f:
        f.write("/build/\n/managed_components/\n")
    return d


def names(data=None):
    data = data or jammer_data()
    return {v: d["project_name"] for v, d in data["variants"].items()}


def failed_checks(r):
    return sorted(n for n, ok, _d in r.checks if not ok)


# ---- check_image ----

def test_check_image(tmp):
    n = names()
    img = image_folder(os.path.join(tmp, "c"))

    def run(variant="jammer", **kw):
        bd = os.path.join(img, "build", variant)
        shutil.rmtree(bd, ignore_errors=False) if os.path.isdir(bd) else None
        make_build(bd, kw.pop("pn", n[variant]), **kw)
        return check_image.check(img, variant)

    r = run()
    CHECK(r.ok and r.image_variant == "jammer", "good build passes: %s"
          % failed_checks(r))
    CHECK(len(r.checks) == 11, "11 checks ran, not %d" % len(r.checks))
    r = run("stop_a_only")
    CHECK(r.ok and r.image_variant == "stop_a_only", "stop_a_only as itself")

    r = run(symbols=good_symbols(jammer_data(), ["twai_driver_install_v2"]))
    CHECK(failed_checks(r) == ["no_forbidden"], "a TWAI driver symbol fails "
          "no_forbidden only: %s" % failed_checks(r))
    r = run(symbols=good_symbols(jammer_data(), ["twai_transmit"]))
    CHECK(failed_checks(r) == ["no_forbidden"], "twai_transmit fails")
    r = run(symbols=["app_main", "jam_drive_recessive", "mcp2515_reset_config"])
    CHECK(failed_checks(r) == ["elf_symbols"], "a near-empty symbol table "
          "fails elf_symbols: %s" % failed_checks(r))
    r = run(symbols=["sym_%d" % i for i in range(200)])
    CHECK(failed_checks(r) == ["elf_symbols"], "required symbols missing")
    r = run(elf_sha="00" * 32)
    CHECK(failed_checks(r) == ["elf_hash"], "an ELF hash mismatch fails "
          "elf_hash: %s" % failed_checks(r))
    r = run(strings=["nothing here"])
    CHECK(failed_checks(r) == ["strings"], "missing strings fail strings")
    r = run(config={"ESP_CONSOLE_NONE": False})
    CHECK(failed_checks(r) == ["config"], "a console on fails config")
    r = run(chip_id=5)
    CHECK(failed_checks(r) == ["chip"], "an esp32c3 image fails chip")
    r = run(corrupt=True)
    CHECK(failed_checks(r) == ["app_image"], "a bad checksum fails app_image")

    # The variant is the image's: a stop_a_only image offered as the jammer.
    r = run("jammer", pn=n["stop_a_only"])
    CHECK(failed_checks(r) == ["build_metadata", "variant"] or
          failed_checks(r) == ["variant"],
          "stop_a_only offered as jammer fails variant: %s" % failed_checks(r))
    CHECK(r.image_variant == "stop_a_only", "image variant read from image")
    r = run("jammer", pn="vtrux_jammer_x2_NOT_FOR_VEHICL")
    CHECK("project_name" in failed_checks(r) and r.image_variant is None,
          "an unknown (cut) name fails project_name: %s" % failed_checks(r))

    # Image data that cannot work: a name over 31 characters, a NOT FOR
    # VEHICLE image whose name does not say so.
    d = jammer_data()
    d["variants"]["stop_a_only"]["project_name"] = \
        "vtrux_jammer_x2_NOT_FOR_VEHICLE_stop_a_only"
    img2 = image_folder(os.path.join(tmp, "c2"), d)
    make_build(os.path.join(img2, "build", "jammer"), n["jammer"])
    r = check_image.check(img2, "jammer")
    CHECK(failed_checks(r) == ["project_name"], "a 43-character variant name "
          "in x2_image.json fails project_name: %s" % failed_checks(r))
    d = jammer_data()
    d["variants"]["jammer"]["project_name"] = "vtrux_jammer_x2"
    img3 = image_folder(os.path.join(tmp, "c3"), d)
    make_build(os.path.join(img3, "build", "jammer"), "vtrux_jammer_x2")
    r = check_image.check(img3, "jammer")
    CHECK(failed_checks(r) == ["not_for_vehicle"], "a name without "
          "NOT_FOR_VEHICLE fails: %s" % failed_checks(r))

    # Refusals: nothing to check.
    for what, fn in (
            ("unknown variant", lambda: check_image.check(img, "nope")),
            ("no build", lambda: check_image.check(img, "stop_b_only")),
            ("no image data", lambda: check_image.check(tmp, "jammer"))):
        try:
            fn()
            CHECK(False, "%s not refused" % what)
        except check_image.Refused:
            CHECK(True, what)
    os.remove(os.path.join(img, "build", "jammer", "ota_data_initial.bin"))
    try:
        check_image.check(img, "jammer")
        CHECK(False, "a missing flash file not refused")
    except check_image.Refused:
        CHECK(True, "missing flash file")

    # The command line: 0 PASS, 1 FAIL, 2 REFUSED.
    run()
    with redirect_stdout(io.StringIO()):
        rc0 = check_image.main([img, "jammer"])
        rc2 = check_image.main([img, "nope"])
    run("jammer", pn=n["stop_b_only"])
    with redirect_stdout(io.StringIO()):
        rc1 = check_image.main([img, "jammer"])
    CHECK((rc0, rc1, rc2) == (0, 1, 2), "exit codes %r" % ((rc0, rc1, rc2),))


# ---- archive ----

def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True,
                       text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def make_repo(root, tag="2026.01.01"):
    os.makedirs(root)
    git(root, "init", "-q", "-b", "trunk")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "tester")
    img = image_folder(root)
    with open(os.path.join(img, "tracked.txt"), "w") as f:
        f.write("tracked\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "image")
    if tag:
        git(root, "tag", tag)
    return img


def builder(pn_for=None, side_effect=None, rc=0):
    """A stand-in dev build: version from git describe at its start, as
    ESP-IDF's configure takes it."""
    def fn(image_dir, variant):
        version = git(image_dir, "describe", "--always", "--tags", "--dirty")
        if side_effect:
            side_effect(image_dir)
        if rc != 0:
            return rc, "stand-in"
        pn = (pn_for or names())[variant]
        make_build(os.path.join(image_dir, "build", variant), pn,
                   version=version)
        return 0, "stand-in build %s" % variant
    return fn


def manifest_rows(rit):
    p = os.path.join(rit, archive.ARCHIVE_REL, "x2_test_images", "jammer",
                     "manifest.jsonl")
    if not os.path.exists(p):
        return [], b""
    with open(p, "rb") as f:
        raw = f.read()
    return [json.loads(l) for l in raw.decode().splitlines() if l], raw


def refused(what, fn):
    try:
        fn()
        CHECK(False, "%s: not refused" % what)
        return None
    except archive.Refused as e:
        CHECK(True, what)
        return str(e)


def test_archive(tmp):
    rit = os.path.join(tmp, "rit")
    os.makedirs(os.path.join(rit, archive.ARCHIVE_REL))
    os.environ["REVERSE_IT_DIR"] = rit
    k = [0]

    def repo(tag="2026.01.01"):
        k[0] += 1
        return make_repo(os.path.join(tmp, "r%d" % k[0]), tag)

    # A tag build of the jammer from a clean tree: pinnable, every file
    # archived with its hashes, one row.
    img = repo()
    row, folder = archive.run(img, "jammer", "gen-inhibit tester",
                              build_fn=builder())
    CHECK(row["pinnable"] is True, "clean tag build pinnable: %s"
          % row["pinnable_checks"])
    CHECK(row["shared_tag"] == "2026.01.01" and row["version"] == "2026.01.01",
          "tag and version recorded")
    CHECK(row["shared_commit"] == git(img, "rev-parse", "HEAD"), "commit")
    CHECK(row["variant"] == "jammer" and row["builder"] == "gen-inhibit tester",
          "variant and builder")
    CHECK([f["offset"] for f in row["files"]] == ["0x0", "0x8000", "0xe000",
                                                 "0x10000"], "offsets")
    for f in row["files"] + [row["elf"]]:
        p = os.path.join(folder, f["name"])
        with open(p, "rb") as fh:
            b = fh.read()
        CHECK(os.path.isfile(p) and len(b) == f["size"]
              and hashlib.sha256(b).hexdigest() == f["sha256"]
              and hashlib.md5(b).hexdigest() == f["md5"],
              "archived %s matches its row" % f["name"])
    CHECK(row["image_hash"] == row["app_elf_sha256"], "R1 hash is the ELF's")
    CHECK(len(row["code_hash"]) == 64, "code hash")
    CHECK(os.path.isfile(os.path.join(folder, "README.md"))
          and os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(folder)),
                                          "README.md")), "READMEs written")
    rows, raw1 = manifest_rows(rit)
    CHECK(len(rows) == 1, "one row, not %d" % len(rows))

    # Again: appended, the first row's bytes untouched.
    row2, _ = archive.run(img, "jammer", "gen-inhibit tester",
                          build_fn=builder())
    rows, raw2 = manifest_rows(rit)
    CHECK(len(rows) == 2 and raw2.startswith(raw1), "second row appended, "
          "first unchanged")

    # The ruling's can-fail: a stop_a_only image offered as the jammer.
    pn = dict(names())
    pn["jammer"] = names()["stop_a_only"]
    img = repo()
    before = manifest_rows(rit)[1]
    msg = refused("stop_a_only image offered as jammer",
                  lambda: archive.run(img, "jammer", "t", build_fn=builder(pn)))
    CHECK(msg and "variant" in msg, "refusal names the variant: %s" % msg)
    CHECK(manifest_rows(rit)[1] == before, "no row for the refused build")

    # A test variant at a clean tag: archived, never pinnable.
    img = repo()
    row, _ = archive.run(img, "stop_a_only", "t", build_fn=builder())
    CHECK(row["pinnable"] is False
          and row["pinnable_checks"]["variant_pinnable"] is False
          and row["pinnable_checks"]["head_is_tag"] is True,
          "stop_a_only at a tag not pinnable: %s" % row["pinnable_checks"])

    # Not at a tag: a dev build.
    img = repo(tag=None)
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(row["pinnable"] is False and not row["pinnable_checks"]["head_is_tag"]
          and row["shared_tag"] is None, "untagged not pinnable")
    img = repo(tag="v1.0")
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(not row["pinnable_checks"]["head_is_tag"], "a non-shared tag")

    # Dirty before the configure (an untracked file): not pinnable, and the
    # version says -dirty? No: untracked files leave git describe clean, so
    # only the status catches it.
    img = repo()
    with open(os.path.join(img, "stray.txt"), "w") as f:
        f.write("x")
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(row["pinnable"] is False and not row["pinnable_checks"]["clean_before"]
          and row["pinnable_checks"]["version_is_tag"],
          "untracked file before: %s" % row["pinnable_checks"])

    # The build changes a tracked file.
    def touch_tracked(d):
        with open(os.path.join(d, "tracked.txt"), "a") as f:
            f.write("changed\n")
    img = repo()
    row, _ = archive.run(img, "jammer", "t",
                         build_fn=builder(side_effect=touch_tracked))
    c = row["pinnable_checks"]
    CHECK(row["pinnable"] is False and not c["clean_after"]
          and not c["no_tracked_change"] and c["clean_before"],
          "tracked file changed by the build: %s" % c)

    # A tracked file changed before: describe says -dirty, so the version is
    # not the tag either.
    img = repo()
    touch_tracked(img)
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    c = row["pinnable_checks"]
    CHECK(not c["no_tracked_change"] and not c["version_is_tag"]
          and not c["clean_before"], "tracked file changed before: %s" % c)

    # An ignored file outside build/ and managed_components/.
    img = repo()
    with open(os.path.join(img, ".gitignore"), "a") as f:
        f.write("/test/build/\n")
    git(os.path.dirname(img), "commit", "-qam", "ignore test/build")
    git(os.path.dirname(img), "tag", "-f", "2026.01.01")
    os.makedirs(os.path.join(img, "test", "build"))
    with open(os.path.join(img, "test", "build", "x"), "w") as f:
        f.write("x")
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(not row["pinnable_checks"]["clean_before"]
          and any("test/build" in l for l in row["unclean_before"]),
          "ignored file elsewhere: %s" % row["unclean_before"])

    # Ignored files under the image's own build/ and managed_components/ are
    # allowed (the build writes them).
    img = repo()
    os.makedirs(os.path.join(img, "managed_components", "vtrux__x2"))
    with open(os.path.join(img, "managed_components", "vtrux__x2", "a"), "w") as f:
        f.write("a")
    row, _ = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(row["pinnable"] is True, "managed_components allowed: %s"
          % row["unclean_before"])

    # Refusals: none of them adds a row.
    rows_before = len(manifest_rows(rit)[0])
    CHECK(rows_before == 10, "10 builds archived so far, not %d" % rows_before)
    img = repo()
    refused("build failure", lambda: archive.run(img, "jammer", "t",
                                                 build_fn=builder(rc=1)))
    refused("unknown variant", lambda: archive.run(img, "nope", "t",
                                                   build_fn=builder()))
    refused("no builder", lambda: archive.run(img, "jammer", "",
                                              build_fn=builder()))
    bad = dict(names())
    bad["jammer"] = "vtrux_jammer_x2_NOT_FOR_VEHICL"
    refused("unknown project name", lambda: archive.run(
        img, "jammer", "t", build_fn=builder(bad)))

    def forbidden_build(image_dir, variant):
        make_build(os.path.join(image_dir, "build", variant), names()[variant],
                   symbols=good_symbols(jammer_data(), ["twai_start"]))
        return 0, "stand-in"
    refused("check_image failure", lambda: archive.run(
        img, "jammer", "t", build_fn=forbidden_build))
    saved = os.environ.pop("REVERSE_IT_DIR")
    msg = refused("REVERSE_IT_DIR unset", lambda: archive.run(
        img, "jammer", "t", build_fn=builder()))
    CHECK(msg and "REVERSE_IT_DIR is not set" in msg,
          "the refusal names the unset variable: %s" % msg)
    os.environ["REVERSE_IT_DIR"] = os.path.join(tmp, "nowhere")
    refused("REVERSE_IT_DIR without artifacts/", lambda: archive.run(
        img, "jammer", "t", build_fn=builder()))
    inside = os.path.join(os.path.dirname(img), "rit_inside")
    os.makedirs(os.path.join(inside, archive.ARCHIVE_REL))
    os.environ["REVERSE_IT_DIR"] = inside
    refused("archive inside the git tree", lambda: archive.run(
        img, "jammer", "t", build_fn=builder()))
    os.environ["REVERSE_IT_DIR"] = saved
    CHECK(len(manifest_rows(rit)[0]) == rows_before, "a refused build added a "
          "row (%d, was %d)" % (len(manifest_rows(rit)[0]), rows_before))

    # An existing build folder with different files is refused.
    img = repo()
    row, folder = archive.run(img, "jammer", "t", build_fn=builder())
    with open(os.path.join(folder, "ota_data_initial.bin"), "wb") as f:
        f.write(b"tampered")
    refused("existing folder differs", lambda: archive.run(
        img, "jammer", "t", build_fn=builder()))


# ---- flash_row ----

FAKE_ESPTOOL = r'''
import os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_ESPTOOL_LOG"], "a") as f:
    f.write(" ".join(a) + "\n")
if "chip_id" in a:
    print("Chip is ESP32-S3 (QFN56) (revision v0.2)")
    print("MAC: 02:00:00:00:00:01")
    sys.exit(0)
if "write_flash" in a:
    n = len(a[a.index("write_flash") + 1:]) // 2
    if os.environ.get("FAKE_ESPTOOL_MODE") == "short":
        n -= 1
    for _ in range(n):
        print("Hash of data verified.")
    sys.exit(0)
sys.exit(2)
'''
FAKE_CANCTL = r'''
import os, shutil, subprocess, sys
with open(os.environ["FAKE_CANCTL_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_CANCTL_MODE", "ok")
net = os.path.join(os.environ["FAKE_NET"], "canx2")
slcand = os.path.join(os.environ["FAKE_PROC"], "4242")
if mode == "fail":
    print("canctl: refused")
    sys.exit(2)
if mode in ("ok", "late") and os.path.lexists(net):
    os.remove(net)
if mode == "ok" and os.path.isdir(slcand):
    shutil.rmtree(slcand)
if mode == "late" and os.path.isdir(slcand):
    subprocess.Popen([sys.executable, "-c",
                      "import shutil, sys, time; time.sleep(0.4); "
                      "shutil.rmtree(sys.argv[1])", slcand],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
sys.exit(0)
'''
FLASH_ROLE = "tester (charge-interposer)"
TEST_MAC = "02:00:00:00:00:01"      # locally administered, made up


def test_flash_row(tmp):
    import flash_row as FR
    rit = os.path.join(tmp, "frit")
    os.makedirs(os.path.join(rit, archive.ARCHIVE_REL))
    os.environ["REVERSE_IT_DIR"] = rit
    img = make_repo(os.path.join(tmp, "fr-repo"))
    row, folder = archive.run(img, "jammer", "t", build_fn=builder())
    CHECK(row["pinnable"], "flash_row's stand-in row is pinnable")
    arch = os.path.dirname(os.path.dirname(folder))
    build = row["app_sha256"][:16]

    fake = os.path.join(tmp, "fake_esptool.py")
    with open(fake, "w") as f:
        f.write(FAKE_ESPTOOL)
    elog = os.path.join(tmp, "esptool_calls.txt")
    os.environ["FAKE_ESPTOOL_LOG"] = elog
    settings = {"esptool": {"argv": [sys.executable, fake], "port": "FAKEPORT",
                            "baud": 115200, "chip": {"mac": TEST_MAC}}}
    lockdir = os.path.join(tmp, "lock")
    os.makedirs(lockdir)
    lock = os.path.join(lockdir, "bench-log.jsonl")
    FR.BL.init(lock, FLASH_ROLE)
    FR.BL.take(lock, FLASH_ROLE, os.getpid())
    other = os.path.join(tmp, "lock2")
    os.makedirs(other)
    lock2 = os.path.join(other, "bench-log.jsonl")
    FR.BL.init(lock2, "android")
    FR.BL.take(lock2, "android", os.getpid())
    net, proc = os.path.join(tmp, "net"), os.path.join(tmp, "proc")
    os.makedirs(net)
    os.makedirs(proc)
    saved = (FR.FL.this_env, FR.SYSFS_NET, FR.PROC, FR.BENCH, FR.CHECKOUT,
             FR.CANCTL, FR.DETACH_WAIT_S, FR.POLL_S)
    FR.FL.this_env = lambda: "windows"   # the flasher takes a port setting
    FR.SYSFS_NET, FR.PROC = net, proc    # never this machine's canx2
    k = [0]

    def logdir():
        k[0] += 1
        d = os.path.join(tmp, "flogs", str(k[0]))
        os.makedirs(d)
        return d

    def calls():
        if not os.path.exists(elog):
            return []
        with open(elog) as f:
            out = f.read().splitlines()
        os.remove(elog)
        return out

    def copy(edit_rows=None, edit_files=None):
        """A copy of the archive, its rows and files edited."""
        k[0] += 1
        d = os.path.join(tmp, "farch", str(k[0]))
        shutil.copytree(arch, d)
        if edit_rows:
            with open(os.path.join(d, "manifest.jsonl")) as f:
                rows = [json.loads(l) for l in f if l.strip()]
            rows = edit_rows(rows)
            with open(os.path.join(d, "manifest.jsonl"), "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
        if edit_files:
            edit_files(os.path.join(d, row["folder"]))
        return d

    def go(a=None, settings_=None, build_=None, lock_=None, dev=False,
           check_only=False):
        return FR.run("jammer", build_ or build, settings_ or settings,
                      FLASH_ROLE, logdir(), dev=dev, check_only=check_only,
                      archive_dir=a or arch, lock_log=lock_ or lock)

    def refuses(what, rc_res, needle):
        rc, res = rc_res
        detail = res.get("detail") or ""
        CHECK(rc == 2 and res["phase"] == "refused" and needle in detail,
              "%s refused naming %r: rc %s, %s" % (what, needle, rc, detail))
        CHECK(calls() == [], "%s: esptool never ran" % what)

    try:
        CHECK(FR.chip_type("esp32s3") == "ESP32-S3"
              and FR.chip_type("esp32c3") == "ESP32-C3"
              and FR.chip_type("esp32") == "ESP32", "chip names")

        # The good row: checked, then flashed with exactly its files.
        rc, res = go(check_only=True)
        CHECK(rc == 0 and res["phase"] == "checked" and calls() == [],
              "check-only passes and runs no esptool: %s" % res.get("detail"))
        CHECK([f[0] for f in res["files"]] == ["0x0", "0x8000", "0xe000",
                                               "0x10000"], "the row's offsets")
        rc, res = go()
        c = calls()
        want = " ".join("%s %s" % (f["offset"], os.path.join(folder, f["name"]))
                        for f in row["files"])
        CHECK(rc == 0 and res["ok"], "the good row flashes: %s"
              % res.get("detail"))
        CHECK(len(c) == 3 and c[1].endswith("write_flash " + want),
              "write_flash got exactly the row's files at their offsets: %s"
              % c[1:2])
        CHECK(row["app_sha256"] in res["note"]
              and "shared tag 2026.01.01" in res["note"]
              and row["folder"] in res["note"], "note: %s" % res.get("note"))
        CHECK(res["flash"]["md5"] and "device" not in res["flash"],
              "the flasher's result kept, without the device")

        # The files differ from the row (T9).
        def grow(d):
            with open(os.path.join(d, "ota_data_initial.bin"), "ab") as f:
                f.write(b"\xff")
        refuses("a file one byte longer", go(copy(edit_files=grow)), "size")

        def flip(d):
            p = os.path.join(d, "bootloader.bin")
            with open(p, "rb") as f:
                b = bytearray(f.read())
            b[5] ^= 1
            with open(p, "wb") as f:
                f.write(b)
        refuses("a file with a byte changed", go(copy(edit_files=flip)),
                "sha256")

        def row_field(name, key, value):
            def ed(rows):
                for r in rows:
                    for f in r["files"]:
                        if f["name"] == name:
                            f[key] = value
                return rows
            return ed
        refuses("a row sha256 the file does not have", go(copy(
            row_field("bootloader.bin", "sha256", "0" * 64))), "sha256")
        refuses("a row md5 the file does not have", go(copy(
            row_field("bootloader.bin", "md5", "0" * 32))), "md5")
        refuses("overlapping offsets", go(copy(
            row_field("ota_data_initial.bin", "offset", "0x8010"))), "overlaps")

        def dotdot(d):
            shutil.copy2(os.path.join(d, "ota_data_initial.bin"),
                         os.path.dirname(d))
        refuses("a file name with a path", go(copy(
            row_field("ota_data_initial.bin", "name",
                      "../ota_data_initial.bin"), dotdot)), "bare name")

        def set_key(key, value):
            def ed(rows):
                for r in rows:
                    r[key] = value
                return rows
            return ed
        refuses("an app_offset at another file", go(copy(
            set_key("app_offset", "0xe000"))), "app_sha256")
        refuses("a code hash the app does not have", go(copy(
            set_key("code_hash", "1" * 64))), "code_hash")
        refuses("an image hash the app does not have", go(copy(
            set_key("image_hash", "2" * 64))), "image_hash")

        def other_folder(d):
            shutil.copytree(d, os.path.join(os.path.dirname(d), "0" * 16))
        refuses("a folder not named by the app sha", go(copy(
            set_key("folder", "builds/" + "0" * 16), other_folder)), "folder")

        # The row: not pinnable, ambiguous, absent.
        a = copy(set_key("pinnable", False))
        refuses("a dev build without --dev", go(a), "pinnable")
        rc, res = go(a, dev=True, check_only=True)
        CHECK(rc == 0 and res["pinnable"] is False, "--dev checks a dev build")

        def two(rows):
            bad = json.loads(json.dumps(rows[0]))
            bad["files"][0]["md5"] = "0" * 32
            return [bad] + rows
        refuses("rows naming different files", go(copy(two)), "disagree")
        refuses("no row", go(build_="f" * 16), "no manifest row")
        refuses("a short build id", go(build_=build[:8]), "16 to 64")

        # The chip, the lock, canx2.
        refuses("no MAC", go(settings_={"esptool": dict(
            settings["esptool"], chip={})}), "MAC")
        refuses("a settings chip type for another chip", go(settings_={
            "esptool": dict(settings["esptool"],
                            chip={"type": "ESP32-C3", "mac": TEST_MAC})}),
            "ESP32-C3")
        refuses("the lock held by another role", go(lock_=lock2), "not held by")

        # canx2: detached through canctl (a stand-in acting on the made-up
        # trees), polled until gone, recorded; refused if it stays.
        def attach(netdev=True, slcand=True):
            if netdev:
                os.symlink("/nonexistent", os.path.join(net, "canx2"))
            if slcand:
                os.makedirs(os.path.join(proc, "4242"))
                with open(os.path.join(proc, "4242", "cmdline"), "wb") as f:
                    f.write(b"/usr/bin/slcand\0-o\0-s6\0ttyACM9\0canx2\0")

        def detach_all():
            if os.path.lexists(os.path.join(net, "canx2")):
                os.remove(os.path.join(net, "canx2"))
            if os.path.isdir(os.path.join(proc, "4242")):
                shutil.rmtree(os.path.join(proc, "4242"))

        def canctl_calls():
            if not os.path.exists(clog):
                return []
            with open(clog) as f:
                out = f.read().splitlines()
            os.remove(clog)
            return out

        def detaches(what, mode, netdev=True, slcand=True):
            attach(netdev, slcand)
            os.environ["FAKE_CANCTL_MODE"] = mode
            d = logdir()
            rc, res = FR.run("jammer", build, settings, FLASH_ROLE, d,
                             archive_dir=arch, lock_log=lock)
            c, cc = calls(), canctl_calls()
            CHECK(rc == 0 and res["ok"] and len(c) == 3
                  and cc == ["slcan detach canx2"]
                  and res["canx2"]["after"] == "not attached"
                  and res["canx2"]["canctl"]["rc"] == 0
                  and res["canx2"]["before"] != "not attached"
                  and os.path.isfile(os.path.join(d, "canx2_detach.txt")),
                  "%s: detached, then flashed: rc %s, %s, canctl %s"
                  % (what, rc, res.get("canx2"), cc))
            detach_all()

        def stays(what, mode, needle):
            attach()
            os.environ["FAKE_CANCTL_MODE"] = mode
            rc, res = go()
            cc = canctl_calls()
            CHECK(rc == 2 and needle in (res.get("detail") or "")
                  and cc == ["slcan detach canx2"]
                  and res["canx2"]["after"] != "not attached",
                  "%s refused naming %r: rc %s, %s, %s"
                  % (what, needle, rc, res.get("detail"), res.get("canx2")))
            CHECK(calls() == [], "%s: esptool never ran" % what)
            detach_all()

        fake_canctl = os.path.join(tmp, "fake_canctl.py")
        with open(fake_canctl, "w") as f:
            f.write(FAKE_CANCTL)
        clog = os.path.join(tmp, "canctl_calls.txt")
        os.environ.update(FAKE_CANCTL_LOG=clog, FAKE_NET=net, FAKE_PROC=proc)
        FR.CANCTL = [sys.executable, fake_canctl]
        FR.DETACH_WAIT_S, FR.POLL_S = 3.0, 0.05
        rc, res = go()
        calls()
        CHECK(rc == 0 and canctl_calls() == []
              and res["canx2"] == {"before": "not attached", "canctl": None,
                                   "after": "not attached"},
              "not attached: no canctl, recorded: %s" % res.get("canx2"))
        detaches("netdev and slcand", "ok")
        detaches("netdev alone", "ok", slcand=False)
        detaches("slcand alone", "ok", netdev=False)
        detaches("slcand exits after canctl returns", "late")
        stays("canctl exiting 2", "fail", "exited 2")
        stays("canctl exiting 0 with canx2 left", "leave", "still attached")
        os.environ.pop("FAKE_CANCTL_MODE", None)

        # The descriptor's project name and chip.
        refuses("a project name the app does not have", go(copy(
            set_key("project_name", "vtrux_jammer_x2_NOT_FOR_VEHICLX"))),
            "project_name")
        try:
            FR.check_files(row, arch, "esp32c3")
            CHECK(False, "an app for another chip than x2_image.json's passes")
        except FR.Refused as e:
            CHECK("chip" in str(e), "another chip refused naming chip: %s" % e)

        # Another vtrux_bench than this checkout's.
        FR.BENCH = tmp
        refuses("a foreign vtrux_bench", go(), "imported from")
        FR.BENCH = saved[3]

        # The flash itself fails: exit 1.
        os.environ["FAKE_ESPTOOL_MODE"] = "short"
        rc, res = go()
        calls()
        CHECK(rc == 1 and not res["ok"] and "verified" in res["detail"],
              "a write not verified for every file: rc %s, %s"
              % (rc, res.get("detail")))
        os.environ.pop("FAKE_ESPTOOL_MODE")

        # The command line.
        sp = os.path.join(tmp, "fsettings.json")
        with open(sp, "w") as f:
            json.dump(settings, f)
        base = ["jammer", build, "--settings", sp, "--role", FLASH_ROLE,
                "--archive", arch, "--lock-log", lock]
        FR.CHECKOUT = os.path.join(tmp, "checkout")  # stands in for the tree
        os.makedirs(FR.CHECKOUT)
        inside = os.path.join(FR.CHECKOUT, "logs")
        with redirect_stdout(io.StringIO()):
            rc0 = FR.main(base + ["--log-dir", os.path.join(tmp, "cli1"),
                                  "--check-only"])
            rc2 = FR.main(base + ["--log-dir", os.path.join(tmp, "cli1")])
            rc2b = FR.main(base + ["--log-dir", inside, "--check-only"])
        CHECK((rc0, rc2, rc2b) == (0, 2, 2) and not os.path.exists(inside),
              "cli: check-only 0, existing log dir 2, log dir in the "
              "checkout 2: %r" % ((rc0, rc2, rc2b),))
        CHECK(os.path.isfile(os.path.join(tmp, "cli1", "flash_row.json")),
              "cli writes flash_row.json")

        # --check-only needs no --settings or --role; a flash needs both.
        bare = ["jammer", build, "--archive", arch]
        outs = []
        for args in (bare + ["--check-only", "--log-dir",
                             os.path.join(tmp, "cli2")],
                     bare + ["--role", FLASH_ROLE, "--lock-log", lock,
                             "--log-dir", os.path.join(tmp, "cli3")],
                     bare + ["--settings", sp, "--lock-log", lock,
                             "--log-dir", os.path.join(tmp, "cli4")]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                outs.append((FR.main(args), buf.getvalue()))
        CHECK(outs[0][0] == 0 and outs[0][1].startswith("CHECKED"),
              "check-only without settings or role: %r" % (outs[0],))
        CHECK(outs[1][0] == 2 and "--settings required to flash" in outs[1][1]
              and calls() == [], "a flash without --settings: %r" % (outs[1],))
        CHECK(outs[2][0] == 2 and "--role required to flash" in outs[2][1]
              and calls() == [], "a flash without --role: %r" % (outs[2],))
    finally:
        (FR.FL.this_env, FR.SYSFS_NET, FR.PROC, FR.BENCH, FR.CHECKOUT,
         FR.CANCTL, FR.DETACH_WAIT_S, FR.POLL_S) = saved
        for v in ("FAKE_ESPTOOL_LOG", "FAKE_ESPTOOL_MODE", "FAKE_CANCTL_LOG",
                  "FAKE_CANCTL_MODE", "FAKE_NET", "FAKE_PROC"):
            os.environ.pop(v, None)


def _onerror(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError as e:
        print("could not remove %s: %s" % (path, e))


def main():
    top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True,
                         check=True).stdout.strip()
    tmp = os.path.join(top, "scratch", "x2-tools-tests", str(os.getpid()))
    if subprocess.run(["git", "-C", top, "check-ignore", "-q", tmp]).returncode:
        print("REFUSED: %s is not ignored by git" % tmp)
        return 1
    saved = os.environ.get("REVERSE_IT_DIR")
    try:
        os.makedirs(tmp)
        test_check_image(tmp)
        test_archive(tmp)
        test_flash_row(tmp)
    finally:
        if saved is None:
            os.environ.pop("REVERSE_IT_DIR", None)
        else:
            os.environ["REVERSE_IT_DIR"] = saved
        shutil.rmtree(tmp, onerror=_onerror)
        if os.path.exists(tmp):
            print("could not remove %s" % tmp)
        try:
            os.rmdir(os.path.dirname(tmp))
        except OSError:
            pass
    if FAILED:
        print("test_tools: %d of %d checks FAILED" % (len(FAILED), COUNT[0]))
        return 1
    print("test_tools: ALL PASS (%d checks)" % COUNT[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
