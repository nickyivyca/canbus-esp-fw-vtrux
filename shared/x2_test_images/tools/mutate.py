"""Mutants of check_image.py, archive.py and flash_row.py: the can-fail of
test_tools.py.

TEST EQUIPMENT. Shared harness of x2_test_images (owner: the testers).

Each mutant breaks one check by a text substitution that must match exactly
once; test_tools.py must FAIL on every mutant and PASS on the unmutated
files first. Each run gets a copy of the tools folder, laid out as in the
tree (shared/x2_test_images/tools/, ../jammer/x2_image.json, and
shared/tools and shared/bench, which are linked, not copied), under
<worktree>/scratch/x2-tools-mutants/<pid>/, removed at the end, pass or
fail.

    python3.13 -B tools/mutate.py
Exit 0: the original passes and every mutant fails. Exit 1 otherwise.
"""

import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", ".."))
FILES = ("check_image.py", "archive.py", "flash_row.py", "test_tools.py")
TIMEOUT_S = 300

# (name, file, the check it breaks, old text, new text)
MUTANTS = [
    ("no-forbidden", "check_image.py", "forbidden symbols pass",
     "bad = sorted(s for s in syms if any(s.startswith(p) for p in prefixes))",
     "bad = []"),
    ("variant-from-arg", "check_image.py", "the offered variant is trusted",
     'r.add("variant", found == variant,', 'r.add("variant", True,'),
    ("long-names-ok", "check_image.py", "names over 31 characters pass",
     'r.add("project_name", found is not None and not long_names,',
     'r.add("project_name", found is not None,'),
    ("symbols-min", "check_image.py", "a near-empty symbol table passes",
     "len(syms) > SYMBOLS_MIN and not req_missing", "not req_missing"),
    ("elf-hash", "check_image.py", "an ELF hash mismatch passes",
     'r.add("elf_hash", elf_sha == app.image_hash,', 'r.add("elf_hash", True,'),
    ("strings", "check_image.py", "missing strings pass",
     'missing = [s for s in data.get("required_strings", [])\n               if s.encode("ascii") not in app.data]',
     "missing = []"),
    ("config", "check_image.py", "a wrong config value passes",
     "if cfg.get(key) != want:", "if False:"),
    ("nfv", "check_image.py", "a name without NOT_FOR_VEHICLE passes",
     '"NOT_FOR_VEHICLE" in app.project_name,', "True,"),
    ("chip", "check_image.py", "another chip passes",
     "want_chip is None or app.chip == want_chip,", "True,"),
    ("variant-pinnable", "archive.py", "a test variant is pinnable",
     '"variant_pinnable": data["variants"][image_variant]["pinnable"],',
     '"variant_pinnable": True,'),
    ("ignored-anywhere", "archive.py", "ignored files anywhere count as clean",
     'if code == "!!" and path.startswith(allowed):', 'if code == "!!":'),
    ("no-after", "archive.py", "the status after the build is not read",
     '"clean_after": not unclean_after,', '"clean_after": True,'),
    ("tracked", "archive.py", "a changed tracked file is not seen",
     '"no_tracked_change": (not before["tracked_changed"]',
     '"no_tracked_change": True or (not before["tracked_changed"]'),
    ("any-tag", "archive.py", "any tag counts as a shared tag",
     "tag = tag if rc == 0 and TAG_RE.match(tag) else None",
     "tag = tag if rc == 0 else None"),
    ("version", "archive.py", "the version is not compared with the tag",
     '"version_is_tag": before["tag"] is not None and app.version == before["tag"],',
     '"version_is_tag": before["tag"] is not None,'),
    ("archive-failed", "archive.py", "a build check_image fails is archived",
     "    if not res.ok:\n", "    if False:\n"),
    ("rewrite", "archive.py", "the manifest is rewritten, not appended",
     '"manifest.jsonl"), "a",', '"manifest.jsonl"), "w",'),
    ("inside-tree", "archive.py", "an archive inside the git tree is allowed",
     'if os.path.realpath(dest).startswith(os.path.realpath(before["top"]) + os.sep):',
     "if False:"),
    ("folder-differs", "archive.py", "an existing folder's files are not compared",
     "if not os.path.isfile(have) or _hashes(have) != _hashes(p):",
     "if not os.path.isfile(have):"),
    ("rid-unset", "archive.py", "an unset REVERSE_IT_DIR falls back",
     '    if not rid:\n        raise Refused("REVERSE_IT_DIR is not set")\n',
     '    if not rid:\n        rid = os.path.dirname(os.environ.get("HOME", "/"))\n'),
    ("fr-size", "flash_row.py", "a file's size is not compared with the row",
     'for k in ("size", "sha256", "md5"):', 'for k in ("sha256", "md5"):'),
    ("fr-sha256", "flash_row.py", "a file's sha256 is not compared",
     'for k in ("size", "sha256", "md5"):', 'for k in ("size", "md5"):'),
    ("fr-md5", "flash_row.py", "a file's md5 is not compared",
     'for k in ("size", "sha256", "md5"):', 'for k in ("size", "sha256"):'),
    ("fr-overlap", "flash_row.py", "overlapping files are flashed",
     "if a[0] + a[3] > b[0]:", "if False:"),
    ("fr-bare-name", "flash_row.py", "a file name with a path is followed",
     'if os.path.basename(name) != name or name in ("", ".", ".."):',
     "if False:"),
    ("fr-folder", "flash_row.py", "the row's folder is not the app sha's",
     "folder != \"builds/%s\" % str(row.get(\"app_sha256\", \"\"))[:16]:",
     "False:"),
    ("fr-app-sha", "flash_row.py", "the app file is not the row's app_sha256",
     'if app[0][4] != row.get("app_sha256"):', "if False:"),
    ("fr-descriptor", "flash_row.py", "the app's descriptor is not compared",
     "        if have != want:\n", "        if False:\n"),
    ("fr-dev", "flash_row.py", "a dev build flashes without --dev",
     "if not pinnable and not dev:", "if False:"),
    ("fr-rows-agree", "flash_row.py", "rows naming different files pass",
     "if _files_key(r) != _files_key(first):", "if False:"),
    ("fr-chip-type", "flash_row.py", "a settings chip type for another chip",
     'if sc.get("type") and sc["type"] != typ:', "if False:"),
    ("fr-lock-role", "flash_row.py", "a lock held by another role is enough",
     'if h is None or h["who"] != role:', "if h is None:"),
    ("fr-canx2-skip", "flash_row.py", "canx2 is not detached before the flash",
     'res["canx2"] = detach_canx2(logdir)', 'res["canx2"] = {}'),
    ("fr-canx2-netdev", "flash_row.py", "netdev canx2 is not a sign",
     "if os.path.lexists(os.path.join(net_root, CANX2)):", "if False:"),
    ("fr-canx2-slcand", "flash_row.py", "an slcand for canx2 is not a sign",
     "                    and argv[-1] == CANX2:", "                    and False:"),
    ("fr-canctl-rc", "flash_row.py", "canctl's exit is not read",
     "    if rc != 0:\n        raise DetachFailed", "    if False:\n        raise DetachFailed"),
    ("fr-canx2-after", "flash_row.py", "canx2 left attached is not refused",
     "    if after is not None:\n        raise DetachFailed",
     "    if False:\n        raise DetachFailed"),
    ("fr-canx2-poll", "flash_row.py", "the state is not polled after canctl",
     "while rc == 0 and after is not None and monotonic() < t_end:",
     "while False:"),
    ("fr-args-flash", "flash_row.py", "a flash runs without --settings or --role",
     "if missing and not a.check_only:", "if False:"),
    ("fr-args-check", "flash_row.py", "--check-only needs --settings and --role",
     "if missing and not a.check_only:", "if missing:"),
    ("fr-own-bench", "flash_row.py", "another vtrux_bench is used",
     "    if got != want:\n", "    if False:\n"),
    ("fr-files-passed", "flash_row.py", "not every file reaches the flasher",
     'params = {"files": [["0x%x" % o, p] for o, p, _m in files],',
     'params = {"files": [["0x%x" % o, p] for o, p, _m in files][1:],'),
    ("fr-fail-ok", "flash_row.py", "a failed flash exits 0",
     '    if res["ok"]:\n', "    if True:\n"),
    ("fr-in-checkout", "flash_row.py", "a log folder in the checkout is used",
     "return p != top and not p.startswith(top + os.sep)", "return True"),
]


