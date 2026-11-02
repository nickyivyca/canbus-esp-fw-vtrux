"""The acceptance runner's comparisons, premises and verdict (ACCEPT-SPEC
A10's comparisons, A11, A12).

Comparisons (A10). A [[compare]] names a comparer, committed here or in the
adapter, its two sides and what must hold. The core's comparers:
  run-records  two run records (record.json): each case's verdict, a case in
               one only, and each figure
  transcript   two check logs, line by line, after the masks: "re:<regex>",
               or a name from the adapter's MASKS (a tree's root, say)
An adapter's comparer (COMPARERS, comparer(name)) is called the same way and
gives its differences the same way, as {"kind", "line"}. What must hold:
  identical      no difference at all
  same-verdicts  no case's verdict differs, and no case is in one side only
  within         as same-verdicts, and every figure within the list's
                 tolerance for it ({name or "*": {"abs", "rel"}})
  listed         the differences are exactly the list's, one by one: each
                 listed one is there, and nothing else is

Premises (A11). A premise is what must hold before the results count:
  product_digest_equal    the tree's product digest is the value
  product_digest_same_as  the tree's product digest is the one at a commit,
                          worked out from git objects
  board_read              not built yet: it does not hold, saying so
A premise that fails makes its items (all of them, if it names none) NOT
EXERCISED, never PASS or FAIL.

The verdict (A12). ACCEPTANCE PASS only if every premise held, every check
gave its expected verdict, every comparison held, every can-fail behaved as
stated (and every build and suite run passed); otherwise each item that did
not is listed.

A check's verdict is that of all its results, whatever their order
(check_verdict; the charge-interposer reviewer, 2026-11-02: an ERROR after a
FAIL had been taken as the FAIL a list expected). PASS when every result is
PASS, KNOWN-FAIL or NOT-APPLICABLE, as the chain runner counts them, and no
known FAIL passed. Otherwise the first of these that any result gives: ERROR,
VOID, NOT-RUN, INCONCLUSIVE, a known FAIL that passed, FAIL. So FAIL only
when every result that is not a pass is a FAIL. Every result that is not a
plain PASS is listed in the record, not only the first six (not_passed).

RULES: each is switched off by a mutant in tests/test_accept_judge.py, which
must then fail.
"""

import difflib
import io
import re
import subprocess
import tarfile

from .. import scratch as S
from ..run.engine import product_digest

RULES = {k: True for k in (
    "count_verdicts", "count_figures", "verdicts_only", "missing_new", "tolerance",
    "listed_exact", "listed_seen", "masks", "adapter_comparer", "premise_equal",
    "premise_same_as", "not_exercised", "all_items", "expected_verdict", "every_item",
    "every_result", "ok_set", "known_passed", "listed_ok_set", "listed_known_passed")}

VERDICT_KINDS = ("verdict", "missing", "new")


# ---- comparers --------------------------------------------------------------
def compare_records(a, b, spec, adapter):
    ra = {r["id"]: r for r in a["record"]["results"]}
    rb = {r["id"]: r for r in b["record"]["results"]}
    out = []
    for i in sorted(set(ra) | set(rb)):
        if i not in rb:
            out.append({"kind": "missing", "line": "MISSING in b: %s (was %s)"
                        % (i, ra[i]["verdict"])})
            continue
        if i not in ra:
            out.append({"kind": "new", "line": "NEW in b: %s %s" % (i, rb[i]["verdict"])})
            continue
        if ra[i]["verdict"] != rb[i]["verdict"]:
            out.append({"kind": "verdict", "line": "VERDICT %s: %s -> %s"
                        % (i, ra[i]["verdict"], rb[i]["verdict"])})
        fa, fb = ra[i].get("figures") or {}, rb[i].get("figures") or {}
        for k in sorted(set(fa) | set(fb)):
            if k not in fa or k not in fb:
                out.append({"kind": "figure", "line": "FIGURE %s %s: in one side only"
                            % (i, k), "name": k})
            elif fa[k]["value"] != fb[k]["value"]:
                out.append({"kind": "figure", "line": "FIGURE %s %s: %s -> %s"
                            % (i, k, fa[k]["value"], fb[k]["value"]), "name": k,
                            "a": fa[k]["value"], "b": fb[k]["value"]})
    return out


def _masks(spec, adapter):
    out = []
    for m in spec.get("mask") or []:
        if m.startswith("re:"):
            out.append(re.compile(m[3:]))
        else:
            out.append(re.compile(getattr(adapter, "MASKS", {})[m]))
    return out


