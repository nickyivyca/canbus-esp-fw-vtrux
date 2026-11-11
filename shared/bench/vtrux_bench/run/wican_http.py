"""The WiCAN's HTTP helpers, shared by the OTA flasher (ota.py) and
gen-inhibit's guard scripts (bench_ota_guard.py, bench_flash_guard.py).

Moved from gen-inhibit's `gen-inhibit/tools/bench_ota_guard.py` at
gen-inhibit/main 40ecfa5e (FLASH-SPEC, "What moves, beside the flasher"),
behaviour unchanged, names and signatures kept so the guard scripts can import
them from a pinned shared/bench:

    http(host, path, method="GET", body=None, ctype=None, timeout=TIMEOUT)
    have_external_route()
    wifi_mode(host)
    ap_mode_detail(value, err)
    gi_status(host, strict=True)
    set_mode(host, mode, offset=500, timeout=TIMEOUT)
    device_identity(host)
    runs_dir()
    MODES, TIMEOUT, RUNS_ENV

Two additions, both keyword arguments with the old behaviour as the default:
`http`'s `timeout` (the flasher's disarm reads with 8 s, as gen-inhibit's
wican_reboot.py does), and `set_mode`'s `offset=None`, which sends `mode=<m>`
alone (FLASH-SPEC: the flasher's disarm sends `mode=0` alone, as wican_reboot
does; bench_ota_guard's `mode=0&offset=500`, the default, stays its own).

Never /check_status or any config but `wifi_mode`'s one value: /check_status
and /load_config answer with credentials (reviewer, 2026-10-20; user,
2026-10-21).
"""

import json
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10

MODES = {
    0: "OFF",
    1: "OBSERVE",
    2: "RESPOND",
    3: "INHIBIT",
    4: "PASSIVE",
}


def http(host, path, method="GET", body=None, ctype=None, timeout=TIMEOUT):
    """(status, text). A 4xx/5xx comes back as a status, not an exception."""
    url = "http://%s%s" % (host, path)
    req = urllib.request.Request(url, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, ssl.SSLError) as e:
        return None, str(e)
    except OSError as e:
        # A RESET IS NOT AN EXCEPTION THIS MAY LEAK, and it did on 2026-09-26.
        # A malformed OTA upload made the device close the connection, raising
        # ConnectionResetError -- which is an OSError but NOT a URLError, so it
        # escaped this function, aborted the caller mid-run, and skipped every
        # verification step that would have said what state the device was in.
        # A transport failure has to come back as "no status", like the others.
        return None, "%s: %s" % (type(e).__name__, e)


def have_external_route():
    """Is there internet right now, and not via the WiCAN?

    Checked by making a connection rather than by parsing a routing table. A
    routing table can look right while nothing works, and the thing that
    matters here is whether the operator still has connectivity after WiFi is
    pointed at the device.
    """
    for hostport in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            s = socket.create_connection(hostport, timeout=4)
            local = s.getsockname()[0]
            s.close()
            return True, local
        except OSError:
            continue
    return False, None


def wifi_mode(host):
    """(value, error): the config's wifi_mode from /load_config, and nothing else.

    /load_config answers with the whole config, credentials included. Until the
    WiCAN's factory reset only protocol, port_type, port and wifi_mode may be
    read from it (user, 2026-10-21), so this keeps the one value and never
    prints, records or returns the rest. A missing or unreadable value is an
    error, which callers treat as a failed precondition. The OTA flasher never
    calls it (FLASH-SPEC O6).
    """
    st, txt = http(host, "/load_config")
    if st != 200:
        return None, "GET /load_config returned %s" % st
    try:
        cfg = json.loads(txt)
    except ValueError:
        return None, "the config was not JSON"
    if not isinstance(cfg, dict):
        return None, "the config was not a JSON object"
    value = cfg.get("wifi_mode")
    if value is None:
        return None, "the config has no wifi_mode"
    return value, None


