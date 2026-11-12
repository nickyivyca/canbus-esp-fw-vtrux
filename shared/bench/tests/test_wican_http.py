"""The WiCAN HTTP helpers' paths that only gen-inhibit's guard scripts use
(vtrux_bench/run/wican_http.py; FLASH-SPEC, "What moves, beside the flasher"),
on made-up replies through a fake urlopen. No network, no device.

The helpers moved from gen-inhibit's `gen-inhibit/tools/bench_ota_guard.py` at
gen-inhibit/main 40ecfa5e with their behaviour unchanged, and gen-inhibit's
guard benches (bench_ota_guard.py, bench_flash_guard.py) call them once
gen-inhibit pins the shared flasher, so these paths are part of the condition
for that pin (the gen-inhibit reviewer, 2026-11-11; runs/flasher_check_f67dc7b
found no shared test of them). Each rule is the reference's, at 40ecfa5e:

- http: the default timeout is 10 s (the reference's TIMEOUT; the new
  `timeout=` keyword defaults to it), a 4xx/5xx is a status, a connection
  reset is no status, bytes that are not UTF-8 are replaced, never raised;
- set_mode: the default form is `mode=<m>&offset=500`, POSTed with an empty
  body and a 10 s timeout (the reference's only form); `offset=None`, new,
  sends `mode=<m>` alone (the flasher's disarm);
- wifi_mode: one GET of /load_config, only the one value back, nothing of the
  rest printed or returned; a non-200, a body that is not a JSON object, or no
  wifi_mode is an error;
- ap_mode_detail: only "AP" passes, with the reference's texts;
- runs_dir: $GI_RUNS_DIR as an absolute path, refused if it is not a folder;
  unset, the legacy folder if it exists, else refused; from shared/bench the
  legacy path names no folder;
- device_identity: elf_sha256, git_version and uptime_ms from a lenient read
  of /gen_inhibit, None with no page, never /check_status;
- MODES, the reference's five.

The fake /load_config answers with a made-up value beside wifi_mode, so that
any of it printed or returned shows. Addresses are documentation ones (RFC
5737). Mutants in MUTANTS, one per rule, applied to wican_http.py.

    py -3.14 -B shared/bench/tests/test_wican_http.py             exit 0 only if every check passes
    py -3.14 -B shared/bench/tests/test_wican_http.py --can-fail  the control, then every mutant
"""
import contextlib
import io
import json
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
import _ota_mutants                                  # noqa: E402
import _scratch                                      # noqa: E402
sys.path.insert(0, _ota_mutants.package_root())
from vtrux_bench.run import wican_http as G          # noqa: E402

FAILS = []
HOST = "192.0.2.1"
MADE_UP = "made-up-value-not-a-credential"
SHA = "ab" * 32
ENV = "GI_RUNS_DIR"              # the reference's name, written out: not read from the module


def check(label, fn, detail=""):
    """fn is called here, so a crash in the code under test is this check's FAIL."""
    try:
        ok = bool(fn())
    except Exception as e:      # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def safe(fn):
    """fn() for a value worked out outside a check: a crash, or a SystemExit, reads as its
    text, so it fails the checks that read it instead of ending the test."""
    try:
        return fn()
    except (Exception, SystemExit) as e:      # noqa: BLE001
        return "%s: %s" % (type(e).__name__, e)


