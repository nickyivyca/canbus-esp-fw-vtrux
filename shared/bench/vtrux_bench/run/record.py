"""The run's record: one folder per run, never overwritten.

    <runs_root>/<suite>_<YYYYmmddTHHMMSS>_<hex4>/
        progress.jsonl   every event and result as it happens (append only)
        record.json      the whole run, written once at the end
        index.txt        every check with its verdict, for a person
        <group>/<check>/ each check's own log and outputs
        PAUSE, STOP      written by a person or a session to steer the run

The folder is created with mkdir (never reused) and record.json / index.txt
with exclusive create, so a second run can never overwrite a first.
"""

import datetime
import json
import os
import secrets
import threading

from .model import Figure, Tol, Verdict

SCHEMA = 1


def _now():
    return datetime.datetime.now().astimezone()


def stamp(t=None):
    return (t or _now()).isoformat(timespec="seconds")


def new_run_dir(runs_root, suite_name, run_id=None):
    """Create the run folder. Refuses (FileExistsError) if it exists."""
    if run_id is None:
        run_id = "%s_%s_%s" % (suite_name, _now().strftime("%Y%m%dT%H%M%S"),
                               secrets.token_hex(2))
    path = os.path.join(runs_root, run_id)
    os.makedirs(runs_root, exist_ok=True)
    os.mkdir(path)          # FileExistsError if it is already there
    return run_id, path


def write_new(path, text):
    """Exclusive create: never replaces an existing file."""
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _jsonable(o):
    if isinstance(o, (Figure, Tol)):
        return {k: v for k, v in vars(o).items()}
    raise TypeError("not JSON: %r" % (o,))


class Recorder:
    """Thread-safe: every lane writes through one recorder."""

    def __init__(self, run_dir, run_id):
        self.run_dir = run_dir
        self.run_id = run_id
        self.results = []
        self.events = []
        self._lock = threading.Lock()
        self._progress = open(os.path.join(run_dir, "progress.jsonl"), "x",
                              encoding="utf-8", newline="\n")

    def _append(self, obj):
        self._progress.write(json.dumps(obj, default=_jsonable) + "\n")
        self._progress.flush()

    def event(self, kind, **fields):
        e = dict(type="event", kind=kind, t=stamp(), **fields)
        with self._lock:
            self.events.append(e)
            self._append(e)
        return e

    def result(self, **fields):
        r = dict(type="result", t=stamp(), **fields)
        assert r["verdict"] in Verdict.ALL, r["verdict"]
        with self._lock:
            self.results.append(r)
            self._append(r)
        return r

    def amend(self, r):
        """Record a change to a result already written (a group made VOID
        after the fact), so progress.jsonl stays the truth for --resume."""
        a = dict(type="amend", t=stamp(), id=r["id"], check=r["check"],
                 verdict=r["verdict"], detail=r.get("detail", ""),
                 judged=r.get("judged"))
        with self._lock:
            self._append(a)

    def close(self):
        with self._lock:
            self._progress.close()