def compare_transcript(a, b, spec, adapter):
    masks = _masks(spec, adapter) if RULES["masks"] else []

    def lines(side):
        with open(side["log"], encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in masks:
            text = m.sub("<masked>", text)
        return text.splitlines()
    out = []
    for ln in difflib.unified_diff(lines(a), lines(b), lineterm="", n=0):
        if ln.startswith(("---", "+++", "@@")):
            continue
        out.append({"kind": "text", "line": ln})
    return out


BUILTIN = {"run-records": compare_records, "transcript": compare_transcript}


def _within(d, tol):
    t = tol.get(d.get("name")) or tol.get("*")
    if not t or "a" not in d:
        return False
    try:
        a, b = float(d["a"]), float(d["b"])
    except (TypeError, ValueError):
        return False
    if t.get("abs") is not None and abs(a - b) <= t["abs"]:
        return True
    return t.get("rel") is not None and abs(a - b) <= t["rel"] * abs(a)


def run_compare(spec, a, b, adapter):
    """{"id", "expect", "held", "counted" (the differences that count),
    "all" (every difference)}."""
    name = spec["comparer"]
    if RULES["adapter_comparer"] and name in getattr(adapter, "COMPARERS", ()):
        fn = adapter.comparer(name)
    else:
        fn = BUILTIN[name]
    diffs = list(fn(a, b, spec, adapter))
    expect = spec["expect"]
    if not RULES["count_verdicts"]:
        diffs = [d for d in diffs if d["kind"] != "verdict"]
    if not RULES["count_figures"]:
        diffs = [d for d in diffs if d["kind"] != "figure"]
    if not RULES["missing_new"]:
        diffs = [d for d in diffs if d["kind"] not in ("missing", "new")]
    if expect == "identical":
        counted = diffs
    elif expect == "same-verdicts":
        counted = [d for d in diffs if d["kind"] in VERDICT_KINDS] if RULES["verdicts_only"] \
            else diffs
    elif expect == "within":
        tol = spec.get("tolerance") or {}
        counted = [d for d in diffs if d["kind"] in VERDICT_KINDS or
                   (d["kind"] == "figure" and not (RULES["tolerance"] and _within(d, tol)))]
    else:                                                   # listed
        counted = diffs
    lines = [d["line"] for d in counted]
    if expect == "listed":
        want = list(spec.get("differences") or [])
        extra = [x for x in lines if x not in want] if RULES["listed_exact"] else []
        unseen = [x for x in want if x not in lines] if RULES["listed_seen"] else []
        held = not extra and not unseen
        counted_lines = ["not listed: " + x for x in extra] + ["listed, not there: " + x
                                                               for x in unseen]
        return {"id": spec["id"], "expect": expect, "held": held,
                "counted": counted_lines if not held else [], "all": lines}
    return {"id": spec["id"], "expect": expect, "held": not lines, "counted": lines,
            "all": [d["line"] for d in diffs]}


# ---- premises ---------------------------------------------------------------
def digest_at(repo, commit, adapter, parent, made):
    """The product digest at commit, from git objects (git archive), in a
    scratch folder of the run's own."""
    d = made.add(S.make(parent, "digest"))
    tar = subprocess.run(["git", "-C", repo, "archive", commit], capture_output=True,
                         check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        tf.extractall(d, filter="data")
    return product_digest(d, adapter.product_files(d))


def premises(ps, trees, repo, adapter, parent, made):
    """[{"id", "held", "why", "items"}], one per [[premise]]."""
    out = []
    for p in ps:
        kind, have = p.get("kind"), None
        if p.get("tree") in trees:
            have = trees[p["tree"]]["record"]["product_digest"]
        if kind == "product_digest_equal":
            held = have == p.get("value") or not RULES["premise_equal"]
            why = "the product digest of tree %s is %s, %s %s" % (
                p.get("tree"), (have or "?")[:12], "as" if held else "not", p.get("value", "")[:12])
        elif kind == "product_digest_same_as":
            want = digest_at(repo, p["commit"], adapter, parent, made)
            held = have == want or not RULES["premise_same_as"]
            why = "the product digest of tree %s is %s; at %s it is %s" % (
                p.get("tree"), (have or "?")[:12], p["commit"][:12], want[:12])
        else:
            held, why = False, "%s premises are not built yet" % kind
        out.append({"id": p.get("id"), "held": held, "why": why,
                    "items": list(p.get("items") or [])})
    return out


# ---- the verdict ------------------------------------------------------------
# what a check's results count as (the chain runner's own ok set), and the
# order in which the others decide its verdict
CHECK_OK = ("PASS", "KNOWN-FAIL", "NOT-APPLICABLE")
KNOWN_PASSED = "PASS, a known FAIL passed"
CHECK_RANK = ("ERROR", "VOID", "NOT-RUN", "INCONCLUSIVE", KNOWN_PASSED, "FAIL")


def check_verdict(results, group):
    """(verdict, detail) of the chain group's results (a list check, a suite
    run): see the module's text. "absent" if it has none; the detail names
    each result that is not a pass, up to six."""
    rs = [r for r in results if r["group"] == group]
    if not rs:
        return "absent", ""
    got = []
    for r in rs:
        v = r["verdict"]
        if RULES["known_passed"] and r.get("known_fail_passed"):
            v = KNOWN_PASSED
        ok = (v in CHECK_OK) if RULES["ok_set"] else (v == "PASS")
        if not ok:
            rid = r["id"][len(group) + 1:] if r["id"].startswith(group + "/") else r["id"]
            got.append((rid, v))
    if not got:
        return "PASS", ""
    if RULES["every_result"]:
        v = min((g[1] for g in got),
                key=lambda x: CHECK_RANK.index(x) if x in CHECK_RANK else -1)
    else:
        v = got[0][1]
    detail = ", ".join("%s %s" % g for g in got[:6]) + (
        ", and %d more" % (len(got) - 6) if len(got) > 6 else "")
    return v, detail


def not_passed(results, group=None):
    """Every result of the chain group (with group None, of every group: a
    nested suite run's own record, ids in full) that is not a plain PASS, in
    the run's order, as [(result id within the group, verdict)]: those the
    verdict counts, and also KNOWN-FAIL, NOT-APPLICABLE and a known FAIL that
    passed. The record lists them all (check_verdict's detail stops at six),
    for the tester's VERIFICATION.md entry and the reviewer to check against
    the known fails: the runner itself has no known-fail machinery (the user,
    2026-11-02, through the charge-interposer reviewer)."""
    out = []
    for r in results:
        if group is not None and r["group"] != group:
            continue
        v = r["verdict"]
        if r.get("known_fail_passed") and RULES["listed_known_passed"]:
            v = KNOWN_PASSED
        if v == "PASS" or (v in CHECK_OK and not RULES["listed_ok_set"]):
            continue
        rid = r["id"]
        if group is not None and rid.startswith(group + "/"):
            rid = rid[len(group) + 1:]
        out.append((rid, v))
    return out


def item(kind, item_id, expected, got, detail=""):
    return {"kind": kind, "id": item_id, "expected": expected, "got": got,
            "detail": detail}


def verdict(items, premise_results):
    """{"pass", "items" (each with "status": ok, not as expected, or NOT
    EXERCISED), "lines"}."""
    blocked = {}
    for p in premise_results:
        if p["held"] or not RULES["not_exercised"]:
            continue
        names = p["items"] or ([i["id"] for i in items] if RULES["all_items"] else [])
        for n in names:
            blocked.setdefault(n, p)
    out = []
    for i in items:
        i = dict(i)
        if i["id"] in blocked:
            p = blocked[i["id"]]
            i["status"] = "NOT EXERCISED"
            i["why"] = "premise %s did not hold: %s" % (p["id"], p["why"])
        elif (i["got"] == i["expected"]) if RULES["expected_verdict"] else (
                i["got"] in ("PASS", "held", "as stated")):
            i["status"] = "ok"
        else:
            i["status"] = "not as expected"
        out.append(i)
    judged = out if RULES["every_item"] else [i for i in out if i["kind"] == "check"]
    ok = all(i["status"] == "ok" for i in judged) and all(p["held"] for p in premise_results)
    lines = ["ACCEPTANCE PASS" if ok else "ACCEPTANCE FAIL"]
    for p in premise_results:
        if not p["held"]:
            lines.append("  premise %s did not hold: %s" % (p["id"], p["why"]))
    for i in out:
        if i["status"] == "NOT EXERCISED":
            lines.append("  NOT EXERCISED %s %s: %s" % (i["kind"], i["id"], i["why"]))
        elif i["status"] != "ok":
            lines.append("  %s %s: expected %s, got %s%s" % (
                i["kind"], i["id"], i["expected"], i["got"],
                (" (%s)" % i["detail"]) if i["detail"] else ""))
    return {"pass": ok, "items": out, "lines": lines}
