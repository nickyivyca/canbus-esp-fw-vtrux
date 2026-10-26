#!/usr/bin/env python3
"""host_runner's `cfg build_id` reaches 0x7F3 B0-B3 (port step 0c, spec 10, diag schema 8).

    python3 test_cfg_build_id.py        # builds host_runner (make) first, like run_tests.py

Port step 0c (user, 2026-10-25) renamed the core's config field git_hash to
build_id, and the runner's `cfg` key with it. The goldens leave the key unset,
so 0x7F3 B0-B3 are 0 in every one; this test is the only place the key is set.
Each case feeds the runner a scenario of its own and reads every 0x7F3 it sends,
decoded by name through gi_diag with the schema 8 DBC from git:
  - `cfg build_id <n>`: every 0x7F3 carries diag_build_id = n, and B0-B3 are n
    little-endian (spec 10: a u32, sent little-endian), for a value with its top
    bit set too, so a signed narrowing on the way would show;
  - no `cfg build_id`: diag_build_id is 0, the goldens' value;
  - `cfg git_hash <n>`, the old key: the runner refuses the scenario.
Each case needs at least one 0x7F3 frame, so a scenario that sends none fails
rather than passing on nothing.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "tools", "bench")))
import gi_diag  # noqa: E402  the diag DBCs, by schema byte

RUNNER = os.path.join(HERE, "host_runner")
SCHEMA = 8          # 0x7F3 B0-B3 are diag_build_id from schema 8 on
FAILS = []


def check(name, ok, detail=""):
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok or not detail else "  -- " + str(detail)[:300]))
    if not ok:
        FAILS.append(name)


def run(cfg_lines):
    text = "\n".join(cfg_lines + ["mode 0 3 500", "end 2000000"]) + "\n"
    return subprocess.run([RUNNER], input=text, capture_output=True, text=True, timeout=60)


def pages_7f3(stdout):
    """The data bytes of every 0x7F3 the runner sent."""
    out = []
    for line in stdout.splitlines():
        f = line.split()
        if len(f) >= 6 and f[1] == "TX" and f[2] == "DIAG" and f[3] == "id=7F3":
            out.append(bytes.fromhex(f[5].split("=", 1)[1]))
    return out


def main():
    r = subprocess.run(["make", "-s", "host_runner"], cwd=HERE, capture_output=True, text=True)
    if r.returncode or not os.path.exists(RUNNER):
        print("cannot build host_runner:\n" + r.stdout + r.stderr)
        return 1
    db = gi_diag.schemas_for().get(SCHEMA).db

    for label, n in (("a value", 0x12345678), ("a value with the top bit set", 0xA1B2C3D4)):
        r = run(["cfg build_id %d" % n])
        pages = pages_7f3(r.stdout)
        dec = [db.decode_message(0x7F3, p, decode_choices=False) for p in pages]
        check("cfg build_id, %s: the runner accepts it" % label, r.returncode == 0, r.stderr)
        check("cfg build_id, %s: at least one 0x7F3 sent" % label, len(pages) > 0, len(pages))
        check("cfg build_id, %s: each 0x7F3 states schema 8" % label,
              pages and all(d["diag_schema_ver_build"] == SCHEMA for d in dec), dec[:1])
        check("cfg build_id, %s: diag_build_id = 0x%08X in each 0x7F3" % (label, n),
              pages and all(d["diag_build_id"] == n for d in dec), ["%08x" % d["diag_build_id"] for d in dec[:3]])
        check("cfg build_id, %s: B0-B3 = the value little-endian" % label,
              pages and all(p[0:4] == n.to_bytes(4, "little") for p in pages), [p.hex() for p in pages[:3]])

    r = run([])
    pages = pages_7f3(r.stdout)
    check("no cfg build_id: at least one 0x7F3 sent", r.returncode == 0 and len(pages) > 0, r.stderr or len(pages))
    check("no cfg build_id: diag_build_id is 0, the goldens' value",
          pages and all(db.decode_message(0x7F3, p)["diag_build_id"] == 0 for p in pages), [p.hex() for p in pages[:3]])

    r = run(["cfg git_hash 305419896"])
    check("cfg git_hash, the key before port step 0c: refused",
          r.returncode != 0 and "unknown cfg field 'git_hash'" in r.stderr, (r.returncode, r.stderr))

    print("PASS" if not FAILS else "FAIL: %d" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
