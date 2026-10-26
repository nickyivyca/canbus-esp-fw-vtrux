#!/usr/bin/env python3
"""Mutants of suite_gen_inhibit.py; test_suite_gen_inhibit.py must fail on each BY A
CHECK. A crash is not a kill, and an edit that does not apply is an error.

    PYTHONPATH=<core b9896a0>/shared/bench GI_BASELINE_DIR=<.../runs/baseline_e3d9437> \
        py -3.14 -B test_suite_gen_inhibit_mutants.py

The mutants run in a shared clone of this repo at HEAD, made in the worktree's scratch/,
so the copy sits where the suite does (gen-inhibit/tools/ since port step 0d) and
gi_diag finds the DBC's
git history from bench/ (B15host exports it; in a bare folder it found no history, and
every mutant failed the same six B15host checks). This folder's test, settings example
and bench/ (the suite's HARNESS, 2026-10-22) are copied over the clone's; each mutant
writes suite_gen_inhibit.py there with one edit. The unmutated copy runs first, as the
control: if it fails a check, no kill counts. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "bench"))
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)
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
    ("a bench group does not hold the lock", 'Group("prod", needs_lock=True,', 'Group("prod",'),
    # B17b on rxq (reviewer, 2026-10-25: reproducible builds made _man2 = _man)
    ("B17b on a step other than rxq_page", 'otag("B17b", "rxq_page",', 'otag("B17b", "rxq",'),
    ("B17b's file still _man2", '["B17_spec11_page_%s_rxq.txt" % C], "wican_rxq")',
     '["B17_spec11_page_%s_man2.txt" % C], "wican_rxq")'),
    ("wican_man2 back among the images", '    "wican_man": "man", "wican_rt": "rt",',
     '    "wican_man": "man", "wican_man2": "man2", "wican_rt": "rt",'),
    ("a desk step reads a check that runs after it", 'desk("B2", "B2", ["man_app/B2app", "man_held/B2held"]',
     'desk("B2", "B2", ["man_app/B2app", "man_held/B9"]'),
    ("a placeholder no setting fills", '"REMOTE": "{gi_host_ssh}"', '"REMOTE": "{gi_hostssh}"'),
    ("the suite's role is not the one the bench lock names", 'ROLE = "tester (gen-inhibit)"',
     'ROLE = "tester (gen-inhibit-x)"'),
    ("the WiCAN's standing image is not _man", 'standing_images={"wican": "wican_man", "x2": "x2_app"}',
     'standing_images={"wican": "wican_rxq", "x2": "x2_app"}'),
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
    ("board step: the tool's exit is not looked at",
     'ok=rc == 0 and rec.get("verdict") == "PASS"', 'ok=rec.get("verdict") == "PASS"'),
    ("board step: the tool's verdict is not looked at",
     'ok=rc == 0 and rec.get("verdict") == "PASS"', 'ok=rc == 0'),
    ("board step: the X2 is rebooted too", '            if board != "wican":\n', '            if False:\n'),
    ("board step: --known carries the X2's images too",
     '                if im.board == "wican":\n                    known += [',
     '                if True:\n                    known += ['),
    ("board step: the start names a wanted image, not the first",
     'want = ["--first", image.name]', 'want = ["--want", image.name]'),
    ("board step: an end the core cannot name wants the standing image",
     'want = ["--want", image.name] if image is not None else ["--want-unknown"]',
     'want = ["--want", image.name if image is not None else "wican_man"]'),
    ("board step: no AP join first",
     "wican_sha()          # joins the WiCAN's AP when its page cannot be read", "pass"),
    ("board step: a page it cannot read stops the step before the tool",
     '            except IdentifyError:\n                pass                 # wican_reboot.py',
     '            except ZeroDivisionError:\n                pass                 # wican_reboot.py'),
    ("board step: the record is not named in what it found",
     'record=os.path.basename(rec_path))', 'record=None)'),
    ("board step: the tool's detail is dropped",
     'detail=rec.get("detail") or "wican_reboot.py rc %s" % rc, found=found)',
     'detail="wican_reboot.py rc %s" % rc, found=found)'),
    ("board step: the before state is not kept",
     'before_latched=(before.get("reasons") or {}).get("abort_latched"),', 'before_latched=None,'),
    ("board step: the script from the wrong folder",
     'os.path.join(harness, "wican_reboot.py")', 'os.path.join(tools, "wican_reboot.py")'),
    ("board step: the tool's files are not redacted",
     'rc = run(_py(settings) + [os.path.join(harness, "wican_reboot.py")',
     'rc = (lambda a, l, e, t: _run(a, l, e, root, t))(_py(settings) + [os.path.join(harness, "wican_reboot.py")'),
    ("board step: the suite has no start step",
     'on_start=hooks["on_start"], on_end=hooks["on_end"])', 'on_start=None, on_end=hooks["on_end"])'),
    ("board step: the suite's end step is the start step",
     'on_end=board_step("release"))', 'on_end=board_step("start"))'),
    ("an argv carries a local path", '["{gi_images}", "{gi_prod_dir}"] + step.split()', '[images_dir, prod_dir] + step.split()'),
    ("NONODE without a termination in the settings", '    need(settings, "gi_can1_termination")', '    settings.get("gi_can1_termination")'),
    ("NONODE's termination is a fixed text", 'env={"GI_TERMINATION": "{gi_can1_termination}",', 'env={"GI_TERMINATION": "unknown",'),
    ("NONODE's sha is the prod image's", '"GI_MAN_SHA": images["wican_man"].expect}', '"GI_MAN_SHA": images["wican_prod"].expect}'),
    ("NONODE is not judged", 'judged("NONODE"), ["NONODE_nonode_bench.txt"]', 'None, ["NONODE_nonode_bench.txt"]'),
    # the host-run commit (2026-10-22): H and B15host run this clone's harness
    ("H runs the SeaDrive copy", 'argv=["{bash}", "%s/baseline_host_run.sh" % HARNESS, C]',
     'argv=["{bash}", "projects/vtrux/notes/artifacts/gen-inhibit/baseline_host_run.sh", C]'),
    ("H gets no RROOT", ', "RROOT": "{gi_host_root}"}', '}'),
    ("B15host copies no pins", 'B15_FILES = ("diag_decode_check.py", "gi_diag.py", "pins.json")',
     'B15_FILES = ("diag_decode_check.py", "gi_diag.py")'),
    ("B15host runs the check from another folder", '"~/gi_bench_%s/diag_decode_check.py', '"~/gi_bench_%.0s/diag_decode_check.py'),
    ("B15host does not set REVERSE_IT_DIR", 'REVERSE_IT_DIR=\\"$PWD\\" python3 -B', 'python3 -B'),
    ("B15host's copy failing is not seen", '"set -o pipefail; (%s', '"(%s'),
    ("B15host runs the check after a failed copy", "tar xf - -C ~/gi_bench_%s' && \"", "tar xf - -C ~/gi_bench_%s'; \""),
]


def main():
    if not os.environ.get("GI_BASELINE_DIR"):
        print("ERROR GI_BASELINE_DIR is not set; the test cannot run, so no mutant can be killed")
        return 1
    base = gi_paths.scratch("mutants-suite_gen_inhibit")
    d = clone(base)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (env.get("PYTHONPATH"), HERE) if p)
    killed = errors = 0
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()

    def run(text):
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(text)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, capture_output=True, text=True,
                           env=env)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        return r, fails, (", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "")

    r, fails, crash = run(src)
    control_ok = r.returncode == 0 and not fails and not crash
    print("%s control: the unmutated copy  (exit %d, %d FAIL%s)%s" % (
        "PASS    " if control_ok else "FAILED  ", r.returncode, len(fails), crash,
        "".join("\n           " + f[:130] for f in fails[:3])))
    sys.stdout.flush()
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        r, fails, crash = run(src.replace(old, new))
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails), crash,
            ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


def clone(base):
    """base/repo: a shared clone of this worktree at HEAD, with this folder's test,
    settings example and bench/ copied over its copy of this folder. Returns that folder."""
    wt = gi_paths.worktree_of(HERE)
    head = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    repo = os.path.join(base, "repo")
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", wt, repo], check=True)
    subprocess.run(["git", "-C", repo, "-c", "advice.detachedHead=false", "checkout", "-q", head], check=True)
    d = os.path.join(repo, os.path.relpath(HERE, wt))
    for f in (TEST, EXAMPLE):
        shutil.copy(os.path.join(HERE, f), d)
    # The suite's HARNESS is bench/ beside it (2026-10-22); the e2e board-hook check
    # runs wican_reboot.py from there.
    shutil.rmtree(os.path.join(d, "bench"))
    shutil.copytree(os.path.join(HERE, "bench"), os.path.join(d, "bench"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    return d


if __name__ == "__main__":
    sys.exit(main())
