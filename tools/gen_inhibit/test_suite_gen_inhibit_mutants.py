#!/usr/bin/env python3
"""Mutants of suite_gen_inhibit.py; test_suite_gen_inhibit.py must fail on each BY A
CHECK. A crash is not a kill, and an edit that does not apply is an error.

    PYTHONPATH=<core e37995a>/shared/bench GI_BASELINE_DIR=<.../runs/baseline_e3d9437> \
        py -3.14 -B test_suite_gen_inhibit_mutants.py

Each mutant copies suite_gen_inhibit.py, its test and the settings example into a fresh
folder in the machine's temp folder and applies one edit; everything else (suite_judges,
bench_flash) is imported from this folder. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "suite_gen_inhibit.py"
TEST = "test_suite_gen_inhibit.py"
EXAMPLE = "suite_gen_inhibit.settings.example.json"   # the test reads it beside itself

MUTANTS = [
    ("identify WiCAN: an unreadable page is 'not this image'",
     '''raise IdentifyError("the WiCAN's /gen_inhibit could not be read: %r" % (last,))''', 'return None'),
    ("identify WiCAN: a page without the hash is 'not this image'",
     '''raise IdentifyError("the WiCAN's page has no elf_sha256 (%r)" % (sha,))''', 'return None'),
    ("identify WiCAN: any hash string will do",
     'if not (isinstance(sha, str) and SHA_RX.match(sha)):', 'if sha is None:'),
    ("identify X2: a port that would not open is not looked for",
     'if PORT_RX.search(t + _text(recs[-1])):', 'if False:'),
    ("identify X2: the port error is looked for in the log only",
     'if PORT_RX.search(t + _text(recs[-1])):', 'if PORT_RX.search(t):'),
    ("identify X2: an older pins record stands in",
     'recs = new_records(logdir, "x2_hold_pins_*.json", before)',
     'recs = sorted(glob.glob(os.path.join(logdir, "x2_hold_pins_*.json")))'),
    ("identify X2: an older jammer record stands in",
     'recs = new_records(logdir, "x2_jammer_id_*.json", before)',
     'recs = sorted(glob.glob(os.path.join(logdir, "x2_jammer_id_*.json")))'),
    ("identify X2: any pin verdict is a hold",
     'if rc == 0 and "TX (GPIO7) not driven" in t:', 'if "TX (GPIO7)" in t:'),
    ("identify X2: bench_identify is skipped (ID goes to every image)",
     'got = BI.identify(image, ctx, logdir, settings)', 'got = None'),
    ("identify X2: no banner is the jammer",
     'return "jammer:banner" if json.load(f).get("banner_ok") else None', 'return "jammer:banner"'),
    ("ota: the exit is not looked at", 'ok = rc == 0 and "VERDICT: (c) TOOK" in t', 'ok = "VERDICT: (c) TOOK" in t'),
    ("ota: the verdict is not looked at", 'ok = rc == 0 and "VERDICT: (c) TOOK" in t', 'ok = rc == 0'),
    ("ota: no dual-slot restore before the retry",
     'ok, d2 = upload("restore", cur[0].name, cur[0].params["bin"], settings_, logdir)', 'ok, d2 = True, "skipped"'),
    ("ota: the restore uploads the wanted image, not the running build",
     'ok, d2 = upload("restore", cur[0].name, cur[0].params["bin"], settings_, logdir)',
     'ok, d2 = upload("restore", cur[0].name, params["bin"], settings_, logdir)'),
    ("ota: no second attempt", 'ok, d3 = upload("attempt2", image.name, params["bin"], settings_, logdir)',
     'ok, d3 = False, "no retry"'),
    ("ota: a failed restore is not looked at",
     '        if not ok:\n            return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=False)',
     '        pass'),
    ("ota: a failed restore allows more WiCAN flashes",
     'return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=False)',
     'return dict(ok=False, phase="restore", detail="%s; %s" % (d1, d2), recoverable=True)'),
    ("ota: any WiCAN image is taken as the running build",
     'cur = [im for im in images.values() if im.board == "wican" and im.expect == sha]',
     'cur = [im for im in images.values() if im.board == "wican"]'),
    ("ota: an unreadable page after a failure is not caught",
     '            sha = wican_sha()\n        except IdentifyError as e:', '            sha = wican_sha()\n        except ZeroDivisionError as e:'),
    ("ota: the first attempt's failure line is dropped",
     'detail="%s; %s; %s" % (d1, d2, d3)', 'detail="%s; %s" % (d2, d3)'),
    ("x2_hold: any pin state is a hold", 'ok = rc == 0 and "TX (GPIO7) not driven" in t', 'ok = rc == 0'),
    ("x2_jammer: the dump's verdict is not read", 'if rc != 0 or verdict != "PASS":', 'if rc != 0 or not recs:'),
    ("x2_jammer: a failed jammer write allows more X2 flashes",
     'detail="flash-jammer rc %s" % rc, recoverable=False)', 'detail="flash-jammer rc %s" % rc, recoverable=True)'),
    ("redact: a binary is rewritten as text",
     'if os.path.splitext(p)[1].lower() not in TEXT_EXT:', 'if False:'),
    ("redact: only the first occurrence", 's.replace(ssid, "<GI_WICAN_SSID>")', 's.replace(ssid, "<GI_WICAN_SSID>", 1)'),
    ("redact: a hook's new records are not redacted",
     'redact([logpath] + [os.path.join(d, n) for n in sorted(set(os.listdir(d)) - before)])', 'redact([logpath])'),
    ("check.log is not a declared output", 'outputs=list(outputs) + ["check.log"])', 'outputs=list(outputs))'),
    ("adapter: a non-zero exit is not looked at", '        if run.rc != 0:', '        if False:'),
    ("adapter: results are not named by their judge", 'out.append(Result(r["verdict"], sub=cid,',
     'out.append(Result(r["verdict"], sub="",'),
    ("the ELF sha from the wrong offset", 'return head[0xB0:0xD0].hex()', 'return head[0x90:0xB0].hex()'),
    ("a WiCAN image expects a short sha", 'Image(name, "ota", {"bin": b}, elf_sha256_of_bin(b), board="wican")',
     'Image(name, "ota", {"bin": b}, elf_sha256_of_bin(b)[:8], board="wican")'),
    ("B8 is judged nowhere", 'judged("B5", "B6", "B7", "B8")', 'judged("B5", "B6", "B7")'),
    ("B19page is judged nowhere", 'judged("B19absent", "B19page")', 'judged("B19absent")'),
    ("B27 is dropped instead of skipped", 'Check(id="B27", argv=["true"], skip=B27_SKIP)', 'Check(id="B27", argv=["true"])'),
    ("a bench group does not hold the lock", 'Group("man2", needs_lock=True,', 'Group("man2",'),
    ("a desk step reads a check that runs after it", 'desk("B2", "B2", ["man_app/B2app", "man_held/B2held"]',
     'desk("B2", "B2", ["man_app/B2app", "man_held/B9"]'),
    ("a placeholder no setting fills", '"REMOTE": "{gi_host_ssh}"', '"REMOTE": "{gi_hostssh}"'),
    ("the WiCAN's standing image is not _man", 'standing_images={"wican": "wican_man", "x2": "x2_app"}',
     'standing_images={"wican": "wican_man2", "x2": "x2_app"}'),
    ("code: an image from another commit is not dirty",
     'clean = all(t["clean"] and t["at"] for t in trees.values()) and all(',
     'clean = all(t["clean"] and t["at"] for t in trees.values()) or all('),
    ("code: HEAD need not be gi_commit", 'st["at"] = bool(st["head"] and commit and st["head"].startswith(commit))',
     'st["at"] = True'),
    ("code: the emulated suite is never dirty", '"dirty": not (t["clean"] and t["at"])', '"dirty": False'),
    ("the bench suite does not require the emulated one", 'requires="gen-inhibit-emulated",', 'requires=None,'),
    ("a bench check runs a bare bash (WSL's on Windows)",
     'kw = dict(id=cid, argv=["{bash}", "%s/%s" % (H, script)]', 'kw = dict(id=cid, argv=["bash", "%s/%s" % (H, script)]'),
    ("reads: a desk check reads nothing",
     'out["%s/%s" % (g.name, c.id)] = [a[len("{run_dir}/"):] for a in c.argv if a.startswith("{run_dir}/")]',
     'out["%s/%s" % (g.name, c.id)] = []'),
    ("a setting the example does not name", 'prod_dir = need(settings, "gi_prod_dir")',
     'prod_dir = need(settings, "gi_prod_dir") or need(settings, "gi_prod_extra")'),
    ("an argv carries a local path", '["{gi_images}", "{gi_prod_dir}"] + step.split()', '[images_dir, prod_dir] + step.split()'),
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "suite_gen_inhibit")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (env.get("PYTHONPATH"), HERE) if p)
    killed = errors = 0
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(old, new))
        for f in (TEST, EXAMPLE):
            shutil.copy(os.path.join(HERE, f), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True,
                           env=env)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "",
            ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
