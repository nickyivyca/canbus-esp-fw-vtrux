"""Build every PlatformIO environment, and check the truck image is silent.

Review item C2b. On 2026-09-29 `slcan_main.cpp` was added without being
excluded from the other environments' `build_src_filter`, so
`[env:esp32-can-x2]` -- the image that goes in the truck -- failed to link
with "multiple definition of setup()", and `selftest` and `diag` with it. It
stayed broken until 2026-10-03. Nothing noticed because nothing built every
environment: the bench only ever built what it was about to flash, and what
it was about to flash was never the truck build.

Two checks, and the second needs the first:

1. **every environment links.** The list comes from `platformio.ini`, not
   from a list here, so an environment added tomorrow is covered without
   anyone remembering to add it. That is the whole defect: the exclusion
   lists are per-environment and adding a `*_main.cpp` means editing all of
   them, which is exactly the kind of edit that gets missed.

2. **the truck image carries no serial output** (spec 8.3). Every print in
   `main.cpp` runs inside the bridge loop, and a USB host attached but not
   reading can block the CDC write and stall bridging with it; `[env:truck]`
   compiles the calls out with `-DINTP_NO_SERIAL` rather than guarding them
   at run time, so there is nothing left to block on. Checked by looking for
   the format strings themselves in the image: if the calls are compiled out,
   their literals are not in `.rodata`.

   **With a positive control**, because "I did not find the string" is the
   one result a broken search also gives. The same strings must be PRESENT
   in the bench image. A typo in a pattern, a changed message, or a search
   pointed at the wrong file then fails loudly instead of passing twice.

Usage (from the repo root):
    py -3.11 projects/vtrux/tools/interposer/firmware/test/build_check.py
    ... --no-build   check images already in .pio/build
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

# REFUSE TO RUN UNDER PYTHON < 3.10, before doing anything else.
#
# build() below invokes `sys.executable -m platformio`, so this script
# builds with whatever interpreter started it. Under 3.9 the C++
# compiles cleanly and then bootloader.bin dies with
# `TypeError: unsupported operand type(s) for |`, because esptool 5.0.2
# annotates `str | None` (PEP 604, 3.10+). The failure is deep in a
# build log and looks like a firmware problem; on 2026-10-04 it was
# read as a broken toolchain and very nearly became a request to
# reinstall PlatformIO. Nothing was wrong except the interpreter.
#
# `tools-reference.md` says this already, in the PlatformIO table for
# NICKY-XPS: a 6.1.18 under Python 3.9 is first on PATH as `pio` and
# must not be used; invoke `py -3.14 -m platformio` from PowerShell.
# This turns ignoring that into a refusal on line one instead of a
# confusing failure ten minutes into a build.
if sys.version_info < (3, 10):
    raise SystemExit(chr(10).join([
        "build_check must run under Python 3.10 or newer (this is %d.%d)."
        % sys.version_info[:2],
        "It builds with the interpreter that starts it, and esptool 5.0.2 "
        "needs 3.10+.",
        "On NICKY-XPS:  py -3.14 test/build_check.py   (from PowerShell)",
        "Do not use the bare `pio` on PATH -- see the PlatformIO table in "
        "tools-reference.md.",
    ]))

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.normpath(os.path.join(HERE, ".."))

# The environment whose image goes in the truck, and the one it is otherwise
# identical to. Spec 8.3 and the [env:truck] comment in platformio.ini.
TRUCK_ENV = "truck"
BENCH_ENV = "esp32-can-x2"

# Literals that exist only on a serial path in main.cpp. Each must be absent
# from the truck image and present in the bench image.
SERIAL_MARKERS = (
    b"Vtrux charge interposer",      # the banner, setup()
    b"diag st=",                     # the 1 Hz diag line, spec 8.3
    b"BRIDGE DOWN",                  # the failed-start message
    b"CAN1 (vehicle, TWAI)",         # controller bring-up
    b"  LOSS veh(rx=",               # the loss line in status()
)


def environments():
    """Every [env:NAME] in platformio.ini, in file order."""
    with open(os.path.join(FW, "platformio.ini"), "r", encoding="utf-8") as f:
        return re.findall(r"^\[env:([^\]]+)\]", f.read(), re.M)


# The tag table is IMPORTED from the firmware directory, never copied:
# build_name.py sets the filenames from the same table, and a second
# copy here would drift into a check that agrees with a stale idea of
# the truth rather than with the build.
sys.path.insert(0, FW)
import build_identity            # noqa: E402


def image(env):
    """The one tagged image `env` produced, or None.

    No longer `firmware.bin`: every environment now writes
    interposer_<srcdigest>_<tag>.bin so that six images cannot be told
    apart only by their parent directory. More than one match is
    itself a problem -- a stale image from an earlier source digest
    sitting beside the current one is exactly how the wrong file gets
    flashed -- so this returns None rather than guessing.
    """
    hits = glob.glob(os.path.join(FW, ".pio", "build", env,
                                  build_identity.name_glob(env)))
    return hits[0] if len(hits) == 1 else None


def images_in(env):
    return sorted(glob.glob(os.path.join(FW, ".pio", "build", env,
                                         build_identity.PREFIX + "*.bin")))


def build(envs):
    """Clean, then build, every environment.

    CLEAN FIRST (tester, 2026-10-07). `platformio run` skips an environment
    it considers current, so on 2026-10-07 the witness image checked here
    was one the implementor had built four minutes earlier: five of six
    environments compiled in the run and the sixth was REUSED, and every
    check below passed it identically, because a reused image satisfies an
    on-disk check exactly as a fresh one does. Spec 9 C2b is that every
    environment BUILDS, so each is cleaned and compiled, and
    check_images_fresh() then requires every image to postdate the build's
    start.
    """
    base = [sys.executable, "-m", "platformio", "run", "-d", FW]
    for e in envs:
        base += ["-e", e]
    print("cleaning %d environment(s)" % len(envs))
    r = subprocess.run(base + ["-t", "clean"], capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).splitlines()[-25:]
        return ["platformio clean failed (exit %d):\n    %s"
                % (r.returncode, "\n    ".join(tail))]
    cmd = base
    print("building %d environment(s): %s" % (len(envs), ", ".join(envs)))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).splitlines()[-25:]
        return ["platformio run failed (exit %d):\n    %s"
                % (r.returncode, "\n    ".join(tail))]
    # A per-environment verdict, so a single failure names itself rather than
    # leaving the reader to find it in the log.
    bad = re.findall(r"^(\S+)\s+(FAILED|ERROR)\s", r.stdout, re.M)
    return ["environment %s: %s" % (n, s) for n, s in bad]


def check_images_fresh(envs, t0):
    """Every environment's image was written by THIS build (after `t0`).

    The check that tells "compiled now" from "already there". Without it a
    skipped or failed environment whose old image is still on disk passes
    every other check here -- which is how the witness environment went
    uncompiled in a C2b run on 2026-10-07 with nothing in the output to
    say so.
    """
    problems = []
    for e in envs:
        p_img = image(e)
        if p_img is None:
            continue           # already reported by check_images_exist
        if os.path.getmtime(p_img) < t0:
            problems.append(
                "%s predates this build (%s) -- [env:%s] was not compiled "
                "in this run, so it is not evidence that it builds"
                % (os.path.basename(p_img),
                   time.strftime("%H:%M:%S",
                                 time.localtime(os.path.getmtime(p_img))),
                   e))
    if not problems:
        print("fresh: all %d images written by this build" % len(envs))
    return problems


# Diagnostic instrumentation that must never be compiled into ANY firmware
# image: the macro that gates it, and the header it is declared in.
DIAG_GATES = (
    ("INTP_TX_TRACE", "src/port_mcp2515.h"),
    ("INTP_ORDER_WITNESS", "src/port_mcp2515.h"),
)

# The ONE environment allowed to define a diagnostic gate, named exactly.
WITNESS_ENV = "esp32-can-x2-witness"
WITNESS_MACRO = "INTP_ORDER_WITNESS"
# Key under which _env_blocks collects everything that is NOT an
# [env:...] section. Angle brackets cannot appear in a section name,
# so it can never collide with a real environment.
NON_ENV = "<not an environment>"


def _env_blocks(ini_text):
    """platformio.ini split into {env name: its own text}.

    [env:NAME] sections are keyed by NAME. Everything else -- [common],
    the file preamble, any section added later -- goes under NON_ENV,
    so a gate defined there is caught rather than ignored: every
    environment inherits [common].

    A flag reached through `extends` is NOT resolved. It does not need
    to be: `${common.build_flags}` is literal text, and a gate added to
    [common] lands in the NON_ENV bucket above.
    """
    blocks = {}
    name = None
    for line in ini_text.splitlines():
        # COMMENTS ARE NOT FLAGS. The prose above each section explains
        # what that environment does and freely names the macros --
        # including the ones this check forbids. Counting those made the
        # first version of this check report INTP_TX_TRACE "in
        # [env:slcan]" and the witness env as a no-serial build, both
        # false, because a section's preceding comment block lands in
        # the PREVIOUS section when scanning line by line.
        if line.lstrip().startswith(";"):
            continue
        m = re.match(r"^\[([^\]]+)\]\s*$", line)
        if m:
            sec = m.group(1)
            # Everything that is not an [env:...] section -- [common],
            # the preamble, anything added later -- is collected under
            # one key, because every environment inherits [common] and a
            # gate defined there would reach all of them.
            name = sec[4:] if sec.startswith("env:") else NON_ENV
            blocks.setdefault(name, [])
            continue
        if name is None:
            name = NON_ENV            # preamble, before any section
            blocks.setdefault(name, [])
        blocks[name].append(line)
    return dict((k, chr(10).join(v)) for k, v in blocks.items())


# The literal the witness image prints at boot and on every dump
# header. Behind INTP_ORDER_WITNESS, so it is in that image and no
# other -- which is what makes a static image check possible at all.
WITNESS_MARKER = b"WITNESS BUILD (env esp32-can-x2-witness)"


def check_images_exist(envs):
    """Every environment left exactly one correctly tagged image.

    "platformio run exited 0" is not the same as "an image exists", so
    this asserts the artefact rather than the exit code.

    It also asserts the NAME. Every environment used to write
    `firmware.bin`, so six images differed only by their parent
    directory -- the same trap the generator-inhibit project fixed by
    tagging its auto-arm build, and a worse one here because a board
    carrying measurement instrumentation must not be confusable with
    the bench bridge. `build_name.py` names them
    interposer_<srcdigest>_<tag>.bin from the table in
    build_identity.py, which this imports rather than copies.
    """
    problems = []
    digests = {}
    for e in envs:
        found = images_in(e)
        try:
            want = build_identity.tag_for(e)
        except KeyError as exc:
            problems.append(str(exc))
            continue
        if not found:
            problems.append(
                "environment %s produced no %s -- the build reported no "
                "error but there is no image to flash"
                % (e, build_identity.name_glob(e)))
            continue
        if len(found) > 1:
            # A stale image from an earlier source digest sitting beside
            # the current one is exactly how the wrong file gets flashed.
            problems.append(
                "environment %s has %d images (%s) -- cannot say which is "
                "current; clean the build directory"
                % (e, len(found), ", ".join(os.path.basename(f)
                                            for f in found)))
            continue
        base = os.path.basename(found[0])
        if not base.endswith("_%s.bin" % want):
            problems.append(
                "environment %s produced %s, which is not tagged %r"
                % (e, base, want))
            continue
        digests[e] = build_identity.digest_of(base)

        # Only the witness environment may produce a _witness image.
        marked = base.endswith("_%s.bin" % build_identity.WITNESS_TAG)
        if marked != (e == WITNESS_ENV):
            problems.append(
                "environment %s produced %s: the %r tag must appear on "
                "exactly one image, from [env:%s]"
                % (e, base, build_identity.WITNESS_TAG, WITNESS_ENV))

    # All environments build the SAME sources, so every image name must
    # carry the same digest. A mismatch means part of the tree was built
    # at one revision and part at another -- which would make a manifest
    # lookup name the wrong sources.
    if len(set(digests.values())) > 1:
        problems.append(
            "images carry different source digests (%s) -- not all "
            "environments were built from the same tree"
            % ", ".join("%s=%s" % (k, v) for k, v in sorted(digests.items())))

    # ... AND THE TREE ITSELF, added 2026-10-05.
    #
    # The comparison above proves the six images agree WITH EACH OTHER.
    # It does not prove any of them agrees with the sources on disk: six
    # images all built from the same older tree pass it unanimously. That
    # is the blind spot the generator-inhibit session asked about, and it
    # is the same shape as the defect it found in its own repo -- a
    # value that can only be compared against itself.
    #
    # manifest.py re-checks the digest after each link, but a post-action
    # runs only when the image is actually linked, so a `pio run` with
    # nothing to do verifies nothing. THIS is the check for images
    # already on disk, which is exactly what --no-build inspects.
    tree = build_identity.source_digest(FW)[:build_identity.DIGEST_CHARS]
    stale = sorted(e for e, d in digests.items() if d != tree)
    if stale:
        problems.append(
            "image(s) built from a different tree than the one on disk: %s "
            "-- the sources now hash to %s. Rebuild before flashing or "
            "quoting a digest."
            % (", ".join("%s=%s" % (e, digests[e]) for e in stale), tree))

    if not problems:
        print("images: %d environments, each one correctly tagged image, "
              "source digest %s"
              % (len(envs), sorted(set(digests.values()))[0] if digests
                 else "?"))
    return problems


def check_manifest(envs):
    """builds/manifest.json covers this build and has no ambiguous id.

    The board reports only the FIRST FOUR BYTES of the ELF SHA-256 in
    0x7F7, so two images sharing that prefix would make the lookup name
    the wrong one -- and name it confidently. A 32-bit collision is
    unlikely and is also the single way this table can lie, so it is
    checked rather than assumed.

    Also asserts the manifest actually describes the images that exist.
    A manifest that silently lags the build directory would be worse
    than none: an arm would read a build id off the wire, find a row,
    and trust a description of a different binary.
    """
    problems = []
    path = os.path.join(FW, "builds", "manifest.json")
    if not os.path.exists(path):
        return ["no builds/manifest.json -- it is written by "
                "firmware/manifest.py on every build, so its absence means "
                "the post-action did not run"]
    try:
        with open(path, "r") as f:
            entries = json.load(f).get("builds", [])
    except (IOError, ValueError) as exc:
        return ["builds/manifest.json is unreadable: %s" % exc]

    by_prefix = {}
    for e in entries:
        full = e.get("elf_sha256")
        img = e.get("image", "?")
        if not full:
            problems.append(
                "manifest entry %s has no elf_sha256 -- esptool image-info "
                "did not report one, so that image cannot be identified "
                "from 0x7F7" % img)
            continue
        pre = full[:8]
        if pre in by_prefix and by_prefix[pre][1] != full:
            problems.append(
                "0x7F7 build-id COLLISION: %s and %s both report %s but are "
                "different images -- the lookup would name the wrong one"
                % (by_prefix[pre][0], img, pre))
        by_prefix.setdefault(pre, (img, full))

    # Every image on disk must have a row -- for ITS bytes, not just its
    # name (tester, 2026-10-07). The filename comes from the source digest,
    # and two clean rebuilds of one digest gave two different ELFs under the
    # same name, so a name match can describe a different binary. Rows are
    # matched on (image, elf_sha256), the ELF read out of the image itself.
    # Several rows per image name are fine; none for these bytes is not.
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..")))
    import build_lookup as BL
    for e in envs:
        p_img = image(e)
        if p_img is None:
            continue           # already reported by check_images_exist
        base = os.path.basename(p_img)
        elf = BL.elf_sha256_of_bin(p_img)
        if elf is None:
            problems.append(
                "%s carries no readable application descriptor, so its ELF "
                "hash -- the identity 0x7F7 reports -- is unknown" % base)
            continue
        if not any(x.get("image") == base
                   and (x.get("elf_sha256") or "").lower() == elf
                   for x in entries):
            named = [(x.get("elf_sha256") or "?")[:16] for x in entries
                     if x.get("image") == base]
            problems.append(
                "%s on disk is ELF %s, and no manifest row records that "
                "image with those bytes (rows under that name: %s) -- the "
                "manifest describes a different binary"
                % (base, elf[:16], ", ".join(named) or "none"))

    if not problems:
        print("manifest: %d entries, %d distinct 0x7F7 build ids, no "
              "collisions" % (len(entries), len(by_prefix)))
    return problems


def check_witness_marker(envs):
    """The WITNESS BUILD banner is in the witness image and nowhere else.

    A static check on the IMAGES, which is stronger than checking the
    build flag: it is evidence about the bytes that would be flashed,
    not about the configuration that was meant to produce them.

    This became possible only once the witness image announced itself.
    An earlier version of the gate check argued the images could not be
    checked "because neither gate adds a string literal" -- true then,
    false as soon as the banner existed, and worth saying because the
    reasoning outlived the fact it rested on.

    The positive control is built in and cannot be skipped: the marker
    must be PRESENT in the witness image. If it is renamed or compiled
    out, that fails rather than every absence check passing vacuously.
    """
    problems = []
    wit = image(WITNESS_ENV)
    if wit is None:
        return ["no single image for %s, so the witness marker check "
                "cannot run" % WITNESS_ENV]
    with open(wit, "rb") as f:
        if WITNESS_MARKER not in f.read():
            problems.append(
                "%r is NOT in the witness image %s -- the marker this "
                "check looks for no longer exists, so its absence from "
                "the other images proves nothing"
                % (WITNESS_MARKER.decode(), os.path.basename(wit)))
            return problems

    checked = 0
    for e in envs:
        if e == WITNESS_ENV:
            continue
        p = image(e)
        if p is None:
            continue           # already reported by check_images_exist
        with open(p, "rb") as f:
            if WITNESS_MARKER in f.read():
                problems.append(
                    "%r is in the %s image (%s): instrumentation reached a "
                    "non-witness build" % (WITNESS_MARKER.decode(), e,
                                           os.path.basename(p)))
        checked += 1
    if not problems:
        print("witness marker: present in %s, absent from the other %d "
              "image(s)" % (WITNESS_ENV, checked))
    return problems


def check_diagnostics_are_gated():
    """Only WITNESS_ENV may define a diagnostic gate.

    The stall trace and the completion-order witness are compiled only
    when their macro is defined. One bench environment is allowed to
    define the witness, because dumping it on the board is what it is
    for; nothing else may define either gate, and the truck image may
    not define anything.

    The failure mode is silent: an image carrying the witness would work
    perfectly and merely be several KB larger, with a measurement
    apparatus riding along in the vehicle. Searching the IMAGES does not
    work -- neither gate adds a string literal and the .bin is stripped
    -- so the build flag is what gets checked, since the build flag is
    the mechanism.
    """
    problems = []
    ini = os.path.join(FW, "platformio.ini")
    try:
        with open(ini, "r") as f:
            ini_text = f.read()
    except IOError as e:
        return ["cannot read platformio.ini: %s" % e]

    blocks = _env_blocks(ini_text)

    # The whitelist must name an environment that EXISTS. Renaming the
    # witness env without updating this list fails here rather than
    # silently letting the macro live somewhere unchecked.
    if WITNESS_ENV not in blocks:
        problems.append(
            "the whitelisted witness environment %r is not in "
            "platformio.ini -- the whitelist has drifted from the file "
            "it is meant to describe" % WITNESS_ENV)
    else:
        if WITNESS_ENV == TRUCK_ENV:
            problems.append(
                "the witness environment and the truck environment are the "
                "same (%r)" % WITNESS_ENV)
        wb = blocks[WITNESS_ENV]
        if "INTP_NO_SERIAL" in wb:
            problems.append(
                "%r defines INTP_NO_SERIAL: the witness build needs serial "
                "to dump, and a silent one could not report anything"
                % WITNESS_ENV)
        if "INTP_ORDER_WITNESS" not in wb:
            problems.append(
                "%r does not define INTP_ORDER_WITNESS, so it is not a "
                "witness build and the whitelist entry is meaningless"
                % WITNESS_ENV)

    for macro, header in DIAG_GATES:
        # The control, in the same spirit as the serial markers: if the
        # macro is no longer in the source it gates nothing, and its
        # absence from platformio.ini proves nothing at all.
        hp = os.path.join(FW, header.replace("/", os.sep))
        try:
            with open(hp, "r") as f:
                src = f.read()
        except IOError as e:
            problems.append("cannot read %s: %s" % (header, e))
            continue
        if macro not in src:
            problems.append(
                "%s does not appear in %s -- the gate this check looks for "
                "no longer exists, so its absence from platformio.ini "
                "proves nothing" % (macro, header))
            continue
        for env, body in sorted(blocks.items()):
            if macro not in body:
                continue
            if env == WITNESS_ENV and macro == WITNESS_MACRO:
                continue
            if env == NON_ENV:
                problems.append(
                    "%s appears outside any [env:...] section (e.g. in "
                    "[common]), which every environment inherits" % macro)
            else:
                problems.append(
                    "%s appears in [env:%s]: diagnostic instrumentation "
                    "would be compiled into that firmware image"
                    % (macro, env))

    if not problems:
        print("diagnostic gates: %d defined in source; only [env:%s] enables "
              "one, and it keeps serial" % (len(DIAG_GATES), WITNESS_ENV))
    return problems


def check_truck_is_silent():
    problems = []
    truck, bench = image(TRUCK_ENV), image(BENCH_ENV)
    # image() returns None when an environment has no image OR more
    # than one, and this used to pass that straight to os.path.exists
    # and die with a TypeError. A crash is not a check result: it
    # reports nothing about the truck image either way.
    for p, label in ((truck, TRUCK_ENV), (bench, BENCH_ENV)):
        if p is None:
            problems.append(
                "no single image for %s, so the serial-marker check cannot "
                "run (check_images_exist says which)" % label)
        elif not os.path.exists(p):
            problems.append("no image for %s at %s" % (label, p))
    if problems:
        return problems

    with open(truck, "rb") as f:
        t = f.read()
    with open(bench, "rb") as f:
        b = f.read()

    for marker in SERIAL_MARKERS:
        # The control first: if this fails the search is broken, and the
        # absence below means nothing.
        if marker not in b:
            problems.append(
                "%r is not in the BENCH image either -- the check is looking "
                "for a string that no longer exists, so its absence from the "
                "truck image proves nothing" % marker.decode())
            continue
        if marker in t:
            problems.append(
                "%r is in the TRUCK image: serial output was not compiled "
                "out (spec 8.3)" % marker.decode())
    if not problems:
        print("truck image: %d serial markers absent, all %d present in the "
              "bench image" % (len(SERIAL_MARKERS), len(SERIAL_MARKERS)))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true",
                    help="skip the build and check the images already in "
                         ".pio/build (they may be stale -- say so if it "
                         "matters)")
    a = ap.parse_args()

    envs = environments()
    print("platformio.ini declares: %s" % ", ".join(envs))
    problems = []
    if len(envs) < 2:
        problems.append("only %d environment(s) parsed from platformio.ini -- "
                        "the regex is not finding them" % len(envs))
    for need in (TRUCK_ENV, BENCH_ENV):
        if need not in envs:
            problems.append("[env:%s] is not in platformio.ini; this check "
                            "is written around it" % need)

    t0 = time.time()
    if not problems and not a.no_build:
        problems += build(envs)
    if not problems:
        problems += check_images_exist(envs)
        if not a.no_build:
            problems += check_images_fresh(envs, t0)
        problems += check_witness_marker(envs)
        problems += check_manifest(envs)
        problems += check_truck_is_silent()
        problems += check_diagnostics_are_gated()

    if problems:
        print("\nFAIL -- %d problem(s):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    # Note the digest check above already proves these images came from
    # the tree on disk, so "may be stale" no longer means "may be from
    # other sources" -- it means the toolchain or platform version could
    # have moved, which the digest does not cover and the manifest does.
    how = ("%d environments NOT rebuilt (--no-build), but verified against "
           "the tree's source digest, so the images checked "
           "may be stale" % len(envs)) if a.no_build else (
           "%d environments build" % len(envs))
    print("\nOK -- %s; the truck image is silent and no diagnostic "
          "instrumentation is compiled in" % how)
    return 0


if __name__ == "__main__":
    sys.exit(main())
