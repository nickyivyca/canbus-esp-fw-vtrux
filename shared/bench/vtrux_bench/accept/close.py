"""The acceptance record's close (ACCEPT-SPEC A13), with the cleanup before
it (A14).

close(rec, summary, made, adapter), once per record, in this order:
  1. the cleanup (A14): made.remove_all() removes every tree, mutant copy,
     settings file and work folder the run made; whatever it cannot remove
     is a LEFTOVER line, and goes into the README below.
  2. the identifier scan (A13): the adapter's identifier_scan(folder) over
     the record, every hit recorded in the README, the close never refused
     for one (the bench's identifiers may stay in SeaDrive run records,
     never in git: user, 2026-11-01). An adapter that gives no scan is
     recorded as such, and a scan that fails is recorded as FAILED, with
     its error, and the close goes on.
  3. README.md: the step, the list's commit and sha256, the runner's
     commit, the trees, the builds, every item with its status, every
     result of each check and suite run that is not a plain PASS (all of
     them: the item's detail stops at six; the user, 2026-11-02, through
     the charge-interposer reviewer: they are checked against the known
     fails by hand), the
     comparisons (each with the differences that count, then every other
     difference its comparer saw, "seen, not counted"), the can-fails, the
     premises, each flagged case's first
     and re-run verdicts, the identifier hits, the leftovers, the verdict.
  4. verification_skeleton.md: the facts for the VERIFICATION.md entry,
     which the tester still writes; a --dev run's opens "DEV RUN, not
     evidence".
  5. the console stops, and SHA256SUMS goes over every other file. From
     here the folder never changes; a re-run is a new record naming this
     one (summary["earlier"]).
  6. the project's artifacts README row, through the adapter's
     artifacts_row(folder, summary). This and anything else that fails
     after the close is printed and returned ("late"), never written into
     the closed record (A14).

summary: {"step", "list" {commit, path, sha256}, "runner" {commit, dirty},
"trees" {name: tree record}, "builds" {id: build record}, "compares"
[judge.run_compare results], "premises" [judge.premises results], "reruns"
{suite run id: nested.json "flagged"}, "verdict" (judge.verdict), "earlier"
(an earlier record's id, or None)}.

RULES: each is switched off by a mutant in tests/test_accept_close.py,
which must then fail.
"""

import hashlib
import os

RULES = {k: True for k in (
    "readme_items", "readme_commits", "sums_all", "console_stops", "close_once",
    "scan_hits", "scan_absent", "cleanup_first", "leftovers_in", "late_outside", "row",
    "skeleton_reruns", "dev_marked", "scan_failure", "readme_not_passed", "not_passed_all",
    "compare_all")}


class CloseError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _write(rec, name, text):
    with open(os.path.join(rec.path, name), "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _rerun_lines(summary):
    out = []
    for sr, rows in sorted((summary.get("reruns") or {}).items()):
        for r in rows:
            out.append("| %s | %s | first %s | re-run %s |" % (
                sr, r["check"], r["first"], r["rerun"] or "(not re-run)"))
    return out


def readme(rec, summary, hits, leftovers):
    s = summary
    st = s["step"]
    L = ["# Acceptance record %s" % rec.id, "",
         "Step **%s** (%s), ordered by %s on %s; spec %s; VERIFICATION.md section: %s."
         % (st["id"], st["project"], st["ordered_by"], st["ordered_on"],
            ", ".join(st["spec"]), st["verification"]), ""]
    if s.get("earlier"):
        L += ["A re-run of acceptance record %s." % s["earlier"], ""]
    L += ["## Verdict", ""] + ["    " + ln for ln in s["verdict"]["lines"]] + [""]
    if RULES["readme_commits"]:
        L += ["## What ran", "",
              "- list: `%s` at %s, sha256 %s" % (s["list"]["path"], s["list"]["commit"],
                                                 s["list"]["sha256"]),
              "- runner (shared/bench): %s%s" % (s["runner"]["commit"],
                                                 " (DIRTY)" if s["runner"].get("dirty") else "")]
        for name, t in sorted(s["trees"].items()):
            L.append("- tree %s: project %s (tree %s), product digest %s" % (
                name, t["project"]["commit"], t["project"]["tree_id"], t["product_digest"]))
            for c, sc in sorted(t["shared"].items()):
                L.append("  - shared %s: %s (tree %s)" % (c, sc["commit"], sc["tree_id"]))
        for b, br in sorted(s["builds"].items()):
            L.append("- build %s: %s, variant %s, toolchain %s, ELF sha256 %s" % (
                b, br["verdict"], br.get("variant"), br.get("toolchain"),
                (br.get("elf") or {}).get("sha256")))
            for n, i in sorted((br.get("images") or {}).items()):
                L.append("  - image %s: sha256 %s" % (n, i["sha256"]))
        L.append("")
    if RULES["readme_items"]:
        L += ["## Items", "", "| kind | id | expected | got | status | detail |",
              "|---|---|---|---|---|---|"]
        for i in s["verdict"]["items"]:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                i["kind"], i["id"], i["expected"], i["got"], i["status"],
                i.get("why") or i.get("detail") or ""))
        if RULES["readme_not_passed"]:
            L += ["", "## Results that are not a pass", "",
                  "Every result of each check and suite run that is not a plain PASS, "
                  "in the order run, for checking against the known fails.", ""]
            for i in s["verdict"]["items"]:
                if "not_passed" not in i:
                    continue
                got = i["not_passed"] if RULES["not_passed_all"] else i["not_passed"][:6]
                L.append("- %s %s: %s" % (i["kind"], i["id"],
                                          "%d" % len(i["not_passed"]) if got else "none"))
                L += ["  - %s %s" % (rid, v) for rid, v in got]
        L += ["", "## Comparisons", ""]
        for c in s["compares"]:
            L.append("- compare %s (%s): %s" % (c["id"], c["expect"],
                                                "held" if c["held"] else "DID NOT HOLD"))
            L += ["  - %s" % d for d in c["counted"]]
            # every other difference the comparer saw, unjudged figures
            # included, so both sides' values are in the record (the
            # charge-interposer reviewer, 2026-11-04)
            if RULES["compare_all"]:
                L += ["  - seen, not counted: %s" % d for d in c.get("all") or []
                      if d not in c["counted"]]
        L += ["", "## Premises", ""]
        for p in s["premises"]:
            L.append("- premise %s: %s -- %s" % (p["id"], "held" if p["held"] else "DID NOT HOLD",
                                                 p["why"]))
        L.append("")
    rr = _rerun_lines(s)
    if rr:
        L += ["## Flagged cases: first run and re-run", "",
              "| suite run | case | first | re-run |", "|---|---|---|---|"] + rr + [
            "", "The first, whole run decides (A12).", ""]
    L += ["## Identifier scan", ""]
    if hits is None:
        L.append("- no identifier scan: the adapter gives none")
    elif not hits:
        L.append("- no hits")
    else:
        L += ["- %s" % h for h in hits]
    L += ["", "## Leftovers (A14)", ""]
    L += ["- LEFTOVER %s -- %s" % (p, why) for p, why in leftovers] or ["- none"]
    L.append("")
    return "\n".join(L)