def _onerror(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError as e:
        print("could not remove %s: %s" % (path, e))


def remove(path):
    if os.path.exists(path):
        shutil.rmtree(path, onerror=_onerror)
    if os.path.exists(path):
        print("could not remove %s" % path)


def layout(d, originals, override=None):
    """A copy of the tools folder in the tree's shape; returns its tests."""
    tools = os.path.join(d, "shared", "x2_test_images", "tools")
    os.makedirs(tools)
    os.makedirs(os.path.join(d, "shared", "x2_test_images", "jammer"))
    shutil.copy2(os.path.join(SHARED, "x2_test_images", "jammer", "x2_image.json"),
                 os.path.join(d, "shared", "x2_test_images", "jammer"))
    os.symlink(os.path.join(SHARED, "tools"), os.path.join(d, "shared", "tools"))
    os.symlink(os.path.join(SHARED, "bench"), os.path.join(d, "shared", "bench"))
    for name, text in originals.items():
        if override and override[0] == name:
            text = override[1]
        with open(os.path.join(tools, name), "w", encoding="utf-8",
                  newline="") as f:
            f.write(text)
    return os.path.join(tools, "test_tools.py")


def run(test):
    try:
        r = subprocess.run([sys.executable, "-B", test], capture_output=True,
                           text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return r.returncode, r.stdout + r.stderr


def main():
    top = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True,
                         check=True).stdout.strip()
    scratch = os.path.join(top, "scratch", "x2-tools-mutants", str(os.getpid()))
    if subprocess.run(["git", "-C", top, "check-ignore", "-q",
                       scratch]).returncode:
        print("REFUSED: %s is not ignored by git" % scratch)
        return 1
    originals = {}
    for name in FILES:
        with open(os.path.join(HERE, name), encoding="utf-8", newline="") as f:
            originals[name] = f.read()
    bad = 0
    try:
        os.makedirs(scratch)
        rc, text = run(layout(os.path.join(scratch, "original"), originals))
        if rc != 0:
            print("ORIGINAL FAILS: test_tools.py must pass unmutated")
            print(text)
            return 1
        print("%-18s PASS (as it must)" % "original")
        for name, fname, what, old, new in MUTANTS:
            n = originals[fname].count(old)
            if n != 1:
                print("%-18s BROKEN MUTANT: matches %d times in %s"
                      % (name, n, fname))
                bad += 1
                continue
            test = layout(os.path.join(scratch, name), originals,
                          (fname, originals[fname].replace(old, new)))
            rc, _text = run(test)
            if rc == 0:
                print("%-18s SURVIVED: %s" % (name, what))
                bad += 1
            else:
                print("%-18s killed    (%s)" % (name, what))
    finally:
        remove(scratch)
        try:
            os.rmdir(os.path.dirname(scratch))
        except OSError:
            pass
    print()
    if bad:
        print("%d of %d mutants survived or are broken" % (bad, len(MUTANTS)))
        return 1
    print("all %d mutants killed" % len(MUTANTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
