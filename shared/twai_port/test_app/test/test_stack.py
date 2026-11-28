#!/usr/bin/env python3
"""Hold the probe's frames, and its call path from the URI handler, to a budget on the host.

The implementor's (SPEC.md T11, T13). The probe runs on the caller's task:
on gen-inhibit that is the httpd task, with a 5120-byte stack. Its call
path is the URI handler (twai_port_txabort_http.c), twai_port_txabort_run()
and the older phases (twai_port_txabort_probe.c), and m1-m3's parse, run and
answer (twai_port_unacked.c). Twice it overflowed that stack, and every
m1-m3 call, then 10 of 140, ended in a restart (gen-inhibit VERIFICATION.md
12.4, 2026-11-28 and 2026-11-27):
- at shared/main 3bb6c945 the answer's buffers were on the stack
  (txp_write_json(), 3520 bytes on the C3);
- at 9d1dc134 the handler's and the run's were (576 and 2016 bytes on the
  C3), and stack_free_min read 44-108 bytes. Under the probe's frames the
  C3 stack also holds httpd (about 460 bytes), newlib's snprintf() (about
  1330) and an interrupt's 128.

    python3 test_stack.py <file.ci>...

The .ci files are gcc's -fcallgraph-info=su output for those three files,
which the Makefile writes to OUT. Three checks:
- every function's frame at most FRAME_MAX bytes and bounded;
- the deepest path from ROOT through the three files at most PATH_MAX bytes;
- the deepest such path that leaves out the older phases (OLD), so m1-m3's
  path, at most M_PATH_MAX bytes.
A call to a function outside the three files counts 0 bytes. An indirect
call counts as a call to the deepest of INDIRECT, the transport's callbacks
the probe calls through txab_io_t. A function with no node (inlined) adds
its frame to its caller's, which gcc has already counted.

Host frames differ from the C3's: on x86-64 they run about 1.4 times the
C3's. The limits are budgets, not measurements of the target: on the C3,
1 KB free needs the probe's frames on the path to stay under about 2 KB, and
M_PATH_MAX allows about 730 C3 bytes. Any of the probe's big buffers back
on the stack goes over a limit. The checks fail on 3bb6c945 and 9d1dc134.
"""
import os
import re
import sys

FRAME_MAX = 768
PATH_MAX = 1536
M_PATH_MAX = 1024
ROOT = "txab_handler"
OLD = "txab_old_run"
INDIRECT = ("http_get", "http_emit")

NODE = re.compile(r'^node: \{ title: "([^"]+)" label: "([^"]*)"')
EDGE = re.compile(r'^edge: \{ sourcename: "([^"]+)" targetname: "([^"]+)"')
SIZE = re.compile(r'\\n(\d+) bytes \(([^)]*)\)$')


def load(paths):
    """{title: (short name, bytes, kind, file)}, {title: set(callee titles)}."""
    nodes, edges = {}, {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = NODE.match(line)
                if m:
                    s = SIZE.search(m.group(2))
                    if s:   # defined here; a node with no size is a callee
                        name = m.group(2).split("\\n")[0]
                        nodes[m.group(1)] = (name, int(s.group(1)), s.group(2), path)
                    continue
                m = EDGE.match(line)
                if m:
                    edges.setdefault(m.group(1), set()).add(m.group(2))
    return nodes, edges


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.split("\n")[0] + "\nusage: test_stack.py <file.ci>...")
    nodes, edges = load(sys.argv[1:])
    by_name = {}
    for t, (name, _, _, _) in nodes.items():
        by_name.setdefault(name, []).append(t)

    def callees(t):
        """Defined callee titles: by title in the same file, else by name."""
        out = set()
        for c in edges.get(t, ()):
            if c in nodes:
                out.add(c)
            elif c == "__indirect_call":
                for n in INDIRECT:
                    out.update(by_name.get(n, ()))
            else:
                out.update(by_name.get(c, ()))
        return out

    def deepest(t, skip, seen):
        """(bytes, [titles]) of the deepest path from t, leaving out skip."""
        if t in seen:       # recursion: not followed again
            return 0, []
        best = (0, [])
        for c in callees(t):
            if nodes[c][0] in skip:
                continue
            d = deepest(c, skip, seen | {t})
            if d[0] > best[0]:
                best = d
        return nodes[t][1] + best[0], [t] + best[1]

    bad = 0
    for t, (name, n, kind, path) in sorted(nodes.items()):
        # "dynamic,bounded" (a variadic call's spill, say) is fine within the
        # limit; plain "dynamic" has no bound at all.
        if n > FRAME_MAX or kind not in ("static", "dynamic,bounded"):
            print("  FAIL %s (%s): %d bytes, %s (limit %d, bounded)" % (
                name, os.path.basename(path), n, kind, FRAME_MAX))
            bad += 1
    if not nodes:
        print("  FAIL no functions in %s" % " ".join(sys.argv[1:]))
        bad += 1
    roots = by_name.get(ROOT, [])
    if len(roots) != 1:
        print("  FAIL %d functions named %s, not 1" % (len(roots), ROOT))
        return 1
    for label, skip, limit in (("any path", (), PATH_MAX),
                               ("m1-m3 path (no %s)" % OLD, (OLD,), M_PATH_MAX)):
        total, path = deepest(roots[0], skip, frozenset())
        print("  %s: %d bytes (limit %d): %s" % (
            label, total, limit,
            " > ".join("%s %d" % (nodes[t][0], nodes[t][1]) for t in path)))
        if total > limit:
            print("  FAIL %s: %d bytes, over %d" % (label, total, limit))
            bad += 1
    print("stack: %d function(s), %s: %d failure(s)" % (len(nodes), "PASS" if bad == 0 else "FAIL", bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
