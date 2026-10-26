#!/usr/bin/env python3
"""Test bench_images.sh's img_in and the scripts that use it (port step 0c, 2026-10-25).

Port step 0c took the commit out of project_name, and the image file is named after it:
wican-fw_obd_<C>_man[_rt].bin before, wican-fw_obd_man[_rt].bin from 0c on.
baseline_b_hand.sh (B17a, B18) and baseline_b_otagroup.sh named only the first form.
img_in, sourced by both, finds either, and only when exactly one is there. In a real
bash, in fake folders (C=abc1234):
  - an old-named image, a new-named one: found; with the suffix "" only the plain _man;
    with no suffix the variant's own (_rt); ota_data_initial.bin and another commit's
    image never;
  - both forms, or none: refused (exit 1, the count on stderr, a NO-SINGLE-IMAGE path);
  - baseline_b_otagroup.sh's ota() stops on a missing image before ota_pcap.sh runs;
  - neither script names an image file by the commit any more, and both source the
    helper.

    py -3.14 -B gen-inhibit/tools/bench/test_bench_image_lookup.py [--mutants]

Gen-inhibit tester, 2026-10-25.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASH = shutil.which("bash") or r"C:/Program Files/Git/usr/bin/bash.exe"
HELPER = "bench_images.sh"
SCRATCH = os.path.normpath(os.path.join(HERE, "..", "..", "..", "scratch", "bench-image-lookup"))
FAILS = []


def fw(p):
    return p.replace("\\", "/")


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (str(detail)[:300],)))
    if not ok:
        FAILS.append(name)


def folder(base, name, files):
    d = os.path.join(base, name)
    os.makedirs(d)
    for f in files:
        open(os.path.join(d, f), "w").close()
    return d


def img_in(helper_dir, d, *suffix):
    args = " ".join('"%s"' % a for a in suffix)
    r = subprocess.run([BASH, "-c", 'C=abc1234; . "%s"; img_in "%s" %s' % (fw(os.path.join(helper_dir, HELPER)),
                                                                          fw(d), args)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def ota_fn(src):
    """baseline_b_otagroup.sh's ota() function, as text."""
    m = re.search(r"^ota\(\) \{.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else ""


def checks(helper_dir, script_dir):
    os.makedirs(SCRATCH, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="img-lookup-", dir=SCRATCH) as t:
        old = folder(t, "old", ["wican-fw_obd_abc1234_man.bin", "ota_data_initial.bin", "wican-fw_obd_def5678_man.bin"])
        new = folder(t, "new", ["wican-fw_obd_man.bin", "ota_data_initial.bin"])
        both = folder(t, "both", ["wican-fw_obd_abc1234_man.bin", "wican-fw_obd_man.bin"])
        none = folder(t, "none", ["ota_data_initial.bin", "wican-fw_obd_def5678_man.bin"])
        rt = folder(t, "rt", ["wican-fw_obd_man_rt.bin", "ota_data_initial.bin"])
        rt_old = folder(t, "rt_old", ["wican-fw_obd_abc1234_man_rt.bin"])
        for label, d, sfx, want in (
                ("an old-named image (the commit in the name), suffix \"\"", old, ("",), "wican-fw_obd_abc1234_man.bin"),
                ("a new-named image (no commit), suffix \"\"", new, ("",), "wican-fw_obd_man.bin"),
                ("a new-named variant image, no suffix", rt, (), "wican-fw_obd_man_rt.bin"),
                ("an old-named variant image, no suffix", rt_old, (), "wican-fw_obd_abc1234_man_rt.bin"),
                ("a new-named plain image, no suffix", new, (), "wican-fw_obd_man.bin")):
            rc, out, err = img_in(helper_dir, d, *sfx)
            check("found: %s" % label, rc == 0 and out == fw(os.path.join(d, want)), (rc, out, err))
        rc, out, err = img_in(helper_dir, rt, "")
        check("suffix \"\" does not take the variant's image", rc == 1 and out.endswith("NO-SINGLE-IMAGE"), (rc, out, err))
        for label, d in (("both forms", both), ("neither form (another commit's image only)", none)):
            rc, out, err = img_in(helper_dir, d, "")
            check("refused: %s" % label, rc == 1 and out.endswith("NO-SINGLE-IMAGE") and "img_in: " in err,
                  (rc, out, err))
        # ota() stops on a missing image, before ota_pcap.sh
        src = open(os.path.join(script_dir, "baseline_b_otagroup.sh"), encoding="utf-8").read()
        fn = ota_fn(src)
        stub = folder(t, "stub", [])
        with open(os.path.join(stub, "ota_pcap.sh"), "w", newline="\n") as fh:
            fh.write('echo PCAP_RAN "$1"; echo "VERDICT: (c) TOOK" > "$2.txt"\n')
        body = 'A="%s"; D="%s"; s=x\n%s\nota rt "%s"; echo "after rc=$?"' % (fw(stub), fw(stub), fn,
                                                                          fw(os.path.join(stub, "NO-SINGLE-IMAGE")))
        r = subprocess.run([BASH, "-c", body], capture_output=True, text=True, timeout=60)
        check("ota(): a missing image stops the group before ota_pcap.sh runs",
              bool(fn) and r.returncode == 1 and "PCAP_RAN" not in r.stdout and "after" not in r.stdout,
              (r.returncode, r.stdout, r.stderr))
        present = os.path.join(stub, "img.bin")
        open(present, "w").close()
        body = 'A="%s"; D="%s"; s=x\n%s\nota rt "%s"; echo "after rc=$?"' % (fw(stub), fw(stub), fn, fw(present))
        r = subprocess.run([BASH, "-c", body], capture_output=True, text=True, timeout=60)
        check("ota(): an image that is there goes to ota_pcap.sh", "PCAP_RAN" in r.stdout and "after rc=0" in r.stdout,
              (r.returncode, r.stdout, r.stderr))
    for s in ("baseline_b_hand.sh", "baseline_b_otagroup.sh"):
        src = open(os.path.join(script_dir, s), encoding="utf-8").read()
        check("%s names no image file by the commit" % s, "wican-fw_obd_${C}" not in src,
              [ln for ln in src.splitlines() if "wican-fw_obd_${C}" in ln])
        check("%s sources bench_images.sh" % s, '. "$A/bench_images.sh"' in src, "")


MUTANTS = [   # (file, label, old, new)
    (HELPER, "the new form not looked for", ' "$d/wican-fw_obd_man$sfx.bin"\n', '\n'),
    (HELPER, "the old form not looked for", 'set -- "$d/wican-fw_obd_${C}_man$sfx.bin" ', 'set -- '),
    (HELPER, "more than one accepted", 'if [ "$n" != 1 ]; then', 'if [ "$n" = 0 ]; then'),
    (HELPER, "no suffix means plain _man only", 'set -- "$d"/wican-fw_obd_${C}_man*.bin "$d"/wican-fw_obd_man*.bin',
     'set -- "$d"/wican-fw_obd_${C}_man.bin "$d"/wican-fw_obd_man.bin'),
    (HELPER, "a refusal returns 0", '    return 1\n', '    return 0\n'),
    ("baseline_b_otagroup.sh", "ota() runs on a missing image",
     '  [ -f "$2" ] || { echo "OTA $1: no image $2: the group stops here"; exit 1; }\n', ''),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        checks(HERE, HERE)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    for i, (f, label, old, new) in enumerate(MUTANTS, 1):
        os.makedirs(SCRATCH, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="img-lookup-m%d-" % i, dir=SCRATCH) as tmp:
            for g in (HELPER, "baseline_b_hand.sh", "baseline_b_otagroup.sh"):
                shutil.copy(os.path.join(HERE, g), tmp)
            src = open(os.path.join(tmp, f), encoding="utf-8", newline="").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: the edit matches %d times" % (i, label, src.count(old)))
                continue
            open(os.path.join(tmp, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            del FAILS[:]
            try:
                checks(tmp, tmp)
            except Exception as e:  # noqa: BLE001  a crash is not a kill
                print("CRASHED  m%d %s: %s" % (i, label, e))
                continue
            killed += bool(FAILS)
            print("%s m%d %s%s" % ("KILLED  " if FAILS else "SURVIVED", i, label, "  (%s)" % FAILS[0] if FAILS else ""))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