class Reply:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """A fake urllib.request.urlopen. replies: {path: ("ok", status, bytes) | ("http", code, bytes)
    | ("url",) | ("reset",)}; a path not in it is a 404. Records each request."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def urlopen(self, req, timeout=None):
        url = req.full_url
        path = url.split(HOST, 1)[1] if HOST in url else url
        self.calls.append({"url": url, "path": path, "method": req.get_method(), "data": req.data,
                           "timeout": timeout, "ctype": req.get_header("Content-type")})
        r = self.replies.get(path, ("http", 404, b"not found"))
        if r[0] == "ok":
            return Reply(r[1], r[2])
        if r[0] == "http":
            raise urllib.error.HTTPError(url, r[1], "made-up", {}, io.BytesIO(r[2]))
        if r[0] == "url":
            raise urllib.error.URLError("made-up: no route")
        raise ConnectionResetError(104, "made-up: reset by peer")

    def paths(self):
        return [c["path"] for c in self.calls]


def on(replies, fn):
    """(fn()'s result, the Net, what was printed): fn run with G's urlopen faked."""
    net = Net(replies)
    real = G.urllib.request.urlopen
    G.urllib.request.urlopen = net.urlopen
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            res = safe(fn)
    finally:
        G.urllib.request.urlopen = real
    return res, net, out.getvalue()


def main():
    # -- http -------------------------------------------------------------------------
    res, net, _ = on({"/x": ("ok", 200, b"hello")}, lambda: G.http(HOST, "/x"))
    c = net.calls[0] if net.calls else {}
    check("http: a GET of http://<host><path>, its 200 and text", lambda: res == (200, "hello")
          and c["url"] == "http://%s/x" % HOST and c["method"] == "GET" and c["data"] is None, (res, c))
    check("http: the default timeout is 10 s, the reference's", lambda: c["timeout"] == 10 and G.TIMEOUT == 10,
          (c.get("timeout"), G.TIMEOUT))
    res, net, _ = on({"/x": ("ok", 200, b"")}, lambda: G.http(HOST, "/x", timeout=3))
    check("http: timeout= is the one given", lambda: net.calls[0]["timeout"] == 3, net.calls and net.calls[0]["timeout"])
    res, net, _ = on({"/u": ("ok", 200, b"ok")}, lambda: G.http(HOST, "/u", method="POST", body=b"abc", ctype="text/plain"))
    check("http: the method, the body and the Content-Type sent",
          lambda: net.calls[0]["method"] == "POST" and net.calls[0]["data"] == b"abc"
          and net.calls[0]["ctype"] == "text/plain", net.calls)
    res, _, _ = on({"/x": ("http", 403, b"armed")}, lambda: G.http(HOST, "/x"))
    check("http: a 4xx comes back as its status and text, not an exception", lambda: res == (403, "armed"), res)
    res, _, _ = on({"/x": ("http", 500, b"oops")}, lambda: G.http(HOST, "/x"))
    check("http: a 5xx comes back as its status and text", lambda: res == (500, "oops"), res)
    res, _, _ = on({"/x": ("url",)}, lambda: G.http(HOST, "/x"))
    check("http: no route is no status", lambda: isinstance(res, tuple) and res[0] is None and "no route" in res[1], res)
    res, _, _ = on({"/x": ("reset",)}, lambda: G.http(HOST, "/x"))
    check("http: a connection reset is no status, never an exception (2026-09-26)",
          lambda: isinstance(res, tuple) and res[0] is None and res[1].startswith("ConnectionResetError"), res)
    res, _, _ = on({"/x": ("ok", 200, b"a\xffb")}, lambda: G.http(HOST, "/x"))
    check("http: bytes that are not UTF-8 are replaced, not raised", lambda: res == (200, "a\ufffdb"), ascii(res))

    # -- set_mode -----------------------------------------------------------------------
    res, net, _ = on({"/gen_inhibit_set?mode=3&offset=500": ("ok", 200, b"set")}, lambda: G.set_mode(HOST, 3))
    c = net.calls[0] if net.calls else {}
    check("set_mode: the default form is mode=<m>&offset=500, the reference's",
          lambda: c["path"] == "/gen_inhibit_set?mode=3&offset=500", c.get("path"))
    check("set_mode: POSTed", lambda: c["method"] == "POST", c.get("method"))
    check("set_mode: with an empty body", lambda: c["data"] == b"", c.get("data"))
    check("set_mode: the default timeout is 10 s", lambda: c["timeout"] == 10, c.get("timeout"))
    check("set_mode: http's reply comes back", lambda: res == (200, "set"), res)
    res, net, _ = on({}, lambda: G.set_mode(HOST, 2, offset=250, timeout=8))
    check("set_mode: offset= and timeout= are the ones given",
          lambda: net.calls[0]["path"] == "/gen_inhibit_set?mode=2&offset=250" and net.calls[0]["timeout"] == 8,
          net.calls and (net.calls[0]["path"], net.calls[0]["timeout"]))
    res, net, _ = on({}, lambda: G.set_mode(HOST, 0, offset=None))
    check("set_mode: offset=None sends mode=<m> alone (the flasher's disarm)",
          lambda: net.calls[0]["path"] == "/gen_inhibit_set?mode=0" and net.calls[0]["method"] == "POST",
          net.calls and net.calls[0]["path"])

    # -- wifi_mode ------------------------------------------------------------------------
    cfg = json.dumps({"wifi_mode": "AP", "ap_pass": MADE_UP, "sta_ssid": MADE_UP, "port": "3333"}).encode()
    res, net, out = on({"/load_config": ("ok", 200, cfg), "/check_status": ("ok", 200, cfg)},
                       lambda: G.wifi_mode(HOST))
    check("wifi_mode: the one value, no error", lambda: res == ("AP", None), res)
    check("wifi_mode: one GET of /load_config, nothing else asked for",
          lambda: net.paths() == ["/load_config"] and net.calls[0]["method"] == "GET", net.paths())
    check("wifi_mode: none of the rest of the config printed or returned",
          lambda: MADE_UP not in out and MADE_UP not in repr(res) and out == "", (len(out), MADE_UP in repr(res)))
    res, _, _ = on({"/load_config": ("ok", 200, json.dumps({"wifi_mode": "APStation"}).encode())},
                   lambda: G.wifi_mode(HOST))
    check("wifi_mode: another value comes back as it is", lambda: res == ("APStation", None), res)
    res, _, _ = on({"/load_config": ("http", 500, json.dumps({"wifi_mode": "AP"}).encode())},
                   lambda: G.wifi_mode(HOST))
    check("wifi_mode: a non-200 is an error, whatever its body", lambda: res == (None, "GET /load_config returned 500"), res)
    res, _, _ = on({"/load_config": ("reset",)}, lambda: G.wifi_mode(HOST))
    check("wifi_mode: no status is an error", lambda: res == (None, "GET /load_config returned None"), res)
    res, _, _ = on({"/load_config": ("ok", 200, b"{not json")}, lambda: G.wifi_mode(HOST))
    check("wifi_mode: a body that is not JSON is an error", lambda: res == (None, "the config was not JSON"), res)
    res, _, _ = on({"/load_config": ("ok", 200, b'["AP"]')}, lambda: G.wifi_mode(HOST))
    check("wifi_mode: JSON that is not an object is an error", lambda: res == (None, "the config was not a JSON object"), res)
    res, _, _ = on({"/load_config": ("ok", 200, json.dumps({"port": "3333"}).encode())}, lambda: G.wifi_mode(HOST))
    check("wifi_mode: no wifi_mode is an error", lambda: res == (None, "the config has no wifi_mode"), res)

    # -- ap_mode_detail ------------------------------------------------------------------
    res = safe(lambda: G.ap_mode_detail("AP", None))
    check("ap_mode_detail: AP passes, with the reference's text",
          lambda: res == (True, "wifi_mode='AP' -- the configuration that flies"), res)
    res = safe(lambda: G.ap_mode_detail(None, "GET /load_config returned 500"))
    check("ap_mode_detail: a value not read fails, with the reason",
          lambda: res == (False, "wifi_mode not read: GET /load_config returned 500"), res)
    res = safe(lambda: G.ap_mode_detail("APStation", None))
    check("ap_mode_detail: APStation fails, named, with spec section 8's reason",
          lambda: res[0] is False and res[1].startswith("wifi_mode='APStation', not 'AP'. Spec section 8 makes AP permanent")
          and "not a result about the build that flies" in res[1], res)
    res = safe(lambda: G.ap_mode_detail("ap", None))
    check("ap_mode_detail: only AP exactly, not ap", lambda: res[0] is False, res)

    # -- runs_dir ------------------------------------------------------------------------
    scratch = _scratch.run_dir("wican_http")
    runs = os.path.join(scratch, "runs")
    legacy = os.path.join(scratch, "legacy")
    a_file = os.path.join(scratch, "a_file")
    os.makedirs(runs)
    os.makedirs(legacy)
    with open(a_file, "w") as f:
        f.write("not a folder\n")
    saved_env, saved_legacy = os.environ.get(ENV), G._LEGACY_RUNS

    def runs_with(env, legacy_path):
        if env is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = env
        G._LEGACY_RUNS = legacy_path
        try:
            return safe(G.runs_dir)
        finally:
            G._LEGACY_RUNS = saved_legacy
            if saved_env is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = saved_env
    missing = os.path.join(scratch, "no-such-folder")
    res = runs_with(os.path.relpath(runs), missing)
    check("runs_dir: $GI_RUNS_DIR, given relative, comes back absolute", lambda: res == os.path.abspath(runs), res)
    res = runs_with(runs, legacy)
    check("runs_dir: $GI_RUNS_DIR comes before the legacy folder", lambda: res == os.path.abspath(runs), res)
    res = runs_with(a_file, legacy)
    check("runs_dir: a $GI_RUNS_DIR that is not a folder is refused, named",
          lambda: res.startswith("SystemExit: GI_RUNS_DIR=") and "is not a directory" in res, res)
    res = runs_with(missing, legacy)
    check("runs_dir: a $GI_RUNS_DIR that does not exist is refused",
          lambda: res.startswith("SystemExit: GI_RUNS_DIR=") and "is not a directory" in res, res)
    res = runs_with(None, legacy)
    check("runs_dir: unset, the legacy folder if it exists", lambda: res == os.path.abspath(legacy), res)
    res = runs_with(None, missing)
    check("runs_dir: unset and no legacy folder, refused, naming GI_RUNS_DIR",
          lambda: res == "SystemExit: no runs folder: set GI_RUNS_DIR to reverse-it's "
          "projects/vtrux/notes/artifacts/gen-inhibit/runs", res)
    res = runs_with(None, saved_legacy)
    check("runs_dir: from shared/bench the legacy path names no folder, so unset is refused",
          lambda: not os.path.isdir(saved_legacy) and res.startswith("SystemExit: no runs folder"), res)

    # -- device_identity ----------------------------------------------------------------
    page = {"elf_sha256": SHA, "git_version": "made-up-1.0", "uptime_ms": 1234, "mode": 0, "tx_frames": 7}
    res, net, _ = on({"/gen_inhibit": ("ok", 200, json.dumps(page).encode()),
                      "/check_status": ("ok", 200, MADE_UP.encode())}, lambda: G.device_identity(HOST))
    check("device_identity: elf_sha256, git_version and uptime_ms, and nothing else",
          lambda: res == {"elf_sha256": SHA, "git_version": "made-up-1.0", "uptime_ms": 1234}, res)
    check("device_identity: one GET of /gen_inhibit, never /check_status",
          lambda: net.paths() == ["/gen_inhibit"], net.paths())
    res, _, _ = on({"/gen_inhibit": ("ok", 200, json.dumps(page).encode() + b"\n{\"frag")},
                   lambda: G.device_identity(HOST))
    check("device_identity: a page with trailing data is read (lenient)",
          lambda: isinstance(res, dict) and res.get("elf_sha256") == SHA and "_trailing" not in res, res)
    res, _, _ = on({"/gen_inhibit": ("ok", 200, json.dumps({"elf_sha256": SHA}).encode())},
                   lambda: G.device_identity(HOST))
    check("device_identity: a key the page lacks is None",
          lambda: res == {"elf_sha256": SHA, "git_version": None, "uptime_ms": None}, res)
    for why, reply in (("a 500", ("http", 500, b"")), ("a reset", ("reset",)), ("a page that is not JSON", ("ok", 200, b"<html>"))):
        res, _, _ = on({"/gen_inhibit": reply}, lambda: G.device_identity(HOST))
        check("device_identity: None on %s" % why, lambda: res is None, res)

    # -- MODES ----------------------------------------------------------------------------
    check("MODES: the reference's five",
          lambda: G.MODES == {0: "OFF", 1: "OBSERVE", 2: "RESPOND", 3: "INHIBIT", 4: "PASSIVE"}, G.MODES)

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


HTTP = "run/wican_http.py"
MUTANTS = [(n, HTTP, o, w) for n, o, w in [
    # http
    ("the default timeout is 8 s", "TIMEOUT = 10", "TIMEOUT = 8"),
    ("http's default timeout is not TIMEOUT", 'def http(host, path, method="GET", body=None, ctype=None, timeout=TIMEOUT):',
     'def http(host, path, method="GET", body=None, ctype=None, timeout=None):'),
    ("http ignores timeout=", "urlopen(req, timeout=timeout)", "urlopen(req, timeout=TIMEOUT)"),
    ("no Content-Type sent", "    if ctype:\n", "    if False:\n"),
    ("a 4xx/5xx is no status", 'return e.code, e.read().decode("utf-8", "replace")', "return None, str(e)"),
    ("a reset leaks", "    except OSError as e:\n", "    except ConnectionAbortedError as e:\n"),
    ("bytes that are not UTF-8 raise", 'return r.status, r.read().decode("utf-8", "replace")',
     'return r.status, r.read().decode("utf-8")'),
    # set_mode
    ("the default form is mode alone", "def set_mode(host, mode, offset=500, timeout=TIMEOUT):",
     "def set_mode(host, mode, offset=None, timeout=TIMEOUT):"),
    ("the default offset is 0", "def set_mode(host, mode, offset=500, timeout=TIMEOUT):",
     "def set_mode(host, mode, offset=0, timeout=TIMEOUT):"),
    ("set_mode by GET", 'method="POST", body=b"", timeout=timeout)', 'method="GET", body=b"", timeout=timeout)'),
    ("set_mode with no body", 'method="POST", body=b"", timeout=timeout)', 'method="POST", body=None, timeout=timeout)'),
    ("set_mode drops its timeout", 'method="POST", body=b"", timeout=timeout)', 'method="POST", body=b"")'),
    ("offset=None still sends an offset", 'form = {"mode": mode} if offset is None else {"mode": mode, "offset": offset}',
     'form = {"mode": mode, "offset": offset}'),
    ("the offset before the mode", 'else {"mode": mode, "offset": offset}', 'else {"offset": offset, "mode": mode}'),
    # wifi_mode
    ("wifi_mode reads /check_status", 'st, txt = http(host, "/load_config")', 'st, txt = http(host, "/check_status")'),
    ("wifi_mode returns the whole config", "    return value, None\n", "    return cfg, None\n"),
    ("wifi_mode prints the config", "        cfg = json.loads(txt)\n", "        cfg = json.loads(txt)\n        print(txt)\n"),
    ("a non-200 config is read", '    if st != 200:\n        return None, "GET /load_config returned %s" % st',
     '    if st is None:\n        return None, "GET /load_config returned %s" % st'),
    ("a config that is not JSON raises", '    except ValueError:\n        return None, "the config was not JSON"',
     '    except TypeError:\n        return None, "the config was not JSON"'),
    ("a config that is not an object is read", "    if not isinstance(cfg, dict):\n", "    if False:\n"),
    ("no wifi_mode is no error", '    if value is None:\n        return None, "the config has no wifi_mode"',
     '    if False:\n        return None, "the config has no wifi_mode"'),
    # ap_mode_detail
    ("a value not read passes", 'return False, "wifi_mode not read: %s" % err', 'return True, "wifi_mode not read: %s" % err'),
    ("APStation passes", 'if value != "AP":', 'if value not in ("AP", "APStation"):'),
    ("ap passes", 'if value != "AP":', 'if str(value).upper() != "AP":'),
    ("AP fails", "return True, \"wifi_mode='AP' -- the configuration that flies\"",
     "return False, \"wifi_mode='AP' -- the configuration that flies\""),
    # runs_dir
    ("the variable is GI_RUN_DIR", 'RUNS_ENV = "GI_RUNS_DIR"', 'RUNS_ENV = "GI_RUN_DIR"'),
    ("$GI_RUNS_DIR kept relative", "        return os.path.abspath(d)\n", "        return d\n"),
    ("a $GI_RUNS_DIR that is not a folder is used", "        if not os.path.isdir(d):\n", "        if False:\n"),
    ("the legacy folder before $GI_RUNS_DIR", "    d = os.environ.get(RUNS_ENV)\n",
     "    d = None if os.path.isdir(_LEGACY_RUNS) else os.environ.get(RUNS_ENV)\n"),
    ("the legacy folder never used", "    if os.path.isdir(_LEGACY_RUNS):\n", "    if False:\n"),
    ("unset, the current folder", "    raise SystemExit(\"no runs folder: set %s to reverse-it's \"",
     "    return os.getcwd()\n    raise SystemExit(\"no runs folder: set %s to reverse-it's \""),
    ("the legacy path names a folder from shared/bench", '"..", "..", "notes", "artifacts", "gen-inhibit", "runs")',
     '"..")'),
    # device_identity
    ("device_identity returns the page", "    return {k: page.get(k) for k in (\"elf_sha256\", \"git_version\", \"uptime_ms\")}",
     "    return page"),
    ("device_identity drops git_version", '("elf_sha256", "git_version", "uptime_ms")', '("elf_sha256", "uptime_ms")'),
    ("device_identity reads strictly", "    page, _ = gi_status(host, strict=False)\n", "    page, _ = gi_status(host, strict=True)\n"),
    ("device_identity asks /check_status", "    page, _ = gi_status(host, strict=False)\n",
     "    http(host, \"/check_status\")\n    page, _ = gi_status(host, strict=False)\n"),
    ("no page is an empty identity", "    if page is None:\n        return None\n    return {k:",
     "    if page is None:\n        return {}\n    return {k:"),
    ("a missing key raises", "{k: page.get(k) for k in", "{k: page[k] for k in"),
    # MODES
    ("no PASSIVE mode", '    4: "PASSIVE",\n', ""),
]]


if __name__ == "__main__":
    if "--can-fail" in sys.argv[1:]:
        sys.exit(_ota_mutants.run_mutants(os.path.abspath(__file__), MUTANTS, "wican_http"))
    sys.exit(main())