def ap_mode_detail(value, err):
    """The precondition's verdict on wifi_mode: (ok, detail). Only "AP" passes."""
    if value is None:
        return False, "wifi_mode not read: %s" % err
    if value != "AP":
        return False, ("wifi_mode=%r, not 'AP'. Spec section 8 makes AP permanent: "
                       "the APStation reconnect path calls esp_wifi_stop()/start(), "
                       "which has no place on a device transmitting to a live "
                       "powertrain bus. A result from this configuration is not a "
                       "result about the build that flies." % (value,))
    return True, "wifi_mode='AP' -- the configuration that flies"


def gi_status(host, strict=True):
    """(page, error). With strict=False, a trailing fragment is tolerated.

    The leading JSON document is read with raw_decode either way, so the fields
    come from real data rather than from a repair. What differs is the verdict:
    strict reports trailing data as an error, lenient returns the page and puts
    the complaint in `page["_trailing"]` for the caller to report as it sees fit.

    Callers should be strict about an image they have just installed and lenient
    about one they are about to replace -- a device with a broken status page is
    exactly a device that needs reflashing, and refusing on that ground leaves
    only the cable. See ota.py, which does both.
    """
    st, txt = http(host, "/gen_inhibit")
    if st != 200:
        return None, "GET /gen_inhibit returned %s" % st
    try:
        page, end = json.JSONDecoder().raw_decode(txt.lstrip())
    except ValueError as e:
        return None, "status was not JSON: %s" % e
    if not isinstance(page, dict):
        return None, "status was %s, not an object" % type(page).__name__
    rest = txt.lstrip()[end:].strip()
    if rest:
        msg = ("status page has %d bytes of trailing data after the JSON "
               "object (starts %r)" % (len(rest), rest[:40]))
        if strict:
            return None, msg
        page["_trailing"] = msg
    return page, None


def set_mode(host, mode, offset=500, timeout=TIMEOUT):
    """POST /gen_inhibit_set with an empty body. The form is the caller's:
    `mode=<m>&offset=<o>` by default (bench_ota_guard's), `mode=<m>` alone
    with offset=None (the OTA flasher's disarm, as wican_reboot sends it)."""
    form = {"mode": mode} if offset is None else {"mode": mode, "offset": offset}
    q = urllib.parse.urlencode(form)
    return http(host, "/gen_inhibit_set?" + q, method="POST", body=b"", timeout=timeout)


RUNS_ENV = "GI_RUNS_DIR"
# Where gen-inhibit's runs folder sat in reverse-it before its tools moved into
# the firmware repo, relative to the file that defines it. Kept as written in
# bench_ota_guard.py; from this file's folder it names no runs folder that
# exists, so here only $GI_RUNS_DIR finds one.
_LEGACY_RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "notes", "artifacts", "gen-inhibit", "runs")


def runs_dir():
    """The bench's run-record folder: $GI_RUNS_DIR, else the reverse-it path
    beside this file if it exists. Otherwise refuse, BEFORE touching the
    device: in the firmware repo there is no runs folder, and a record must
    neither go missing nor land inside the repo."""
    d = os.environ.get(RUNS_ENV)
    if d:
        if not os.path.isdir(d):
            raise SystemExit("%s=%r is not a directory" % (RUNS_ENV, d))
        return os.path.abspath(d)
    if os.path.isdir(_LEGACY_RUNS):
        return os.path.abspath(_LEGACY_RUNS)
    raise SystemExit("no runs folder: set %s to reverse-it's "
                     "projects/vtrux/notes/artifacts/gen-inhibit/runs" % RUNS_ENV)


def device_identity(host):
    """The running image, from /gen_inhibit: elf_sha256 names the image file
    (spec 11, "The page names the binary it runs"), with git_version and
    uptime_ms beside it. None if the page does not answer.

    NEVER /check_status (reviewer, 2026-10-20): it answers with the device's
    config, credentials included.
    """
    page, _ = gi_status(host, strict=False)
    if page is None:
        return None
    return {k: page.get(k) for k in ("elf_sha256", "git_version", "uptime_ms")}
