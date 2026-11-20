"""Print this session's context size. Read-only: it reads the session's own transcript.

    <python> .claude/tools/check_context.py [--mark TOKENS]

The size is the input, cache read, cache write and output tokens of the
session's last API call, which is what the next call carries. If a compaction
came after that call, the compaction's own after-size is printed instead.
--mark TOKENS adds a line saying whether the context is above it.
The exit code is 0 whenever it could read the transcript, 2 when it could not.
"""
import argparse
import glob
import json
import os
import sys

TAIL_BYTES = 4 * 1024 * 1024


def fail(msg):
    print("check_context: " + msg)
    sys.exit(2)


def find_transcript(sid):
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    hits = glob.glob(os.path.join(base, "projects", "*", sid + ".jsonl"))
    if len(hits) != 1:
        fail("expected one transcript for session %s, found %d" % (sid, len(hits)))
    return hits[0]


def records(path, start):
    with open(path, "rb") as f:
        f.seek(start)
        if start:
            f.readline()  # skip the partial line
        for raw in f:
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def last_context(path):
    size = os.path.getsize(path)
    start = max(0, size - TAIL_BYTES)
    while True:
        call = boundary = None
        for r in records(path, start):
            if r.get("type") == "assistant" and not r.get("isSidechain"):
                m = r.get("message") or {}
                u = m.get("usage")
                if u and m.get("model") != "<synthetic>":
                    call, boundary = (r.get("timestamp"), u), None
            elif r.get("type") == "system" and r.get("subtype") == "compact_boundary":
                boundary = (r.get("timestamp"), r.get("compactMetadata") or {})
        if call or boundary or start == 0:
            return call, boundary
        start = max(0, start - 8 * TAIL_BYTES)


def main():
    ap = argparse.ArgumentParser(description="Print this session's context size.")
    ap.add_argument("--mark", type=int, help="say whether the context is above this many tokens")
    a = ap.parse_args()
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        fail("CLAUDE_CODE_SESSION_ID is unset; run this as a Claude Code tool call")
    call, boundary = last_context(find_transcript(sid))
    tokens = None
    if boundary:
        when, meta = boundary
        tokens = meta.get("postTokens")
        print("context: compacted at %s, %s tokens after it (no API call since)"
              % (when, "{:,}".format(tokens) if tokens else "unknown"))
    elif call:
        when, u = call
        tokens = sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                 "cache_creation_input_tokens", "output_tokens"))
        print("context: {:,} tokens at the last API call ({})".format(tokens, when))
    else:
        print("context: no API call recorded yet")
    if a.mark is not None and tokens:
        print("mark: %s %s" % ("above" if tokens > a.mark else "at or below", "{:,}".format(a.mark)))


if __name__ == "__main__":
    main()
