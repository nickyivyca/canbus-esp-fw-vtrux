"""Bench check of the harness's use of can-tools' bench-role layer, at a
hand take (charge-interposer tester, 2026-11-19, the charge-interposer
reviewer's bench-layer item (6)).

The harness reaches the bench adapters through canbench.bench_layer
(adapter_roles), behind shared/bench's bench guard (bench_lock.guard, tag
2026.11.19). This runs each piece the item changed on the real bench, in
one process under the session's hand take, and judges what it reads back:

  guard        the bench log's holder is this project's tester; the guard
               passes in this process (a descendant of the holder's pid),
               and refuses (NotHeld, not an ancestor) in a process started
               outside the holder's tree (a systemd --user service, so its
               parent is the user manager)
  kvaser link  kvaser_link.set_link(False) then set_link(True): the layer's
               link_down / link_up, each read back by the layer and again
               in sysfs (IFF_UP), up at 500000
  opens        the Kvaser (kvaser_link.open_bus), the PCAN (pcanfd, listen-
               only) and canchg (libusb) each opened through the harness's
               openers and read for 1 s (counted, not judged), then shut;
               the PCAN twice (the reviewer's PCAN item 2, 2026-11-20): with
               bus states raised (pcanfd: python-can's PcanBus, bench_vport's
               witness) and reported (pcanfd_report: can-tools'
               CanbenchPcanBus, every other PCAN reader), each the class its
               option gives
  canchg mode  canchg_mode.start (to_libusb, the run-long hold), canchg's
               open changing nothing, canchg_mode.end (to_socketcan: on
               gs_usb with a netdev, link down), then start again when it
               was found in libusb
  recover      kvaser_link.recover: link_down, canctl usbreset, link_up,
               each read back; the link up after it
  as found     canchg's mode and the Kvaser's link put back as found

Nothing is transmitted on either bus; the PCAN opens listen-only. The X2 and
the WiCAN are not touched.

Observations go to <out>/bench_layer_check.json and the judged lines to
<out>/bench_layer_check.txt. The judge works on the observations alone, so
the self-test runs it on made-up ones and touches nothing.

    python3.13 -B bench_layer_check.py --out DIR        (under a hand take)
    python3.13 -B bench_layer_check.py --self-test | --can-fail
Exit 0 PASS, 1 FAIL.
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths                                                 # noqa: E402

# charge-interposer/, found from paths.py rather than from this file, so a
# copy run by script_can_fail.py from its scratch folder finds it too
CODE = os.path.dirname(os.path.abspath(paths.__file__))
HOLDER = "tester (charge-interposer)"
BITRATE = 500000
LISTEN_S = 1.0
ROLES = ("canchg", "kvaser", "pcanfd")

# the guard in a process outside the holder's tree
OUTSIDE_CHILD = r'''
import os, sys
sys.path.insert(0, %r)
import adapter_roles
print("ppid %%d" %% os.getppid())
try:
    adapter_roles._guard()
    print("GUARD PASSED")
except Exception as e:
    print("GUARD REFUSED %%s: %%s" %% (type(e).__name__, e))
''' % CODE


def outside_argv(environ=None):
    """A systemd --user service (not a scope: its parent is the user
    manager, never this session) running OUTSIDE_CHILD, waited for, its
    output piped back."""
    env = os.environ if environ is None else environ
    sets = ["--setenv=%s=%s" % (k, env[k]) for k in ("REVERSE_IT_DIR", "CAN_TOOLS_DIR")
            if env.get(k)]
    return (["systemd-run", "--user", "--wait", "--pipe", "--quiet", "--collect",
             "--slice=bench.slice", "--working-directory=%s" % CODE] + sets
            + [sys.executable, "-B", "-c", OUTSIDE_CHILD])


def _try(fn):
    try:
        v = fn()
        return {"ok": True, "value": v if isinstance(v, (str, int, float, bool, list, dict,
                                                         type(None))) else repr(v)}
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def roles_now():
    """Where each role is now (read-only `canctl roles`, and the Kvaser's
    link from `ip` and sysfs)."""
    import adapter_roles
    import kvaser_link
    out = {}
    for role in ROLES:
        try:
            r = adapter_roles.resolve(role)
            out[role] = {"mode": r.mode, "driver": r.role.driver, "netdev": r.netdev,
                         "link": r.role.link}
        except Exception as e:                          # noqa: BLE001
            out[role] = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        out["kvaser"]["sysfs"] = kvaser_link.link_state()
        out["kvaser"]["bitrate"] = adapter_roles.layer().link_info(
            kvaser_link.netdev())["bitrate"]
    except Exception as e:                              # noqa: BLE001
        out["kvaser"]["sysfs_error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _listen(bus):
    n, t_end = 0, time.monotonic() + LISTEN_S
    try:
        while time.monotonic() < t_end:
            if bus.recv(timeout=0.05) is not None:
                n += 1
    finally:
        bus.shutdown()
    return n


def _listen_pcan(bus):
    """_listen for a PCAN open, with the class the open returned: python-
    can's PcanBus by default, can-tools' CanbenchPcanBus with
    bus_states="report" (the reviewer's PCAN item 2, 2026-11-20)."""
    cls = type(bus).__name__
    return {"frames": _listen(bus), "cls": cls}


def observe(out):
    import can
    import shared_pin
    shared_pin.use_bench()
    from vtrux_bench import bench_lock
    import adapter_roles
    import canchg_mode
    import kvaser_link
    obs = {"steps": []}
    h = bench_lock.read_state(bench_lock.default_log())["holder"]
    obs["holder"] = {"who": h and h["who"], "pid": h and h["pid"], "me": os.getpid()}
    obs["guard_here"] = _try(adapter_roles._guard)
    p = subprocess.run(outside_argv(), capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    obs["guard_outside"] = {"rc": p.returncode, "out": (p.stdout + p.stderr).strip()}
    obs["found"] = roles_now()

    def step(name, fn):
        r = _try(fn)
        r["name"], r["state"] = name, roles_now()
        obs["steps"].append(r)
        print("  %-14s %s" % (name, "ok" if r["ok"] else r["error"]), flush=True)
        return r

    step("kv_down", lambda: kvaser_link.set_link(False))
    step("kv_up", lambda: kvaser_link.set_link(True))
    step("kv_open", lambda: _listen(kvaser_link.open_bus(receive_own_messages=False)))
    step("pcan_open", lambda: _listen_pcan(adapter_roles.pcanfd(
        BITRATE, state=can.BusState.PASSIVE)))
    step("pcan_open_report", lambda: _listen_pcan(adapter_roles.pcanfd_report(
        BITRATE, state=can.BusState.PASSIVE)))
    step("chg_libusb", lambda: canchg_mode.start(out))
    step("chg_open", lambda: _listen(adapter_roles.canchg(BITRATE)))
    step("recover", lambda: list(kvaser_link.recover(environ={})))
    step("chg_socketcan", lambda: canchg_mode.end(out))
    found = obs["found"]
    if found.get("canchg", {}).get("mode") == "libusb":
        step("chg_back", lambda: canchg_mode.start(out))
    if found.get("kvaser", {}).get("sysfs") == "down":
        step("kv_back", lambda: kvaser_link.set_link(False))
    obs["end"] = roles_now()
    return obs


def judge(obs):
    """-> (lines, ok)."""
    lines, oks = [], []

    def say(cond, what):
        lines.append("%s  %s" % ("PASS" if cond else "FAIL", what))
        oks.append(bool(cond))

    st = {s["name"]: s for s in obs.get("steps", [])}

    def ok_step(name):
        s = st.get(name)
        if s is None or not s["ok"]:
            return False
        v = s.get("value")
        return not (isinstance(v, dict) and v.get("ok") is False) and not (
            isinstance(v, list) and v and v[0] is False)

    def state(name, role):
        return (st.get(name) or {}).get("state", {}).get(role, {})

    h = obs.get("holder", {})
    say(h.get("who") == HOLDER, "guard: the bench log's holder is %s (%s)" % (HOLDER, h))
    say(obs.get("guard_here", {}).get("ok") is True,
        "guard: passes in this process, a descendant of the holder's pid (%s)"
        % obs.get("guard_here"))
    go = obs.get("guard_outside", {}).get("out", "")
    say("GUARD REFUSED NotHeld" in go and "ancestor" in go and "GUARD PASSED" not in go,
        "guard: refuses outside the holder's tree (%s)" % go.replace("\n", " | "))
    kd, ku = state("kv_down", "kvaser"), state("kv_up", "kvaser")
    say(ok_step("kv_down") and kd.get("sysfs") == "down" and kd.get("link") != "up",
        "kvaser link: set_link(False) through the layer, down in sysfs too (%s; %s)"
        % ((st.get("kv_down") or {}).get("value") or (st.get("kv_down") or {}).get("error"), kd))
    say(ok_step("kv_up") and ku.get("sysfs") == "up" and ku.get("bitrate") == BITRATE,
        "kvaser link: set_link(True) through the layer, up at %d in sysfs and ip (%s; %s)"
        % (BITRATE, (st.get("kv_up") or {}).get("value") or (st.get("kv_up") or {}).get("error"),
           ku))
    for name, what, cls in (("kv_open", "the Kvaser (kvaser_link.open_bus)", None),
                            ("pcan_open", "the PCAN (pcanfd, listen-only, bus states "
                             "raised: python-can's PcanBus)", "PcanBus"),
                            ("pcan_open_report", "the PCAN (pcanfd_report, listen-only, "
                             "bus states reported: can-tools' CanbenchPcanBus)",
                             "Canbench" "PcanBus"),
                            ("chg_open", "canchg (libusb)", None)):
        s = st.get(name) or {}
        v = s.get("value")
        n = v.get("frames") if isinstance(v, dict) else v
        got = v.get("cls") if isinstance(v, dict) else None
        say(ok_step(name) and (cls is None or got == cls),
            "opens: %s opened, read %s s and shut (%s)"
            % (what, LISTEN_S, ("%s frame(s)%s" % (n, ", class %s" % got if cls else ""))
               if s.get("ok") else s.get("error")))
    cl = state("chg_libusb", "canchg")
    say(ok_step("chg_libusb") and cl.get("mode") == "libusb" and cl.get("driver") != "gs_usb",
        "canchg mode: canchg_mode.start (to_libusb) leaves it in libusb, off gs_usb (%s)" % cl)
    co = state("chg_open", "canchg")
    say(co.get("mode") == "libusb",
        "canchg mode: its open changed nothing and put nothing back (still libusb: %s)" % co)
    kr = state("recover", "kvaser")
    say(ok_step("recover") and kr.get("sysfs") == "up",
        "recover: link_down, usbreset, link_up through the layer, the link up after (%s)"
        % ((st.get("recover") or {}).get("value") or (st.get("recover") or {}).get("error")))
    cs = state("chg_socketcan", "canchg")
    say(ok_step("chg_socketcan") and cs.get("mode") == "socketcan"
        and cs.get("driver") == "gs_usb" and cs.get("netdev") and cs.get("link") != "up",
        "canchg mode: canchg_mode.end (to_socketcan) puts it on gs_usb with a netdev, link "
        "down (%s)" % cs)
    f, e = obs.get("found", {}), obs.get("end", {})
    say(f.get("canchg", {}).get("mode") == e.get("canchg", {}).get("mode")
        and f.get("kvaser", {}).get("sysfs") == e.get("kvaser", {}).get("sysfs")
        and all(ok_step(n) for n in ("chg_back", "kv_back") if n in st),
        "as found: canchg %s -> %s, the Kvaser's link %s -> %s"
        % (f.get("canchg", {}).get("mode"), e.get("canchg", {}).get("mode"),
           f.get("kvaser", {}).get("sysfs"), e.get("kvaser", {}).get("sysfs")))
    return lines, all(oks)


# --- self-test -----------------------------------------------------------------

def _good():
    def roles(chg="libusb", link="up", sysfs="up", driver=None, netdev=None):
        return {"canchg": {"mode": chg, "driver": driver if chg == "libusb" else "gs_usb",
                           "netdev": netdev if chg == "libusb" else "canchg", "link": "down"},
                "kvaser": {"mode": "socketcan", "driver": "kvaser_usb", "netdev": "kvaser",
                           "link": link, "sysfs": sysfs, "bitrate": BITRATE},
                "pcanfd": {"mode": "-", "driver": "pcan", "netdev": None, "link": None}}

    def s(name, value, **r):
        return {"name": name, "ok": True, "value": value, "state": roles(**r)}
    return {
        "holder": {"who": HOLDER, "pid": 100, "me": 200},
        "guard_here": {"ok": True, "value": None},
        "guard_outside": {"rc": 0, "out": "ppid 1234\nGUARD REFUSED NotHeld: the bench lock's "
                                          "holder tester (charge-interposer) has pid 100, "
                                          "which is not pid 300 or an ancestor (300 <- 1234)"},
        "found": roles(),
        "steps": [s("kv_down", "kvaser link down", link="down", sysfs="down"),
                  s("kv_up", "kvaser link up"),
                  s("kv_open", 12), s("pcan_open", {"frames": 30, "cls": "PcanBus"}),
                  s("pcan_open_report", {"frames": 31, "cls": "Canbench" "PcanBus"}),
                  s("chg_libusb", {"ok": True, "detail": "canchg: libusb mode"}),
                  s("chg_open", 0),
                  s("recover", [True, "kvaser recovered"]),
                  s("chg_socketcan", {"ok": True, "detail": "back on gs_usb"}, chg="socketcan"),
                  s("chg_back", {"ok": True, "detail": "canchg: libusb mode"})],
        "end": roles(),
    }


def self_test():
    fails = []

    def check(cond, what):
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails.append(what)

    lines, ok = judge(_good())
    check(ok and len(lines) == 14, "the good observations PASS, 14 lines: %s"
          % [ln for ln in lines if not ln.startswith("PASS")])

    def bad(what, edit):
        o = json.loads(json.dumps(_good()))
        edit(o)
        lines, ok = judge(o)
        check(not ok, "%s -> FAIL (%s)" % (what, [ln[:70] for ln in lines
                                                  if ln.startswith("FAIL")][:1]))

    def st(o, name):
        return [x for x in o["steps"] if x["name"] == name][0]

    def setp(name, **kv):
        def e(o):
            st(o, name).update(kv)
        return e

    def sets(name, role, **kv):
        def e(o):
            st(o, name)["state"][role].update(kv)
        return e
    bad("another role holds the bench", lambda o: o["holder"].update(who="tester (gen-inhibit)"))
    bad("the guard refused here", lambda o: o.update(guard_here={"ok": False, "error": "x"}))
    bad("the guard passed outside", lambda o: o["guard_outside"].update(
        out="ppid 1\nGUARD PASSED"))
    bad("outside refused for another reason", lambda o: o["guard_outside"].update(
        out="GUARD REFUSED NotHeld: the bench lock is free"))
    bad("outside never ran", lambda o: o["guard_outside"].update(out="Failed to start"))
    bad("link_down failed", setp("kv_down", ok=False, error="LinkError: x"))
    bad("link down but sysfs up", sets("kv_down", "kvaser", sysfs="up"))
    bad("link_up failed", setp("kv_up", ok=False, error="LinkError: x"))
    bad("link up at another bitrate", sets("kv_up", "kvaser", bitrate=250000))
    bad("link up but sysfs down", sets("kv_up", "kvaser", sysfs="down"))
    for n in ("kv_open", "pcan_open", "pcan_open_report", "chg_open"):
        bad("%s raised" % n, setp(n, ok=False, error="NotHeld: x"))
    # the reviewer's PCAN item 2: each PCAN open is the class its option gives
    bad("the bus-states-reported open is python-can's PcanBus",
        setp("pcan_open_report", value={"frames": 31, "cls": "PcanBus"}))
    bad("the default open is CanbenchPcanBus",
        setp("pcan_open", value={"frames": 30, "cls": "Canbench" "PcanBus"}))
    bad("the bus-states-reported open missing", lambda o: o.update(
        steps=[x for x in o["steps"] if x["name"] != "pcan_open_report"]))
    bad("to_libusb not ok", setp("chg_libusb", value={"ok": False, "detail": "x"}))
    bad("to_libusb left it on gs_usb", sets("chg_libusb", "canchg", driver="gs_usb"))
    bad("canchg's open moved it", sets("chg_open", "canchg", mode="socketcan"))
    bad("recover not ok", setp("recover", value=[False, "kvaser recovery FAILED"]))
    bad("recover left the link down", sets("recover", "kvaser", sysfs="down"))
    bad("to_socketcan not ok", setp("chg_socketcan", value={"ok": False, "detail": "x"}))
    bad("to_socketcan with no netdev", sets("chg_socketcan", "canchg", netdev=None))
    bad("to_socketcan left it off gs_usb", sets("chg_socketcan", "canchg", driver="usbfs"))
    bad("to_socketcan left its link up", sets("chg_socketcan", "canchg", link="up"))
    bad("canchg not put back", lambda o: o["end"]["canchg"].update(mode="socketcan"))
    bad("the Kvaser's link not put back", lambda o: o["end"]["kvaser"].update(sysfs="down"))
    bad("putting canchg back failed", setp("chg_back", value={"ok": False, "detail": "x"}))
    bad("a step missing", lambda o: o.update(steps=[x for x in o["steps"]
                                                    if x["name"] != "recover"]))
    check(judge({})[1] is False, "no observations -> FAIL")
    a = outside_argv({"REVERSE_IT_DIR": "/r", "CAN_TOOLS_DIR": "/c"})
    check(a[:3] == ["systemd-run", "--user", "--wait"] and "--scope" not in a
          and "--pipe" in a and "--setenv=REVERSE_IT_DIR=/r" in a
          and "--setenv=CAN_TOOLS_DIR=/c" in a and a[-1] == OUTSIDE_CHILD,
          "the outside guard runs as a systemd --user service (not a scope), waited for, "
          "piped, with REVERSE_IT_DIR and CAN_TOOLS_DIR: %s" % a[:9])
    check("adapter_roles._guard()" in OUTSIDE_CHILD and "GUARD REFUSED" in OUTSIDE_CHILD,
          "the outside child runs the harness's own guard and says how it ended")
    print("\nbench_layer_check self-test: %s"
          % ("all checks OK" if not fails else "%d FAILED" % len(fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out")
    g.add_argument("--self-test", action="store_true")
    g.add_argument("--can-fail", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.can_fail:
        import script_can_fail
        return script_can_fail.run(__file__, MUTANTS)
    os.makedirs(a.out, exist_ok=True)
    head = ["bench-layer check, %s" % time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "worktree commit %s" % subprocess.run(
                ["git", "-C", CODE, "rev-parse", "HEAD"], capture_output=True,
                text=True).stdout.strip()]
    obs = observe(a.out)
    with open(os.path.join(a.out, "bench_layer_check.json"), "w", newline="\n") as f:
        json.dump(obs, f, indent=1, sort_keys=True)
        f.write("\n")
    lines, ok = judge(obs)
    text = "\n".join(head + lines + ["RESULT: %s" % ("PASS" if ok else "FAIL")]) + "\n"
    print(text, end="")
    with open(os.path.join(a.out, "bench_layer_check.txt"), "w", newline="\n") as f:
        f.write(text)
    return 0 if ok else 1


MUTANTS = [
    ("any holder passes", "    say(h.get(\"who\") == HOLDER,", "    say(True,"),
    ("the guard here not looked at",
     "    say(obs.get(\"guard_here\", {}).get(\"ok\") is True,", "    say(True,"),
    ("the outside guard not looked at",
     "    say(\"GUARD REFUSED NotHeld\" in go and \"ancestor\" in go and \"GUARD PASSED\" not in go,",
     "    say(True,"),
    ("any outside refusal passes",
     "    say(\"GUARD REFUSED NotHeld\" in go and \"ancestor\" in go and \"GUARD PASSED\" not in go,",
     "    say(\"GUARD REFUSED\" in go,"),
    ("link down not read in sysfs",
     "    say(ok_step(\"kv_down\") and kd.get(\"sysfs\") == \"down\" and kd.get(\"link\") != \"up\",",
     "    say(ok_step(\"kv_down\"),"),
    ("link up's bitrate not compared",
     "    say(ok_step(\"kv_up\") and ku.get(\"sysfs\") == \"up\" and ku.get(\"bitrate\") == BITRATE,",
     "    say(ok_step(\"kv_up\") and ku.get(\"sysfs\") == \"up\","),
    ("a failed open passes", "        say(ok_step(name) and (cls is None or got == cls),",
     "        say(True,"),
    ("PCAN item 2: the open's class not checked",
     "        say(ok_step(name) and (cls is None or got == cls),",
     "        say(ok_step(name),"),
    ("a step's own not-ok ignored",
     "        return not (isinstance(v, dict) and v.get(\"ok\") is False) and not (",
     "        return True or not (isinstance(v, dict) and v.get(\"ok\") is False) and not ("),
    ("to_libusb's state not read",
     "    say(ok_step(\"chg_libusb\") and cl.get(\"mode\") == \"libusb\" and cl.get(\"driver\") != \"gs_usb\",",
     "    say(ok_step(\"chg_libusb\"),"),
    ("canchg's open may move it", "    say(co.get(\"mode\") == \"libusb\",", "    say(True,"),
    ("recover's link not read",
     "    say(ok_step(\"recover\") and kr.get(\"sysfs\") == \"up\",", "    say(ok_step(\"recover\"),"),
    ("to_socketcan's state not read",
     "    say(ok_step(\"chg_socketcan\") and cs.get(\"mode\") == \"socketcan\"\n"
     "        and cs.get(\"driver\") == \"gs_usb\" and cs.get(\"netdev\") and cs.get(\"link\") != \"up\",",
     "    say(ok_step(\"chg_socketcan\"),"),
    ("as found not compared",
     "    say(f.get(\"canchg\", {}).get(\"mode\") == e.get(\"canchg\", {}).get(\"mode\")\n"
     "        and f.get(\"kvaser\", {}).get(\"sysfs\") == e.get(\"kvaser\", {}).get(\"sysfs\")\n",
     "    say(True\n"),
    ("the outside guard in a scope (its parent this session)",
     "    return ([\"systemd-run\", \"--user\", \"--wait\", \"--pipe\",",
     "    return ([\"systemd-run\", \"--user\", \"--scope\", \"--wait\", \"--pipe\","),
]

if __name__ == "__main__":
    sys.exit(main())
