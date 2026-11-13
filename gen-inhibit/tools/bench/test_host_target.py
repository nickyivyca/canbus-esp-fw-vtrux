#!/usr/bin/env python3
"""Test host_target.sh, the local mode of baseline_host_run.sh and gi_host_run.sh.

The gen-inhibit reviewer approved it on 2026-11-12: madhouse-debian, the bench host and
both scripts' default REMOTE, cannot ssh to itself, so when REMOTE names this host the
same commands run here without ssh, and the record says local mode and names the host,
so a run here never reads as a remote one.

  1. host_target, sourced in a real bash: REMOTE as this host's name (as hostname gives
     it, upper-cased, short and full), localhost, 127.0.0.1, ::1 and this user@this host
     are local; another user at this host, another host, and a name with this host's
     name as its prefix are remote; an empty REMOTE and one with shell characters are
     refused (exit 2). The record line says local mode, REMOTE and this host's name, or
     ssh and REMOTE.
  2. on_host, local: runs from $HOME, with an environment of only HOME, USER, LOGNAME,
     PATH and LANG (a marker and REVERSE_IT_DIR exported here don't reach it), passes
     stdin through and returns the command's exit status; a fake ssh first on PATH is
     never called. Remote: the fake ssh is called with REMOTE and the command, and its
     status comes back.
  3. baseline_host_run.sh, run twice on an old commit (no gi_diag, quick): REMOTE
     another host with the fake ssh, then REMOTE this host with HOST_BASH a fake shell.
     Each records every command it is given, its stdin, its folder and its environment.
     Both runs exit 0 and give the same commands in the same order, and the same remote
     script on "bash -s"; the local run never calls ssh, runs each command from $HOME
     without REVERSE_IT_DIR; host_run.log's first line and the script's first output
     line say local mode and name this host, or remote and ssh.
  4. gi_host_run.sh --sched, the same two ways (LOCAL_PY a fake for the gi_diag export
     and wican_ref.py, the shared checkout a fake): the same commands, the sync's archive
     the same, no ssh in local mode, and the first output line says which.

    python3.13 -B gen-inhibit/tools/bench/test_host_target.py
    python3.13 -B gen-inhibit/tools/bench/test_host_target.py --mutants

--mutants runs the test on copies of the three scripts with one edit each (GI_HOST_TARGET_DIR
names the folder of copies). The unmutated copy runs first, as the control. Gen-inhibit
tester, 2026-11-12.
"""
import os
import shutil
import socket
import subprocess
import sys
import gi_paths  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("GI_HOST_TARGET_DIR") or HERE
FW = gi_paths.project_root()
BASH = shutil.which("bash")
OLD_COMMIT = os.environ.get("GI_TEST_COMMIT", "2a1a0f1")    # root layout, no gi_diag: quick to stage
FILES = ("host_target.sh", "baseline_host_run.sh", "gi_host_run.sh", "host_identity.sh")
FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %-78s %s" % ("ok" if ok else "FAIL", name, "" if ok else repr(detail)[:240]))
    if not ok:
        FAILS.append(name)


