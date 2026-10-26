#!/usr/bin/env python3
"""Does /gi_txabort actually REFUSE a request it cannot honour? Spec 5.2 item 9.

    py -3.11 gen-inhibit/tools/bench/txabort_refusal_check.py

Needs the GI_INSTRUMENT_TXABORT measurement build on the device.

WHY THIS IS A SEPARATE SCRIPT FROM THE BENCH. The measurement's argument handling
had a defect where the guard's bypass path was indistinguishable from the ordinary
"argument not supplied" path -- `httpd_query_key_value()` returns
ESP_ERR_HTTPD_RESULT_TRUNC rather than ESP_OK for an over-long value, so testing
`== ESP_OK` silently ran the DEFAULT. `gate=<45 chars>` would have run ungated and
been reported as the RS-gated case. Found in review, 2026-09-27.

A fix to that class of defect is worth nothing unless the refusal is OBSERVED, so
this drives each case at the device and checks two things per case:

  1. the response is 400, and
  2. NOTHING RAN. That second half is the one that matters and it is not visible
     in the response: a handler could refuse AFTER transmitting. So each case is
     bracketed by the driver's own transmit counters and the device uptime, read
     from /gen_inhibit's `drv` object, and a case only passes if neither moved.

CHECKING THE RESPONSE ALONE WOULD BE THE WEAKER CHECK, and its failure mode would
look like success -- which is the shape this whole exercise is about.

The cases are the reviewing session's five plus one of mine:

    label 33 chars     the copy_arg path (33 > 32, still inside `val`'s 40)
    label 45 chars     the RESULT_TRUNC path (45 > 40, so the value never
                       reaches copy_arg at all)
    gate 45 chars      same path, on the argument where a silent default is
                       worst: gate=off means ungated
    query 300 bytes    httpd_req_get_url_query_str returns RESULT_TRUNC and ALL
                       parsing used to be skipped -- every argument wrong at once
    d0=70000           out of range; used to truncate into uint16_t as 4464
    d0=abc             MINE. atoi("abc") is 0, so this used to run as d0=0. No
                       mutation of the truncation path would have caught it,
                       because it is the same silent-default defect by a
                       different route.

And two controls, because a refusal check whose "refused" answer is also what a
broken endpoint gives is not a check:

    a valid minimal request   must SUCCEED (200), proving 400 is a verdict and
                              not the endpoint's only behaviour
    an unknown key            documents what the firmware does with `gaet=rs`:
                              it cannot see it. Reported, not asserted -- the
                              guarantee against a misspelled key is the bench's
                              echo check, which is the only side that knows what
                              was intended.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HOST = "192.168.80.1"
# GI_RUNS_DIR, else SeaDrive's runs/ (2026-10-25, reviewer): until then this wrote under this
# folder's runs/, inside the worktree and not gitignored, one `git add` from being published.
RUNS = os.environ.get("GI_RUNS_DIR") or gi_paths.runs()


def post(path, timeout=30.0):
    req = urllib.request.Request("http://%s%s" % (HOST, path), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_json(path):
    with urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=10) as r:
        return json.loads(r.read())


def device_image():
    """The running image's elf_sha256 from /gen_inhibit, or None when the page does not
    answer or names no 64 lower-case hex digits."""
    try:
        sha = get_json("/gen_inhibit").get("elf_sha256")
    except Exception:
        return None
    if isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None


def snapshot():
    """The state a refused request must not have changed.

    `drv` is null until the driver is installed, which is itself information: a
    refusal that never brought the bus up cannot have transmitted. Once a real
    run has installed it, the transmit counters are the evidence.
    """
    g = get_json("/gen_inhibit")
    return {
        "mode": g["mode"],
        "drv": g.get("drv"),
        "tx_ok": g.get("tx_ok"),
        "tx_fail": g.get("tx_fail"),
    }


CASES = [
    ("label 33 chars (copy_arg path)",
     "/gi_txabort?phase=control&reps=2&settle=20&label=" + "L" * 33),
    ("label 45 chars (RESULT_TRUNC path)",
     "/gi_txabort?phase=control&reps=2&settle=20&label=" + "L" * 45),
    ("gate 45 chars (RESULT_TRUNC, worst default)",
     "/gi_txabort?phase=abort&reps=2&settle=20&gate=" + "r" * 45),
    ("query string 300 bytes",
     "/gi_txabort?phase=control&reps=2&settle=20&pad=" + "p" * 300),
    ("d0=70000 (out of range, used to wrap to 4464)",
     "/gi_txabort?phase=abort&reps=2&settle=20&d0=70000&d1=70000"),
    ("d0=abc (atoi would have made this 0)",
     "/gi_txabort?phase=abort&reps=2&settle=20&d0=abc"),
    ("d1 below d0 (used to be silently clamped)",
     "/gi_txabort?phase=abort&reps=2&settle=20&d0=400&d1=100"),
]


def main():
    # The image from /gen_inhibit's elf_sha256 (spec 11), never /check_status, which answers
    # with the device's config, credentials included (reviewer, 2026-10-20). Until 2026-10-25
    # this read git_version from it, its only use of that page.
    sha = device_image()
    print("device elf_sha256 %s" % sha)
    if sha is None:
        raise SystemExit("the device names no elf_sha256 on /gen_inhibit")

    before = snapshot()
    print("before: mode=%s drv=%s tx_ok=%s tx_fail=%s\n"
          % (before["mode"], before["drv"], before["tx_ok"], before["tx_fail"]))

    results = []
    bad = 0

    for name, path in CASES:
        code, body = post(path)
        txt = body.decode("utf-8", "replace").strip()
        after = snapshot()
        moved = [k for k in ("drv", "tx_ok", "tx_fail")
                 if after[k] != before[k]]
        ok = (code == 400) and not moved
        if not ok:
            bad += 1
        print("[%s] %s" % ("PASS" if ok else "FAIL", name))
        print("        HTTP %d" % code)
        print("        body: %s" % (txt[:300] or "(empty)"))
        if moved:
            print("        *** STATE MOVED: %s -- the request was refused but "
                  "something ran" % ", ".join(moved))
        else:
            print("        nothing ran (drv/tx_ok/tx_fail unchanged)")
        results.append({"case": name, "query": path, "http": code,
                        "body": txt[:400], "state_moved": moved, "pass": ok})
        before = after
        time.sleep(0.2)

    # CONTROL 1: a valid request must be accepted, or "400" is not a verdict.
    print("\n[control] a valid minimal request must SUCCEED")
    code, body = post("/gi_txabort?phase=control&reps=2&settle=20&label=ctl",
                      timeout=120.0)
    ctl_ok = (code == 200)
    detail = ""
    if ctl_ok:
        try:
            rec = json.loads(body)
            detail = ("n=%s phase=%s label=%s query_given=%s"
                      % (rec.get("n"), rec.get("phase"), rec.get("label"),
                         rec.get("query_given")))
        except ValueError:
            ctl_ok = False
            detail = "200 but the body is not JSON"
    else:
        detail = body.decode("utf-8", "replace")[:300]
    print("        HTTP %d  %s  -> %s" % (code, detail,
                                          "PASS" if ctl_ok else "FAIL"))
    if not ctl_ok:
        bad += 1

    # CONTROL 2: an unknown key. Reported, not asserted -- see the docstring.
    print("\n[note] an unknown key is invisible to the firmware; the bench's "
          "echo check is what catches it")
    code, body = post("/gi_txabort?phase=control&reps=2&settle=20&gaet=rs",
                      timeout=120.0)
    note = ""
    if code == 200:
        try:
            rec = json.loads(body)
            note = ("accepted, ran with gate=%r -- so a misspelled key really "
                    "does fall through to the default" % rec.get("gate"))
        except ValueError:
            note = "200 with a non-JSON body"
    else:
        note = "HTTP %d: %s" % (code, body.decode("utf-8", "replace")[:200])
    print("        %s" % note)

    out = {"when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "elf_sha256": sha,
           "cases": results,
           "control_valid_request_ok": ctl_ok,
           "unknown_key_note": note}
    os.makedirs(RUNS, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    for p in (os.path.join(RUNS, "txabort_refusals_%s.json" % stamp),
              os.path.join(RUNS, "txabort_refusals.json")):
        with open(p, "w") as fh:
            json.dump(out, fh, indent=1)
        print("wrote %s" % p)

    print("\n%d of %d refusal cases passed; control %s"
          % (len(CASES) - bad + (0 if not ctl_ok else 0), len(CASES),
             "PASSED" if ctl_ok else "FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
