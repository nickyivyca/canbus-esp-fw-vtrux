#!/usr/bin/env python3
"""One read of the WiCAN's /gen_inhibit page, recorded: the state a step leaves behind.

    py -3.14 -B tools/gen_inhibit/bench/page_state.py --label L --out F.json

Records mode, uptime, reset reason, the image (elf_sha256), the live, disabled and
self_off flags and the fields that say why an arm is not live or what latched (test_only_inert's
REASON_FIELDS), and nothing else from the page. It judges nothing. Reads /gen_inhibit
only, never /check_status. Up to --tries GETs, 3 s apart, until one parses; exit 0 when
one did, 1 when none did (the record then says so). GI_WICAN_HOST overrides the
device's address (the harness test's fake). Added 2026-10-20 (reviewer): B12's rt step
ends with it, because without rt_self's OTA the next step starts from whatever the rt
step leaves.

Cross-project dependency (gen-inhibit reviewer, 2026-10-24): the charge-interposer's
runner loads this file by path, read-only, from a pinned gen-inhibit/main checkout (as
wican_reboot.py does), and relies on read_page(timeout) returning the page as a dict
with elf_sha256. A change to it goes to both reviewers first (the full list is in
tools/gen_inhibit/README.md).
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HOST = os.environ.get("GI_WICAN_HOST") or "192.168.80.1"
FIELDS = ("mode", "uptime_ms", "reset_reason", "elf_sha256", "inhibit_live", "disabled", "self_off")
REASON_FIELDS = ("arm_block", "abort_reason", "abort_latched", "disable_reason", "err_n", "err_age_ms", "worker")


def read_page(timeout):
    with urllib.request.urlopen("http://%s/gen_inhibit" % HOST, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    p, _ = json.JSONDecoder().raw_decode(body.lstrip())
    if not isinstance(p, dict):
        raise ValueError("the page is not an object")
    return p


def state(p):
    out = {f: p.get(f) for f in FIELDS}
    out["reasons"] = {f: p.get(f) for f in REASON_FIELDS}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=8.0)
    a = ap.parse_args(argv)
    rec = {"label": a.label, "host": HOST, "page": None, "errors": []}
    for i in range(a.tries):
        if i:
            time.sleep(3)
        rec["t"] = time.time()
        try:
            rec["page"] = state(read_page(a.timeout))
            break
        except Exception as e:
            rec["errors"].append(repr(e)[:120])
    with open(a.out, "w") as fh:
        json.dump(rec, fh, indent=1)
    p = rec["page"]
    if p is None:
        print("%s: no page in %d tries" % (a.label, a.tries))
        return 1
    print("%s: mode %s, uptime_ms %s, reset %s, live %s, %s" % (
        a.label, p["mode"], p["uptime_ms"], p["reset_reason"], p["inhibit_live"],
        ", ".join("%s=%s" % kv for kv in p["reasons"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