def skeleton(rec, summary):
    s = summary
    st = s["step"]
    L = []
    if s.get("dev") and RULES["dev_marked"]:
        L += ["DEV RUN, not evidence: the runner ran with --dev, so nothing here goes "
              "into VERIFICATION.md (gen-inhibit's reviewer, 2026-11-01).", ""]
    L += ["Draft facts for the VERIFICATION.md entry of step %s -- the tester writes the "
          "entry; nothing here is the entry." % st["id"], "",
         "- section: %s; spec %s" % (st["verification"], ", ".join(st["spec"])),
         "- acceptance record: %s" % rec.id,
         "- list: %s at %s (sha256 %s)" % (s["list"]["path"], s["list"]["commit"],
                                           s["list"]["sha256"]),
         "- runner (shared/bench): %s" % s["runner"]["commit"]]
    for name, t in sorted(s["trees"].items()):
        L.append("- tree %s: project %s, product digest %s%s" % (
            name, t["project"]["commit"], t["product_digest"],
            "".join("; %s %s" % (c, sc["commit"]) for c, sc in sorted(t["shared"].items()))))
    counts = {}
    for i in s["verdict"]["items"]:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    L.append("- items: %s" % ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    L += ["- verdict:"] + ["    " + ln for ln in s["verdict"]["lines"]]
    if RULES["skeleton_reruns"]:
        for sr, rows in sorted((s.get("reruns") or {}).items()):
            for r in rows:
                L.append("- suite run %s, flagged %s: first %s, re-run %s" % (
                    sr, r["check"], r["first"], r["rerun"] or "(not re-run)"))
    return "\n".join(L) + "\n"


def close(rec, summary, made, adapter, out=None):
    """Clean up, scan, write and close the record (see the module's
    docstring). {"leftovers", "hits", "row", "late"}."""
    say = out or rec.say
    if getattr(rec, "closed", False) and RULES["close_once"]:
        raise CloseError("record %s is already closed: a re-run is a new record" % rec.id)
    leftovers = list(made.remove_all(out=say)) if RULES["cleanup_first"] else []
    scan = getattr(adapter, "identifier_scan", None)
    if scan is None and RULES["scan_absent"]:
        hits = None
    else:
        try:
            hits = list(scan(rec.path)) if scan is not None else []
        except Exception as e:
            if not RULES["scan_failure"]:
                raise
            hits = ["identifier scan FAILED: %s: %s" % (type(e).__name__, e)]
        if not RULES["scan_hits"]:
            hits = []
    _write(rec, "README.md", readme(rec, summary, hits,
                                   leftovers if RULES["leftovers_in"] else []))
    _write(rec, "verification_skeleton.md", skeleton(rec, summary))
    if not RULES["cleanup_first"]:
        leftovers = list(made.remove_all(out=say))
    if RULES["console_stops"]:
        rec.say("record %s closed: %s" % (rec.id, summary["verdict"]["lines"][0]))
        rec.close()
    rows = []
    for d, _dirs, files in os.walk(rec.path):
        for n in sorted(files):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, rec.path).replace("\\", "/")
            if rel != "SHA256SUMS":
                rows.append((rel, _sha256(p)))
    if not RULES["sums_all"]:
        rows = [r for r in rows if r[0] != "console.txt"]
    _write(rec, "SHA256SUMS", "".join("%s  %s\n" % (h, rel) for rel, h in sorted(rows)))
    rec.closed = True
    late, row = [], None
    try:
        if RULES["row"]:
            row = adapter.artifacts_row(rec.path, summary)
    except Exception as e:
        late.append("artifacts README row: %s" % e)
    for ln in late:
        msg = "after the close of %s (not written into it): %s" % (rec.id, ln)
        if RULES["late_outside"]:
            say(msg)
        else:
            with open(os.path.join(rec.path, "README.md"), "a", encoding="utf-8") as f:
                f.write(msg + "\n")
    for ln in summary["verdict"]["lines"]:
        say(ln)
    return {"leftovers": leftovers, "hits": hits, "row": row, "late": late}
