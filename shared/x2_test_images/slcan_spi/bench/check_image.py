"""slcan_spi's checks on a built image, no board needed. NOT FOR VEHICLE
(x2_test_images SPEC T14).

  name    the app's project_name, its image name, is
          slcan_spi_NOT_FOR_VEHICLE (T14), read with the shared tooling's
          image library (vtrux_build.image)
  hash    the image's app_elf_sha256, the identity its N and V replies give
          (T10, T12), is the SHA-256 of the ELF beside it
  twai    the ELF has no symbol with "twai" in its name: the TWAI driver is
          not linked, so CAN1's controller is never started and nothing is
          sent on CAN1 (T13, while this build has no jamming)

    python3.13 -B check_image.py --build <build folder> --shared-tools <shared-<tag>/shared/tools>
    python3.13 -B check_image.py ... --can-fail

--can-fail: the control (the real build must PASS), then each check on a
broken input, each of which must FAIL: an expected name with one letter
changed; an ELF hash taken from a file one byte longer; and an object file
that defines twai_driver_install, compiled with ESP-IDF's own compiler for
the S3 into <worktree>/scratch/slcan-spi-check-image/<pid>/, removed at the
end. Exit 0 only if the control passes and every broken input fails.

The nm and gcc used are the ESP-IDF v5.4.2 toolchain's, found under
IDF_TOOLS_PATH (set by the export; local-paths.md).
"""

import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys

NAME = "slcan_spi_NOT_FOR_VEHICLE"


def tool(name):
    tp = os.environ.get("IDF_TOOLS_PATH", "")
    hits = sorted(glob.glob(os.path.join(tp, "tools", "xtensa-esp-elf", "*",
                                         "xtensa-esp-elf", "bin",
                                         "xtensa-esp32s3-elf-" + name)))
    if not tp or not hits:
        raise SystemExit("REFUSED: no xtensa-esp32s3-elf-%s under IDF_TOOLS_PATH=%r"
                         % (name, tp))
    return hits[-1]


def check_name(image, want=NAME):
    got = image.project_name
    return [] if got == want else ["project_name %r, not %r" % (got, want)]


def check_hash(image, elf_bytes):
    got = image.image_hash
    want = hashlib.sha256(elf_bytes).hexdigest()
    return [] if got == want else ["app_elf_sha256 %s, the ELF is %s" % (got, want)]


def check_twai(elf_path):
    r = subprocess.run([tool("nm"), elf_path], capture_output=True, text=True)
    if r.returncode != 0:
        return ["nm failed: %s" % r.stderr.strip()]
    hits = [ln.split()[-1] for ln in r.stdout.splitlines() if "twai" in ln.lower()]
    return ["TWAI symbols in %s: %s" % (os.path.basename(elf_path), hits[:5])] if hits else []


def find(build, pattern):
    hits = glob.glob(os.path.join(build, pattern))
    if len(hits) != 1:
        raise SystemExit("REFUSED: %d files match %s in %s" % (len(hits), pattern, build))
    return hits[0]


def worktree_scratch(name):
    here = os.path.dirname(os.path.abspath(__file__))
    root = subprocess.run(["git", "-C", here, "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    path = os.path.join(root, "scratch", name, str(os.getpid()))
    if subprocess.run(["git", "-C", root, "check-ignore", "-q", path]).returncode != 0:
        raise SystemExit("REFUSED: %s is not ignored by git" % path)
    return path


def run_checks(build, image_mod):
    bin_path = find(build, NAME + ".bin")
    elf_path = find(build, NAME + ".elf")
    image = image_mod.AppImage.read(bin_path)
    with open(elf_path, "rb") as f:
        elf = f.read()
    return {"name": check_name(image), "hash": check_hash(image, elf),
            "twai": check_twai(elf_path)}, image, elf, elf_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", required=True)
    ap.add_argument("--shared-tools", required=True)
    ap.add_argument("--can-fail", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.shared_tools))
    from vtrux_build import image as image_mod

    results, image, elf, elf_path = run_checks(args.build, image_mod)
    failed = 0
    for k, problems in results.items():
        print("%-6s %s %s" % (k, "FAIL" if problems else "PASS", "; ".join(problems)))
        failed += bool(problems)
    print("elf sha256 %s, version %s" % (image.image_hash, image.version))
    if not args.can_fail:
        return 1 if failed else 0

    print("\ncan-fail:")
    if failed:
        print("CONTROL FAILS: no broken input is counted")
        return 1
    survived = 0
    broken = {
        "name": check_name(image, NAME.replace("VEHICLE", "VEHICLF")),
        "hash": check_hash(image, elf + b"\0"),
    }
    scratch = worktree_scratch("slcan-spi-check-image")
    try:
        os.makedirs(scratch)
        src = os.path.join(scratch, "twai.c")
        obj = os.path.join(scratch, "twai.o")
        with open(src, "w") as f:
            f.write("void twai_driver_install(void) {}\n")
        r = subprocess.run([tool("gcc"), "-c", src, "-o", obj], capture_output=True, text=True)
        if r.returncode != 0:
            print("could not compile the TWAI object: %s" % r.stderr.strip())
            return 1
        broken["twai"] = check_twai(obj)
    finally:
        shutil.rmtree(scratch, ignore_errors=False)
        try:
            os.rmdir(os.path.dirname(scratch))
        except OSError:
            pass
    for k, problems in broken.items():
        print("%-6s %s %s" % (k, "FAILS (as it must)" if problems else "SURVIVED",
                              "; ".join(problems)))
        survived += not problems
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
