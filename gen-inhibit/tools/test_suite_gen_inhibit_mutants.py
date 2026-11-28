#!/usr/bin/env python3
"""Mutants of suite_gen_inhibit.py; test_suite_gen_inhibit.py must fail on each BY A
CHECK. A crash is not a kill, and an edit that does not apply is an error.

    GI_BASELINE_DIR=<.../runs/baseline_e3d9437> python3.13 -B test_suite_gen_inhibit_mutants.py

The core is the checkout firmware/shared_pins.json pins (bench/gi_core_pin.py; port step 4
item 1, 2026-11-25): the clone gets this worktree's pin file and, beside its root, a link
to that checkout.

The mutants run in a shared clone of this repo at HEAD, made in the worktree's scratch/,
so the copy sits where the suite does (gen-inhibit/tools/ since port step 0d) and
gi_diag finds the DBC's
git history from bench/ (B15host exports it; in a bare folder it found no history, and
every mutant failed the same six B15host checks). This folder's test, settings example,
x2_pin.py and x2_pin.json, and bench/ (the suite's HARNESS, 2026-10-22) are copied over
the clone's; each mutant
writes suite_gen_inhibit.py there with one edit, or, for a mutant whose fourth field
names it, the settings example (the placement mutants, core 2026.11.12; C2scan's; the
wallclock_patterns mutants, 2026-11-23). The unmutated copy runs first, as the
control: if it fails a check, no kill counts. Gen-inhibit tester, 2026-10-19.
"""
import os
import shutil
import subprocess
import sys
# no bytecode anywhere below this process: a child that imports the pinned core must not write
# __pycache__ into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

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
    # the Kvaser's link down after the X2 identify (gen-inhibit reviewer, 2026-11-18, condition 1)
    ("identify X2: the Kvaser left UP after bench_identify",
     "            kvaser_link_down(settings, logdir)\n", "            pass\n"),
    ("identify X2: the link down only when bench_identify succeeds",
     "        try:\n            got = BI.identify(image, ctx, logdir, bi_settings)\n        finally:\n",
     "        got = BI.identify(image, ctx, logdir, bi_settings)\n        if True:\n"),
    ("kvaser_link_down: a link still UP passes",
     '''        raise IdentifyError("after the X2's identify the Kvaser's link is not shown down: %s" % e)''',
     "        return None"),
    ("kvaser_link_down: nothing done on SocketCAN", '    if kb.backend() != "socketcan":\n        return None\n',
     '    if True:\n        return None\n'),
    ("kvaser_link_down: CANlib's closed channel brought down too", '    if kb.backend() != "socketcan":\n        return None\n', ''),
    ("kvaser_link_down: the read-back not written", '        json.dump({"after": "bench_identify", "link": got}, f, sort_keys=True)\n',
     '        pass\n'),
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
     'got = BI.identify(image, ctx, logdir, bi_settings)', 'got = None'),
    ("identify X2: no banner is the jammer",
     'return "jammer:banner" if jrec.get("banner_ok") else None', 'return "jammer:banner"'),
    # the jammer-ID reset (gen-inhibit reviewer, 2026-11-11)
    ("identify X2: a reset during the jammer's ID read is not looked at",
     'if jrec.get("reset_since_open") is True:', 'if False:'),
    ("identify X2: a jammer ID with no uptime raises as a reset",
     'if jrec.get("reset_since_open") is True:', 'if jrec.get("reset_since_open") is not False:'),
    # the WiCAN's OTA is the core's flasher from 2026-11-16 (core 2026.11.16.5, FLASH-SPEC O11):
    # its dual-slot rules' mutants are the core's own (O13); here, the suite's wiring of it
    ("ota: the core's flasher gets no identify (dual-slot refused)",
     "ota = OTA.make_flasher(identify=wican_sha, images=images)", "ota = OTA.make_flasher(images=images)"),
    ("ota: the core's flasher gets no images (no build to restore)",
     "ota = OTA.make_flasher(identify=wican_sha, images=images)", "ota = OTA.make_flasher(identify=wican_sha)"),
    ("ota: the WiCAN images' policy is not dual-slot", 'OTA_RETRY = "dual-slot"', 'OTA_RETRY = "none"'),
    ("join: identify's join is not made",
     "                    join_hotspot(settings)      # the backend settings.ota chooses (2026-11-16)\n",
     "                    pass\n"),
    ("join: settings.ota not read through the core's check",
     '        cfg = GW.config(settings.get("ota"))\n    except GW.Refused as e:\n        return False, "no join: %s" % e',
     '        cfg = dict(settings.get("ota") or {}, ssid="")\n    except GW.Refused as e:\n        return False, "no join: %s" % e'),
    ("tools: no GI_OTA_SETTINGS in a check's environment",
     '    env0.update({k: v.replace("{", "{{").replace("}", "}}") for k, v in ota_tools_env(settings, ssid).items()})\n',
     ""),
    ("hooks: GI_OTA_SETTINGS dropped from the hooks' environment (wican_reboot.py's rejoin), as before 2026-11-18",
     "    base_env.update(ota_tools_env(settings, ssid))\n", "    ota_tools_env(settings, ssid)\n"),
    ("tools: GI_OTA_SETTINGS' braces not doubled for the core's formatting",
     'env0.update({k: v.replace("{", "{{").replace("}", "}}") for k, v in ota_tools_env(settings, ssid)',
     'env0.update({k: v for k, v in ota_tools_env(settings, ssid)'),
    # no capture since topic (E): B21's outputs the client judge's (the gen-inhibit reviewer, answer to b07f7984)
    ("capture: B21 still hands its judge the wire join's files",
     '"B21_client_multipart_%s.txt" % C, "B21_client_image_%s.txt" % C], 3600),',
     '"B21_wire_multipart_%s.txt" % C, "B21_wire_image_%s.txt" % C], 3600),'),
    # F4 (2026-11-27): the RESPOND entry measurement
    ("F4: the arms left out of man_app",
     '            chk("F4arms", "baseline_b_hand.sh", ["F4"], None, timeout=3600),\n', ""),
    ("F4: the desk step reads the B3 arms' folder",
     'desk("F4", "F4", ["man_app/F4arms"], None,', 'desk("F4", "F4", ["man_app/B3arms"], None,'),
    ("F4: the arms judged as B3",
     'chk("F4arms", "baseline_b_hand.sh", ["F4"], None, timeout=3600)',
     'chk("F4arms", "baseline_b_hand.sh", ["F4"], judged("B3"), timeout=3600)'),
    ("F4: the desk step's profile not declared",
     'None, ["F4_respond_entry_profile.txt", "F4_mode_entry_analysis.txt"]),', 'None, ["F4_mode_entry_analysis.txt"]),'),
    ("capture: B11raw's arms skipped again",
     '        if judge:\n            kw["judge"] = judge\n        return Check(**kw)',
     '        if judge:\n            kw["judge"] = judge\n        if cid in ("B11rawarms", "B11raw", "B21"):\n'
     '            kw["skip"] = "no capture interface"\n        return Check(**kw)'),
    # the checks' tools get the esptool argv and the boards' IDs (run cb08; reviewer, 2026-11-19)
    ("tools: no GI_ESPTOOL_ARGV in a check's environment, as in run cb08",
     '    env0.update({k: v.replace("{", "{{").replace("}", "}}") for k, v in esptool_env(settings).items()})\n',
     ""),
    ("tools: no GI_X2_DEVICE / GI_WICAN_DEVICE in a check's environment, as in run cb08",
     '    env0.update({k: v.replace("{", "{{").replace("}", "}}") for k, v in serial_env(settings).items()})\n',
     ""),
    ("tools: the boards' IDs' braces not doubled for the core's formatting",
     'env0.update({k: v.replace("{", "{{").replace("}", "}}") for k, v in serial_env(settings)',
     'env0.update({k: v for k, v in serial_env(settings)'),
    ("tools: ssid_env's variable holding another SSID is not refused", "        if have and have != ssid:", "        if False:"),
    ("tools: another host is not refused", '    if (ota.get("host") or HOST) != HOST:', "    if False:"),
    ("tools: ssid_env's variable not filled", "        os.environ[var] = ssid\n", ""),
    ("bench suite: settings.ota not checked before a run",
     '        GW.config(settings.get("ota"))      # the core\'s own check of the block, before a run',
     "        pass"),
    ("bench suite: the interposer checkout not checked", "    interposer_code_at(settings)\n    pins_installed(settings)",
     "    pins_installed(settings)"),
    ("bench suite: a dirty interposer checkout accepted", 'if not (st["at"] and st["clean"]):', 'if not st["at"]:'),
    ("record: the interposer commit not recorded", 'commit=settings.get("interposer_code_commit"))}', "commit=None)}"),
    ("x2_hold: any pin state is a hold", 'ok = rc == 0 and "TX (GPIO7) not driven" in t', 'ok = rc == 0'),
    # the x2_pin move (2026-11-16): the jammer's archive row by the row route, then verify-row
    ("x2_jammer: verify-row's verdict is not read",
     'if rc != 0 or verdict != "PASS":\n            return dict(ok=False, phase="verify-row"',
     'if rc != 0 or not recs:\n            return dict(ok=False, phase="verify-row"'),
    ("x2_jammer: two verify-row records, the first taken",
     '        if len(recs) == 1:\n            with open(recs[0], encoding="utf-8") as f:\n                verdict = json.load(f).get("verdict")\n        if rc != 0 or verdict != "PASS":\n            return dict(ok=False, phase="verify-row"',
     '        if recs:\n            with open(recs[0], encoding="utf-8") as f:\n                verdict = json.load(f).get("verdict")\n        if rc != 0 or verdict != "PASS":\n            return dict(ok=False, phase="verify-row"'),
    ("x2_jammer: no --no-dump", '"flash-jammer", "--no-dump"] + row', '"flash-jammer"] + row'),
    ("x2_jammer: no verify-row", '        rc = run(py + [custody, "verify-row"] + row, vr, env, 900)\n', '        rc = 0\n'),
    ("x2_jammer: a verify-row FAIL allows more X2 flashes",
     '% (rc, len(recs), verdict), recoverable=False)', '% (rc, len(recs), verdict), recoverable=True)'),
    ("the jammer image is another variant of the row", '{"row": JAMMER_ROW, "variant": JAMMER_VARIANT}',
     '{"row": JAMMER_ROW, "variant": "stop_a_only"}'),
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
    ("a WiCAN image expects a short sha",
     'Image(name, "ota", {"bin": b, "retry": OTA_RETRY}, elf_sha256_of_bin(b), board="wican")',
     'Image(name, "ota", {"bin": b, "retry": OTA_RETRY}, elf_sha256_of_bin(b)[:8], board="wican")'),
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
    ("a placeholder no setting fills", '"FW": "{gi_fw}", "REMOTE": "{gi_host_ssh}"', '"FW": "{gi_fw}", "REMOTE": "{gi_hostssh}"'),
    ("the suite's role is not the one the bench lock names", 'ROLE = "tester (gen-inhibit)"',
     'ROLE = "tester (gen-inhibit-x)"'),
    ("the suite names a standing image again (retired 2026-11-11)",
     '        idle_safe_images=dict(IDLE_SAFE),\n',
     '        idle_safe_images=dict(IDLE_SAFE), standing_images={"wican": "wican_man", "x2": "x2_app"},\n'),
    ("x2_held is not marked not safe to leave idle", '        images[name].idle_safe = False\n',
     '        images[name].idle_safe = name == "x2_held"\n'),
    ("x2_jammer is not marked not safe to leave idle", '        images[name].idle_safe = False\n',
     '        images[name].idle_safe = name == "x2_jammer"\n'),
    ("the X2's idle-safe image is not x2_app", 'IDLE_SAFE = {"x2": "x2_app"}',
     'IDLE_SAFE = {"x2": "x2_slcan"}'),
    ("a WiCAN image is marked not safe to leave idle", 'IDLE_SAFE = {"x2": "x2_app"}',
     'IDLE_SAFE = {"x2": "x2_app", "wican": "wican_man"}\nIDLE_UNSAFE["wican_rt"] = "made up"'),
    ("a marked image is not marked on its Image",
     '        images[name].idle_safe = False\n', '        pass\n'),
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
    # gap 1, the pins check (2026-11-18)
    ("pins: the bench suite does not check", "    pins_installed(settings)                # before the bench", "    pass                # before the bench"),
    ("pins: a failing check passes", "        if rc != 0:\n            bad.append(", "        if False:\n            bad.append("),
    ("pins: canre not checked", 'PIN_CHECKS = (("canre", ("bench", "canre_pin.py")), ("can-tools", ("can_tools_pin.py",)))',
     'PIN_CHECKS = (("can-tools", ("can_tools_pin.py",)),)'),
    ("pins: can-tools not checked", 'PIN_CHECKS = (("canre", ("bench", "canre_pin.py")), ("can-tools", ("can_tools_pin.py",)))',
     'PIN_CHECKS = (("canre", ("bench", "canre_pin.py")),)'),
    ("pins: a tool that cannot run passes", '            rc, said = None, "could not run: %s" % e', '            rc, said = 0, ""'),
    ("board step: found_unknown is not kept (2026-11-18)",
     '("found_image", "found_sha", "found_unknown",', '("found_image", "found_sha",'),
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
    # B5_B8's jammer and WiCAN power (2026-11-16)
    ("B5_B8 without a WiCAN power in the settings", '    need(settings, "gi_wican_power")', '    settings.get("gi_wican_power")'),
    ("B5_B8's power is a fixed text", 'jammer_desc(images["x2_jammer"]), "GI_WICAN_POWER": "{gi_wican_power}"',
     'jammer_desc(images["x2_jammer"]), "GI_WICAN_POWER": "12 V on OBD pin 16, USB unplugged"'),
    ("the other checks get no GI_WICAN_POWER (2026-11-16)", '            "GI_WICAN_POWER": "{gi_wican_power}"}\n',
     '            }\n'),
    ("B5_B8's jammer is a fixed text", '"GI_JAMMER": jammer_desc(images["x2_jammer"])', '"GI_JAMMER": "x2_jammer a89ece8c"'),
    ("jammer_desc names the fixed row", '% (image.params["row"], image.params["variant"]))',
     '% (JAMMER_ROW, JAMMER_VARIANT))'),
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
    ("B15host's copy failing is not seen", '"set -o pipefail; (echo', '"(echo'),
    ("B15host runs the check after a failed copy", "tar xf - -C ~/gi_bench_%s' && \"", "tar xf - -C ~/gi_bench_%s'; \""),
    # B15host through host_target.sh (2026-11-12): local mode, the record line, BatchMode
    ("B15host: the copy still over ssh", 'diag_schemas | on_host "', 'diag_schemas | ssh -o BatchMode=yes {gi_host_ssh} "'),
    ("B15host: the check still over ssh", '"on_host \'cd {gi_host_root}', '"ssh -o BatchMode=yes {gi_host_ssh} \'cd {gi_host_root}'),
    ("B15host: no record line in its file", '(echo \\"# $HOST_MODE\\"; %s', '(%s'),
    ("B15host: no BatchMode", 'host_target || exit 2; HOST_SSH_BATCH=1; ', 'host_target || exit 2; '),
    ("B15host: REMOTE not given", 'env={"REMOTE": "{gi_host_ssh}"}, outputs=', 'outputs='),
    ("B15host: a REMOTE that is no host name runs anyway", '. \\"%s/host_target.sh\\" && host_target || exit 2;',
     '. \\"%s/host_target.sh\\" && host_target;'),
    # the X2's pinned images (user ruling 2026-11-10): the pin, x2_app first, the SLCAN verify
    ("the retired x2_images setting is accepted", 'if "x2_images" in settings:', 'if False:'),
    ("a setting names the pin file", 'pin = X2P.load(need(settings, "x2_pin_dir"), X2_PIN_FILE)',
     'pin = X2P.load(need(settings, "x2_pin_dir"), settings.get("x2_pin_file") or X2_PIN_FILE)'),
    ("the X2 images' app is not named (x2_slcan's verify has none)",'{"files": p["files"], "md5": p["md5"], "app": p["app"]}',
     '{"files": p["files"], "md5": p["md5"]}'),
    ("x2_hold: no x2_app first", '"""x2_app first (so a hold is a hold of x2_app), then x2_hold.py hold."""\n'
     '        bad = x2_app_first(settings_, logdir)', '"""x2_app first (so a hold is a hold of x2_app), then x2_hold.py hold."""\n'
     '        bad = None'),
    ("x2_jammer: no x2_app first", 'safe to leave idle)."""\n        bad = x2_app_first(settings_, logdir)\n        if bad:\n            return bad\n        env = dict(base_env, GI_RUNS_DIR=logdir)\n        py = _py(settings_)\n        custody',
     'safe to leave idle)."""\n        bad = None\n        if bad:\n            return bad\n        env = dict(base_env, GI_RUNS_DIR=logdir)\n        py = _py(settings_)\n        custody'),
    ("x2_app first: any read is taken as x2_app", '        if read == app.expect:\n', '        if read:\n'),
    ("x2_app first: a failed write goes on", 'if not res.get("ok"):\n            return dict(ok=False, phase="x2-app-first:%s"',
     'if False:\n            return dict(ok=False, phase="x2-app-first:%s"'),
    ("x2_app first: a failed write's recoverable is dropped", 'recoverable=res.get("recoverable"),', 'recoverable=True,'),
    ("x2_app first: not read back", '        if got != app.expect:\n            return dict(ok=False, phase="x2-app-first:identify"',
     '        if False:\n            return dict(ok=False, phase="x2-app-first:identify"'),
    ("x2_app first: an identify that could not look counts as x2_app",
     '            return dict(ok=False, phase="x2-app-first:identify", recoverable=True,\n'
     '                        detail="wrote x2_app first (the X2 read %s), then identify failed: %r" % (read, e))',
     '            got = app.expect'),
    ("identify: a failed X2 read leaves the older one", '        x2_last.pop("read", None)\n', '        pass\n'),
    ("identify: the X2 read is not kept", '        x2_last["read"] = got\n', '        pass\n'),
    ("identify: SLCAN answers need no verify", 'return got if x2_slcan_pinned(logdir, stamp) else got + ":unpinned"', 'return got'),
    ("identify: the verify checks every slcan file, not the app", 'x2_verify(settings, [sl.params["app"]],',
     'x2_verify(settings, sl.params["files"],'),
    ("verify: esptool's exit is not looked at", 'return rc == 0 and t.count(VERIFY_OK) == len(files)',
     'return t.count(VERIFY_OK) == len(files)'),
    ("verify: the digest line is not looked at", 'return rc == 0 and t.count(VERIFY_OK) == len(files)', 'return rc == 0'),
    ("verify: a board it could not reach is a mismatch", 'if PORT_RX.search(t) or "Failed to connect" in t:', 'if False:'),
    # esptool (gi_esptool.py; gen-inhibit reviewer, 2026-11-11)
    ("verify: the dashed command name back", '"verify_flash"]', '"verify-flash"]'),
    ("verify: the version is not checked", '        ver = GE.check_version(argv0)\n', '        ver = GE.PINNED\n'),
    ("verify: the argv's form is not checked", '        argv0 = GE.check_form(argv0)\n', '        argv0 = list(argv0)\n'),
    ("verify: esptool 5's line counts", 'VERIFY_OK = GE.VERIFY_OK',
     'VERIFY_OK = "Verification successful (digest matched)"'),
    ("verify: the version is not in the log", 'f.write("esptool version: %s (pinned %s)\\n%s" % (ver, GE.PINNED, t))',
     'f.write(t)'),
    ("the tools get no GI_ESPTOOL_ARGV", '    base_env.update(esptool_env(settings))', '    pass'),
    # the X2's port by its USB IDs (DEVICES-SPEC S1; gen-inhibit reviewer, 2026-11-12)
    ("the tools get no GI_X2_DEVICE / GI_WICAN_DEVICE", '    base_env.update(serial_env(settings))', '    pass'),
    ("verify: on a tty name, not the X2's IDs",
     '    port = x2_port(settings, os.path.dirname(os.path.abspath(logpath)), "x2_verify")\n',
     '    port = et.get("port") or "/dev/ttyACM0"\n'),
    ("bench_identify gets the settings as they are",
     '        bi_settings = dict(settings, serial_port=x2_port(settings, logdir, "bench_identify"))\n',
     '        bi_settings = settings\n'),
    ("the port's use is not written down", 'f.write("used by: %s\\nport: %s\\n" % (X2_PORT_USES[use], port))',
     'f.write("port: %s\\n" % port)'),
    ("code: the X2 files' sha256 are not kept", 'sha256=dict(pin["images"][n]["sha256"]))', 'sha256={})'),
    # placement (core 2026.11.12; gen-inhibit reviewer, 2026-11-12): the settings example's
    # slices (a bench group with images and no lock is the core's validate's to refuse)
    ("example: no placement",
     '  "placement": {"slices": {"lock": "bench.slice", "wallclock": "bench.slice",\n'
     '                           "build": "build.slice", "light": "build.slice"}},\n', '', EXAMPLE),
    ("example: wall-clock checks in build.slice", '"wallclock": "bench.slice"', '"wallclock": "build.slice"', EXAMPLE),
    ("example: no lock slice", '"lock": "bench.slice", ', '', EXAMPLE),
    ("example: light checks in bench.slice", '"light": "build.slice"', '"light": "bench.slice"', EXAMPLE),
    # C2scan through c_checks_run.py (item 1, 2026-11-16)
    ("C2scan: the .ps1 again", '"%s/c_checks_run.py" % HARNESS, "--clone"', '"%s/c_checks_run.ps1" % HARNESS, "--clone"'),
    ("C2scan: not under the host's python", 'python=True, cwd="{reverse_it}", timeout_s=7200, cls="build",',
     'cwd="{reverse_it}", timeout_s=7200, cls="build",'),
    # wallclock_patterns (gen-inhibit reviewer, 2026-11-23: bench run 4b15's events named no
    # other wall-clock test, the example's list being empty). Since port step 4 item 1
    # (2026-11-25) the example gives none and the core's default list is used.
    ("example: wallclock_patterns empty again", '  "placement": {"slices"',
     '  "wallclock_patterns": [],\n  "placement": {"slices"', EXAMPLE),
    ("example: an explicit list without the Android emulator", '  "placement": {"slices"',
     '  "wallclock_patterns": ["run_scenario\\\\.py", "bench_[a-z0-9_]+\\\\.py"],\n  "placement": {"slices"',
     EXAMPLE),
    # the core from the pin, not PYTHONPATH (port step 4 item 1, 2026-11-25)
    ("the record keeps no core pin", '"gi_tools": git_state(need(settings, "gi_tools")), "core": core_pin(),',
     '"gi_tools": git_state(need(settings, "gi_tools")),'),
    ("the record's core pin without the digest", 'for k in ("tag", "commit", "digest", "folder")}',
     'for k in ("tag", "commit", "folder")}'),
    ("example: the core on the host PYTHONPATH again", '"PYTHONPATH": "<interposer checkout>/charge-interposer"',
     '"PYTHONPATH": "<core 2026.11.23.5>/shared/bench:<interposer checkout>/charge-interposer"', EXAMPLE),
    # No "replayer left out" mutant since port step 4 item 1: the example has no list to
    # edit, and an explicit list of any kind is the mutant above. The replayer's pattern is
    # the core's (procs.DEFAULT_WALLCLOCK_PATTERNS), and its own tests carry it.
    ("example: no IDF_TOOLS_PATH for C2scan", ',\n              "IDF_TOOLS_PATH": "<that install\'s tools folder>"', '',
     EXAMPLE),
    # F5 (2026-11-27): the probe's no-acknowledge modes
    ("F5: the X2 on its app, so it may ACK", 'Group("txabort_held", needs_lock=True, image=["wican_txabort", "x2_held"]',
     'Group("txabort_held", needs_lock=True, image=["wican_txabort", "x2_app"]'),
    ("F5: run on _man", 'Group("txabort_held", needs_lock=True, image=["wican_txabort", "x2_held"]',
     'Group("txabort_held", needs_lock=True, image=["wican_man", "x2_held"]'),
    ("F5: its output not declared", '["F5"], None, ["F5_unacked_bench.txt"], 1800, "wican_txabort")',
     '["F5"], None, [], 1800, "wican_txabort")'),
    ("F5: the hand step NONODE instead", 'chk("F5unacked", "baseline_b_hand.sh", ["F5"]',
     'chk("F5unacked", "baseline_b_hand.sh", ["NONODE"]'),
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
    srcs = {t: open(os.path.join(HERE, t), encoding="utf-8", newline="").read() for t in (SRC, EXAMPLE)}
    src = srcs[SRC]

    def run(text, target=SRC):
        """The test, with target holding text and the other file unmutated."""
        for t in (SRC, EXAMPLE):
            open(os.path.join(d, t), "w", encoding="utf-8", newline="").write(text if t == target else srcs[t])
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
    for n, (name, old, new, *tgt) in enumerate(MUTANTS, 1):
        target = tgt[0] if tgt else SRC
        if srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        r, fails, crash = run(srcs[target].replace(old, new), target)
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails), crash,
            ("; " + fails[0][:100]) if fails else ""))
        sys.stdout.flush()
    gi_paths.remove_tree(base)
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
    # the X2's pin, which the suite imports (2026-11-10); the judges, which the test reads
    # (2026-11-12: at HEAD they lagged an uncommitted change, and the control failed on it)
    for f in (TEST, EXAMPLE, "x2_pin.py", "x2_pin.json", "suite_judges.py"):
        shutil.copy(os.path.join(HERE, f), d)
    # The suite's HARNESS is bench/ beside it (2026-10-22); the e2e board-hook check
    # runs wican_reboot.py from there.
    shutil.rmtree(os.path.join(d, "bench"))
    shutil.copytree(os.path.join(HERE, "bench"), os.path.join(d, "bench"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    # The core (port step 4 item 1, 2026-11-25): the pin file as this worktree has it, and
    # the pinned checkout beside the clone's root, as a link to the one this worktree's pin
    # names and gi_core_pin has checked, so the copy's gi_core_pin finds and checks it.
    pins = os.path.join("gen-inhibit", "firmware", "shared_pins.json")
    shutil.copy(os.path.join(wt, pins), os.path.join(repo, pins))
    import gi_core_pin
    c = gi_core_pin.check()
    os.symlink(os.path.dirname(os.path.dirname(c["folder"])), os.path.join(base, "shared-" + c["tag"]))
    return d


if __name__ == "__main__":
    sys.exit(main())