def script(path, body):
    with open(path, "w", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\n" + body)
    os.chmod(path, 0o755)


def recorder(path, calls, kind):
    """A fake ssh (kind "ssh": $1 the host, $2 the command) or shell (kind "shell": $1 is -c,
    $2 the command). Paths are written in, since the local mode's env -i drops the test's
    variables. Each call n leaves n.cmd, n.host, n.cwd, n.env and n.in; "bash -s" answers as
    the remote script's first step would."""
    os.makedirs(calls, exist_ok=True)
    script(path, 'd=%s; n=$(ls "$d" | grep -c "\\.cmd$")\n'
                 'printf %%s "$2" > "$d/$n.cmd"; printf %%s "%s:$1" > "$d/$n.host"\n'
                 'pwd > "$d/$n.cwd"; env | sort > "$d/$n.env"; cat > "$d/$n.in"\n'
                 'case "$2" in "bash -s") echo "=== exit 0 (fake)" ;; esac\n'
                 'exit "${FAKE_RC:-0}"\n' % (shlex_q(calls), kind))


def shlex_q(s):
    import shlex
    return shlex.quote(s)


def calls_of(calls):
    if not os.path.isdir(calls):
        return []
    n = len([f for f in os.listdir(calls) if f.endswith(".cmd")])
    out = []
    for i in range(n):
        rd = lambda ext, mode="r": open(os.path.join(calls, "%d.%s" % (i, ext)), mode).read()  # noqa: E731
        out.append({"cmd": rd("cmd"), "host": rd("host"), "cwd": rd("cwd").strip(), "env": rd("env"),
                    "in": rd("in", "rb")})
    return out


def sh(body, env, stdin=None):
    r = subprocess.run([BASH, "-c", body], capture_output=True, text=True, env=env, timeout=60,
                       input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def names_here():
    me = socket.gethostname()
    out = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip() or me
    short = subprocess.run(["hostname", "-s"], capture_output=True, text=True).stdout.strip()
    full = subprocess.run(["hostname", "-f"], capture_output=True, text=True).stdout.strip()
    return out, short, full


def target(remote, base_env):
    q = shlex_q(os.path.join(DIR, "host_target.sh"))
    rc, out, err = sh('. %s; REMOTE=%s; host_target; rc=$?; printf "%%s|%%s|%%s" "$rc" "${HOST_LOCAL-UNSET}" '
                      '"${HOST_MODE-UNSET}"' % (q, shlex_q(remote)), base_env)
    parts = out.split("|", 2)
    return (parts + ["", "", ""])[:3] if len(parts) == 3 else ["?", "?", out + err]


def part_target(base_env, me, short, full):
    print("1. host_target")
    user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    for label, remote in (("this host as hostname gives it", me), ("upper-cased", me.upper()),
                          ("short", short or me), ("full", full or me), ("localhost", "localhost"),
                          ("127.0.0.1", "127.0.0.1"), ("::1", "::1"), ("this user at this host", "%s@%s" % (user, me))):
        rc, local, mode = target(remote, base_env)
        check("REMOTE=%s (%s) is local" % (remote.replace(user + "@", "<user>@"), label),
              rc == "0" and local == "1", (rc, local, mode))
    rc, local, mode = target(me, base_env)
    check("the local record says local mode, REMOTE and this host, and without ssh",
          mode == "local mode: REMOTE=%s names this host, %s; the commands run here without ssh" % (me, me), mode)
    for label, remote in (("another user at this host", "not-%s@%s" % (user, me)),
                          ("another host", "nowhere.invalid"), ("this host's name as a prefix", me + "x"),
                          ("a host with this host's name in it", "x" + me)):
        rc, local, mode = target(remote, base_env)
        check("REMOTE=%s (%s) is remote" % (remote.replace(user + "@", "<user>@"), label),
              rc == "0" and local == "0", (rc, local, mode))
    rc, local, mode = target("nowhere.invalid", base_env)
    check("the remote record says ssh and REMOTE, and this host", mode == "remote: ssh nowhere.invalid, from %s" % me,
          mode)
    for label, remote in (("empty", ""), ("a space", "a b"), ("a semicolon", "a;b"), ("a $()", "$(id)"),
                          ("a quote", 'a"b')):
        rc, local, mode = target(remote, base_env)
        check("REMOTE with %s is refused (2), nothing set" % label, rc == "2" and local == "UNSET", (rc, local, mode))


def part_on_host(base, base_env, me):
    print("2. on_host")
    bindir = os.path.join(base, "bin2")
    os.makedirs(bindir)
    calls = os.path.join(base, "calls2")
    recorder(os.path.join(bindir, "ssh"), calls, "ssh")
    home = os.path.join(base, "home2")
    os.makedirs(home)
    e = dict(base_env, PATH=bindir + os.pathsep + base_env["PATH"], HOME=home, MARKER_FROM_HERE="1",
             REVERSE_IT_DIR="/from/here")
    q = shlex_q(os.path.join(DIR, "host_target.sh"))
    pre = '. %s; REMOTE=%s; host_target || exit 9; ' % (q, shlex_q(me))
    rc, out, err = sh(pre + 'on_host "pwd; env | sort"', e)
    lines = out.splitlines()
    check("local: the command runs from $HOME", rc == 0 and lines[:1] == [home], (rc, lines[:1], err[-200:]))
    names = sorted(l.split("=", 1)[0] for l in lines[1:] if "=" in l)
    extra = [n for n in names if n not in ("HOME", "USER", "LOGNAME", "PATH", "LANG", "PWD", "SHLVL", "_")]
    check("local: its environment holds only HOME, USER, LOGNAME, PATH, LANG (and bash's own)", not extra,
          extra)
    check("local: REVERSE_IT_DIR and a marker exported here don't reach it",
          "REVERSE_IT_DIR" not in names and "MARKER_FROM_HERE" not in names, names)
    rc, out, err = sh(pre + 'on_host "tr a-z A-Z"', e, stdin="through stdin\n")
    check("local: stdin is passed through", out == "THROUGH STDIN\n", (rc, out, err[-200:]))
    rc, out, err = sh(pre + 'on_host "exit 7"', e)
    check("local: the command's exit status comes back", rc == 7, rc)
    check("local: ssh is never called", calls_of(calls) == [], len(calls_of(calls)))
    pre_r = '. %s; REMOTE=nowhere.invalid; host_target || exit 9; ' % q
    rc, out, err = sh(pre_r + 'on_host "echo remote command"', dict(e, FAKE_RC="5"))
    got = calls_of(calls)
    check("remote: ssh is called with REMOTE and the command, and its status comes back",
          rc == 5 and len(got) == 1 and got[0]["host"] == "ssh:nowhere.invalid"
          and got[0]["cmd"] == "echo remote command", (rc, got[:1]))


def run_twice(base, tag, argv, env_extra, me):
    """Run argv remote (fake ssh) and local (fake shell): {mode: (rc, out, ssh calls, shell calls, home)}."""
    res = {}
    for mode in ("remote", "local"):
        d = os.path.join(base, "%s_%s" % (tag, mode))
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        ssh_calls, sh_calls = os.path.join(d, "ssh_calls"), os.path.join(d, "shell_calls")
        recorder(os.path.join(bindir, "ssh"), ssh_calls, "ssh")
        recorder(os.path.join(bindir, "fakeshell"), sh_calls, "shell")
        home = os.path.join(d, "home")
        os.makedirs(home)
        e = {k: v for k, v in os.environ.items() if k not in ("REMOTE", "FW", "DEST", "PY", "RROOT", "HOST_BASH")}
        e.update(env_extra(d))
        e.update(PATH=bindir + os.pathsep + e.get("PATH", ""), HOME=home, HOST_BASH="fakeshell",
                 REMOTE=me if mode == "local" else "nowhere.invalid", REVERSE_IT_DIR=e.get("REVERSE_IT_DIR", "/x"))
        r = subprocess.run([BASH] + argv, capture_output=True, text=True, env=e, timeout=300, stdin=subprocess.DEVNULL)
        res[mode] = (r.returncode, r.stdout + r.stderr, calls_of(ssh_calls), calls_of(sh_calls), home, d)
    return res


def compare(label, res, me):
    (rc_r, out_r, ssh_r, sh_r, _, _), (rc_l, out_l, ssh_l, sh_l, home, _) = res["remote"], res["local"]
    check("%s remote: exit 0, every command over ssh, none to the shell" % label,
          rc_r == 0 and ssh_r and not sh_r, (rc_r, out_r[-300:], len(ssh_r), len(sh_r)))
    check("%s local: exit 0, ssh never called" % label, rc_l == 0 and not ssh_l and sh_l,
          (rc_l, out_l[-300:], len(ssh_l), len(sh_l)))
    check("%s: the same commands, in the same order, both ways" % label,
          [c["cmd"] for c in ssh_r] == [c["cmd"] for c in sh_l] and len(sh_l) >= 3,
          ([c["cmd"][:60] for c in ssh_r], [c["cmd"][:60] for c in sh_l]))
    check("%s: the same stdin to each command both ways (the archive, the remote script)" % label,
          [c["in"] for c in ssh_r] == [c["in"] for c in sh_l] or _same_but_tar(ssh_r, sh_l),
          [(len(a["in"]), len(b["in"])) for a, b in zip(ssh_r, sh_l)])
    check("%s local: every command runs from $HOME" % label, all(c["cwd"] == home for c in sh_l),
          sorted(set(c["cwd"] for c in sh_l)))
    check("%s local: no command sees REVERSE_IT_DIR from here" % label,
          not any("\nREVERSE_IT_DIR=" in "\n" + c["env"] for c in sh_l), "")
    first_r = out_r.splitlines()[:1]
    first_l = out_l.splitlines()[:1]
    check("%s: the first output line says remote, ssh and REMOTE" % label,
          bool(first_r) and "remote: ssh nowhere.invalid, from %s" % me in first_r[0], first_r)
    check("%s: the first output line says local mode and names this host" % label,
          bool(first_l) and "local mode: REMOTE=%s names this host, %s;" % (me, me) in first_l[0], first_l)


def _same_but_tar(a, b):
    """gzip stamps the time into an archive: the sync's .tgz differs between two runs, so a
    gzip member is compared unpacked."""
    import gzip
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x["in"] == y["in"]:
            continue
        try:
            if gzip.decompress(x["in"]) != gzip.decompress(y["in"]):
                return False
        except OSError:
            return False
    return True


def part_baseline(base, me):
    print("3. baseline_host_run.sh, remote and local")
    res = run_twice(base, "baseline", [os.path.join(DIR, "baseline_host_run.sh"), OLD_COMMIT],
                    lambda d: {"FW": FW, "RROOT": "~/rit", "GI_OUT": os.path.join(d, "out"), "PY": "PYTHON"}, me)
    compare("baseline", res, me)
    for mode, want in (("remote", "# remote: ssh nowhere.invalid, from %s" % me),
                       ("local", "# local mode: REMOTE=%s names this host, %s;" % (me, me))):
        log = os.path.join(res[mode][5], "out", "host_run.log")
        first = open(log).read().splitlines()[:2] if os.path.exists(log) else []
        check("baseline %s: host_run.log's first line says so (%s)" % (mode, want[2:20]),
              bool(first) and first[0].startswith(want) and first[1:] == ["=== exit 0 (fake)"], first)


def part_gi_host(base, me):
    print("4. gi_host_run.sh --sched, remote and local")
    shared = os.path.join(base, "sharedco")
    for comp in ("wican", "can_port", "twai_port"):
        os.makedirs(os.path.join(shared, "shared", comp))
        open(os.path.join(shared, "shared", comp, "README.md"), "w").close()

    def extra(d):
        b = os.path.join(d, "pybin")
        os.makedirs(b)
        script(os.path.join(b, "fakepy"), 'case "${@: -1}" in wican_ref.py) printf "%%s\\n" %s ;; '
                                          '*) mkdir -p "${@: -1}" ;; esac\n'
               % shlex_q(os.path.join(shared, "shared", "wican")))
        return {"FW": FW, "LOCAL_PY": os.path.join(b, "fakepy"), "PY": "PYTHON"}
    res = run_twice(base, "gihost", [os.path.join(DIR, "gi_host_run.sh"), "--sched"], extra, me)
    compare("gi_host_run --sched", res, me)


def checks():
    base = gi_paths.scratch("test_host_target")
    try:
        me, short, full = names_here()
        base_env = {k: v for k, v in os.environ.items() if k not in ("REMOTE", "HOST_LOCAL", "HOST_MODE", "HOST_BASH")}
        part_target(base_env, me, short, full)
        part_on_host(base, base_env, me)
        part_baseline(base, me)
        part_gi_host(base, me)
    finally:
        gi_paths.remove_tree(base)
    return FAILS


MUTANTS = [
    ("host_target.sh", "any user at this host is local",
     '  if [ -z "$user" ] || [ "$user" = "$(id -un)" ]; then\n', "  if true; then\n"),
    ("host_target.sh", "the case matters", '[ "${host,,}" = "${n,,}" ]', '[ "$host" = "$n" ]'),
    ("host_target.sh", "a prefix of a name is local", '[ "${host,,}" = "${n,,}" ]', '[[ "${n,,}" == "${host,,}"* ]]'),
    ("host_target.sh", "localhost is not local", " localhost 127.0.0.1 ::1; do", " 127.0.0.1 ::1; do"),
    ("host_target.sh", "never local", "HOST_LOCAL=1; break;", "HOST_LOCAL=0; break;"),
    ("host_target.sh", "always local", "  HOST_LOCAL=0\n", "  HOST_LOCAL=1\n"),
    ("host_target.sh", "REMOTE not checked", '    "" | *[!A-Za-z0-9@._:-]*)\n', '    "")\n'),
    ("host_target.sh", "the local record reads as remote",
     'HOST_MODE="local mode: REMOTE=$REMOTE names this host, $me;', 'HOST_MODE="remote: REMOTE=$REMOTE names this host, $me;'),
    ("host_target.sh", "the local record names no host", "names this host, $me; the commands", "names this host; the commands"),
    ("host_target.sh", "local runs in this shell's folder", '(cd "$HOME" && env -i', "(env -i"),
    ("host_target.sh", "local keeps this shell's environment",
     'env -i HOME="$HOME" USER="${USER:-}" LOGNAME="${LOGNAME:-}" PATH="$PATH" \\\n      LANG="${LANG:-C.UTF-8}" ', ""),
    ("host_target.sh", "local goes over ssh anyway", '  if [ "${HOST_LOCAL:-0}" = 1 ]; then', "  if false; then"),
    ("baseline_host_run.sh", "baseline: the log's record line dropped", 'echo "# $HOST_MODE" > "$OUT/host_run.log"\n', ""),
    ("baseline_host_run.sh", "baseline: the log written over the record",
     'on_host "bash -s" >> "$OUT/host_run.log"', 'on_host "bash -s" > "$OUT/host_run.log"'),
    ("baseline_host_run.sh", "baseline: no record on its output", 'host_target || exit 2\necho "$HOST_MODE"\n',
     "host_target || exit 2\n"),
    ("baseline_host_run.sh", "baseline: the copy still over ssh", '. | on_host "rm -rf', '. | ssh "$REMOTE" "rm -rf'),
    ("baseline_host_run.sh", "baseline: the traces still over ssh", 'on_host "cd ~/$DEST/test/gen_inhibit_host && tar',
     'ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_host && tar'),
    ("gi_host_run.sh", "gi_host_run: no record", 'echo "--- $HOST_MODE"\n', ""),
    ("gi_host_run.sh", "gi_host_run: the sync still over ssh", '  | on_host "mkdir -p ~/$DEST', '  | ssh "$REMOTE" "mkdir -p ~/$DEST'),
    ("gi_host_run.sh", "gi_host_run: the host identity still over ssh", 'on_host "bash -s" < "$(dirname',
     'ssh "$REMOTE" "bash -s" < "$(dirname'),
    ("gi_host_run.sh", "gi_host_run: --sched still over ssh", '    on_host "cd ~/$DEST/test/gen_inhibit_sched',
     '    ssh "$REMOTE" "cd ~/$DEST/test/gen_inhibit_sched'),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        got = checks()
        print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
        return 1 if got else 0
    base = gi_paths.scratch("test_host_target_mutants")
    killed = errors = 0
    control_ok = False
    try:
        for n, (f, name, old, new) in enumerate([(None, "the control: unmutated copies", None, None)] + MUTANTS):
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            for x in FILES:
                shutil.copy(os.path.join(HERE, x), d)
            if f is not None:
                src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
                if src.count(old) != 1:
                    print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                    errors += 1
                    continue
                open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                               env=dict(os.environ, GI_HOST_TARGET_DIR=d), timeout=900)
            fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            crashed = "Traceback" in r.stderr
            if f is None:
                control_ok = r.returncode == 0 and not fails and not crashed
                print("%s m0 %s%s" % ("PASS    " if control_ok else "FAILED  ", name,
                                      "".join("\n           " + x[:130] for x in fails[:3]) + r.stderr[-300:]))
                if not control_ok:
                    break
                continue
            dead = bool(fails) and not crashed
            killed += dead
            print("%s m%d %s%s" % ("KILLED  " if dead else ("CRASHED " if crashed else "SURVIVED"), n, name,
                                   "".join("\n           " + x[:130] for x in fails[:1])))
    finally:
        gi_paths.remove_tree(base)
    if not control_ok:
        print("\nthe control FAILS: no mutant is judged")
        return 1
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
