"""Decode the diag frames the firmware actually emits, with cantools.

WHY. The firmware, `wican_diag_schema.md` and `vtrux-wican-diag.dbc` are three
hand-maintained descriptions of the same bytes, and nothing has ever checked
that they agree. `diag_schema_ver` catches a DECODER holding the wrong DBC; it
cannot catch a DBC that disagrees with the firmware at the same version, which
is the likelier mistake when all three are edited in one sitting.

This takes real emitted frames -- from the host runner, which builds them with
the same build_diag() the ESP32 runs -- and decodes them against the DBC. A
field at the wrong offset, a width that truncates, or a VAL_ table that has
drifted from the enum shows up as a wrong value rather than as nothing.

It is the first piece of review E3 ("decode emitted diag frames with
cantools"). Run on madhouse-debian, where the host suite builds:

  python3.13 tools/gen_inhibit/bench/diag_decode_check.py \\
      ~/gi_host/test/gen_inhibit_host
"""

import os
import subprocess
import sys
import gi_diag  # noqa: E402  the diag DBCs from git or its export (toolkit item 10)

# THE DBCS (2026-10-24, toolkit item 10). The WiCAN's comes from gi_diag: in this
# repo, from git, with this tree's run identity; in B15host's copy on madhouse-debian
# (no git), from the diag_schemas/ export copied beside this file, which carries the
# identity of the tree it was made from. Each scenario's pages are decoded in order by
# their schema byte, and a page on another schema than the identity's is refused. The
# interposer's DBC is read the same way, at its pin in pins.json (reading Q5).

# One scenario per mode worth sampling. PASSIVE is the point of schema 4, so it
# must be in here; INHIBIT is the comparison.
CASES = (
    ("INHIBIT", 3, "keyon-normal"),
    ("PASSIVE", 4, "keyon-normal"),
)

DIAG_IDS = (0x7F1, 0x7F2, 0x7F3, 0x7F8)

# The other aftermarket device on the same bus. Both may be fitted at once, so
# their diagnostic IDs must be disjoint -- see the allocation table in
# wican_diag_schema.md. Schema 4 shipped its fourth page on 0x7F4, which is the
# interposer's INTP_Status, and it was caught in review rather than by anything
# automatic. This is the automatic thing.


def run_scenario(host_dir, name, mode):
    path = os.path.join(host_dir, "scenarios", name + ".scn")
    text = open(path).read()
    if mode != 3:
        out = []
        for ln in text.splitlines():
            p = ln.split()
            if len(p) == 4 and p[0] == "mode" and p[2] == "3":
                out.append("mode %s %d %s" % (p[1], mode, p[3]))
            else:
                out.append(ln)
        text = "\n".join(out) + "\n"
    r = subprocess.run([os.path.join(host_dir, "host_runner")], input=text,
                       capture_output=True, text=True, cwd=host_dir)
    if r.returncode != 0:
        raise RuntimeError("runner exited %d" % r.returncode)
    return r.stdout


def diag_frames(trace):
    """(id, bytes) for every TX DIAG line."""
    out = []
    for ln in trace.splitlines():
        if " TX DIAG " not in ln:
            continue
        ident = data = None
        for tok in ln.split():
            if tok.startswith("id="):
                ident = int(tok[3:], 16)
            elif tok.startswith("data="):
                data = bytes.fromhex(tok[5:])
        if ident is not None and data is not None:
            out.append((ident, data))
    return out


def main():
    host_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/gi_host/test/gen_inhibit_host")
    schemas = gi_diag.schemas_for()
    ident = schemas.identity or gi_diag.run_identity(schemas=schemas)
    db = schemas.get(ident["dbc_version"]).db
    print("DBC: %s at %s, schema %d, hash %s (%s)"
          % (ident["dbc_path"], ident["dbc_commit"][:10], ident["dbc_version"], ident["dbc_hash"],
             schemas.source))

    bad = 0

    # --- the check that would have caught the 0x7F4 clash -----------------
    try:
        idb = gi_diag.interposer_dbc()
    except Exception as exc:                              # noqa: BLE001
        print("  [WARN] cannot load %s: %s" % (gi_diag.INTERPOSER_DBC_PATH, exc))
        idb = None
    if idb is not None:
        mine = {m.frame_id: m.name for m in db.messages}
        theirs = {m.frame_id: m.name for m in idb.messages}
        clash = sorted(set(mine) & set(theirs))
        if clash:
            for i in clash:
                print("  [FAIL] 0x%03X declared by BOTH: %s (WiCAN) and %s "
                      "(interposer). Two transmitters on one ID."
                      % (i, mine[i], theirs[i]))
            bad += len(clash)
        else:
            print("  WiCAN %s and interposer %s are disjoint"
                  % (["0x%03X" % i for i in sorted(mine)],
                     ["0x%03X" % i for i in sorted(theirs)]))

    for label, mode, scenario in CASES:
        trace = run_scenario(host_dir, scenario, mode)
        frames = diag_frames(trace)
        seen = {}
        decoder = gi_diag.Decoder(schemas, ident)
        last7f8 = None
        for fid, data in frames:
            seen.setdefault(fid, 0)
            seen[fid] += 1
            try:
                dec = decoder.feed(fid, data)
            except gi_diag.DiagError as exc:
                print("  [FAIL] %s 0x%03X %s: REFUSED, %s" % (label, fid, data.hex(), exc))
                bad += 1
                break
            except Exception as exc:                      # noqa: BLE001
                print("  [FAIL] %s 0x%03X %s: %s" % (label, fid, data.hex(), exc))
                bad += 1
                continue
            if isinstance(dec, gi_diag.Undecodable):
                print("  [FAIL] %s 0x%03X %s: undecodable, %s" % (label, fid, data.hex(), dec.reason))
                bad += 1
                continue
            if fid == 0x7F8:
                last7f8 = (data, dec)
            if fid == 0x7F1:
                # The DBC's own schema, not a literal: this was pinned to 4 until
                # 2026-10-12 and would have failed every build since schema 5.
                want = ident["dbc_version"]
                if int(dec["diag_schema_ver"]) != want:
                    print("  [FAIL] %s 0x7F1 schema_ver=%s, the DBC says %d"
                          % (label, dec["diag_schema_ver"], want))
                    bad += 1
                got = str(dec["diag_mode"])
                if got != label:
                    print("  [FAIL] %s 0x7F1 diag_mode decodes as %s" % (label, got))
                    bad += 1
            if fid == 0x7F8 and int(dec["diag_flags2"]) & 0x08:
                print("  [FAIL] %s 0x7F8 emit_refused set -- firmware bug" % label)
                bad += 1

        missing = [i for i in DIAG_IDS if i not in seen]
        print("  %-8s pages seen: %s%s"
              % (label, ", ".join("0x%03X x%d" % (i, seen[i]) for i in sorted(seen)),
                 "   MISSING %s" % ["0x%03X" % i for i in missing] if missing else ""))
        if missing:
            bad += 1

        # Show the LAST 0x7F8, not the first: the first is built at t=0 before
        # anything has happened, so every counter reads zero and the frame
        # proves only that it parses.
        if last7f8:
            print("    0x7F8 %s -> %s" % (last7f8[0].hex(),
                                          ", ".join("%s=%s" % kv
                                                    for kv in last7f8[1].items())))
        print("    decoder: %s" % decoder.summary())

    print()
    print("%s" % ("all emitted diag frames decode against the DBC"
                  if bad == 0 else "%d PROBLEM(S) -- firmware, schema doc and "
                                   "DBC disagree" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