def load_progress(run_dir):
    out = []
    with open(os.path.join(run_dir, "progress.jsonl"), encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def _short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _fig_text(figs):
    if not figs:
        return ""
    parts = []
    for k, f in figs.items():
        v = f["value"] if isinstance(f, dict) else f.value
        u = f.get("unit", "") if isinstance(f, dict) else f.unit
        parts.append("%s=%s%s" % (k, v, u))
    return " ".join(parts)


def index_text(record):
    """Every check with its verdict; known FAILs and changes called out."""
    res = record["results"]
    lines = list(record.get("not_on_standing") or []) + [
             "Run %s -- suite %s" % (record["run_id"], record["suite"]),
             "started %s, ended %s" % (record["started"], record["ended"]),
             "code: %s" % json.dumps(record.get("code", {}), sort_keys=True)]
    code = record.get("code") or {}
    if code.get("product_digest"):
        lines.append("product digest (the code identity): %s over %s file(s)"
                     % (code["product_digest"], code.get("product_files")))
    h = record.get("harness")
    if h:
        lines.append("harness (recorded, not gated): commit %s, dirty %s, "
                     "artifacts_sha256 %s, ship lists %s" % (
                         h.get("commit"), h.get("dirty"),
                         h.get("artifacts_sha256"),
                         ", ".join("%s %s" % (k, v[:12]) for k, v in
                                   sorted((h.get("ship_lists") or {}).items()))
                         or "none"))
    if record.get("requires_met"):
        lines.append("requires: %s" % record["requires_met"])
    if record.get("subset"):
        cases = record.get("subset_cases") or []
        sel = record.get("selection") or {}
        lines.append("SUBSET RUN (--group %s --check %s): evidence for these "
                     "%d case(s) alone, never a whole-suite run: %s" % (
                         sel.get("groups"), sel.get("checks"), len(cases),
                         ", ".join(cases)))
    else:
        lines.append("whole suite: no --group or --check selection")
    lines.append("")
    counts = {}
    for r in res:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines.append("Verdicts: " + ", ".join(
        "%s %d" % (v, counts[v]) for v in Verdict.ALL if v in counts))
    flagged = [r for r in res if r.get("known_fail_passed")]
    if flagged:
        lines.append("KNOWN FAILS THAT NOW PASS (a change -- registry "
                     "entry to review): " + ", ".join(r["id"] for r in flagged))
    if record.get("unmatched_known_fails"):
        lines.append("KNOWN-FAIL REGISTRY ENTRIES NOT PRODUCED by any selected "
                     "check (renamed or removed?): "
                     + ", ".join(record["unmatched_known_fails"]))
    lines.append("")
    lines.append("Groups (wall clock, lock):")
    for g in record["groups"]:
        lines.append("  %-28s %-16s lock=%-3s %8.0f s  %s" % (
            g["name"], g["lane"], "yes" if g["needs_lock"] else "no",
            g.get("wall_s") or 0.0, g.get("status", "")))
    lines.append("")
    lines.append("%-15s %-60s %s" % ("verdict", "id", "detail"))
    for r in res:
        extra = []
        if r.get("known_fail"):
            extra.append("[known: %s]" % _short(r["known_fail"], 60))
        if r.get("carried_from"):
            extra.append("[carried from %s]" % r["carried_from"])
        ft = _fig_text(r.get("figures"))
        if ft:
            extra.append("{%s}" % _short(ft, 80))
        lines.append("%-15s %-60s %s %s" % (
            r["verdict"], r["id"], _short(r.get("detail", ""), 100),
            " ".join(extra)))
    return "\n".join(lines) + "\n"


def write_record(recorder, header, groups, unmatched):
    rec = dict(header)
    rec.update(schema=SCHEMA, run_id=recorder.run_id, ended=stamp(),
               groups=groups, results=recorder.results,
               events=recorder.events, unmatched_known_fails=unmatched)
    write_new(os.path.join(recorder.run_dir, "record.json"),
              json.dumps(rec, indent=1, default=_jsonable, sort_keys=True))
    write_new(os.path.join(recorder.run_dir, "index.txt"), index_text(rec))
    return rec


def _within(a, b, tol):
    if a == b:
        return True
    if not tol or (tol.get("abs") is None and tol.get("rel") is None):
        return False
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if tol.get("abs") is not None and abs(a - b) <= tol["abs"]:
        return True
    if tol.get("rel") is not None and abs(a - b) <= tol["rel"] * abs(b):
        return True
    return False


def compare(ref, new):
    """Differences of `new` against the reference run `ref` (both records).
    Tolerances come from the reference's results. Returns a list of lines;
    empty means the same results."""
    out = []
    a = {r["id"]: r for r in ref["results"]}
    b = {r["id"]: r for r in new["results"]}
    for i in sorted(set(a) | set(b)):
        if i not in b:
            out.append("MISSING in new: %s (was %s)" % (i, a[i]["verdict"]))
            continue
        if i not in a:
            out.append("NEW check: %s %s" % (i, b[i]["verdict"]))
            continue
        if a[i]["verdict"] != b[i]["verdict"]:
            out.append("VERDICT %s: %s -> %s" % (i, a[i]["verdict"],
                                                 b[i]["verdict"]))
        fa, fb = a[i].get("figures") or {}, b[i].get("figures") or {}
        tols = a[i].get("tolerances") or {}
        for k in sorted(set(fa) | set(fb)):
            if k not in fa or k not in fb:
                out.append("FIGURE %s %s: present in only one run" % (i, k))
                continue
            if not _within(fb[k]["value"], fa[k]["value"], tols.get(k)):
                out.append("FIGURE %s %s: %s -> %s (tolerance %s)" % (
                    i, k, fa[k]["value"], fb[k]["value"], tols.get(k)))
    return out
