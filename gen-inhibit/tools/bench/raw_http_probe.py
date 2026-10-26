#!/usr/bin/env python3
"""One HTTP request over a raw socket, recording everything urllib hides.

Written 2026-10-14 for the reviewer's question about the INHIBIT POST from
OBSERVE: "does the reply arrive late, or never?" On 2026-10-12 two such POSTs
(dace947, OBSERVE arms 1 and 5) ended with the poster thread dying on an
exception `gi()` does not catch -- not a timeout, not a reset, not a refusal,
since those are OSError and are caught -- and the batch log's grep dropped the
exception's own line. So urllib could say only that it failed, not how.

This sends the same request urllib sends (same headers, same order) and keeps:

  t_connected, t_sent      when the TCP connect finished and the request left
  chunks                   every recv(), [t, nbytes], so a late or split reply shows
  t_first_byte, t_end      first response byte; when reading stopped
  t_complete               when the bytes reached headers + Content-Length (if any)
  ending                   how reading stopped: "fin" (peer closed), "rst"
                           (ConnectionResetError/AbortedError), "timeout",
                           "complete_open" (full reply, peer still open after
                           AFTER_COMPLETE_S), or "connect_error"
  status_line, headers, content_length, body_len, body_json_ok, raw
  http_client              what Python's http.client does with exactly these
                           bytes and this ending -- the exception class urllib
                           would have raised, or "ok". This is the direct answer
                           to which exception escaped `gi()` on 2026-10-12, for a
                           reply that comes back the same way.

All times are seconds on `clock()` minus `base`, so they share the caller's clock
(e4_device_calibration.py passes the replay's base). `epoch_sent` is time.time()
at the send, for placing it against the Kvaser's timestamps (epoch).

Tested by test_raw_http_probe.py against a local fake server.
"""

import http.client
import io
import json
import socket
import sys
import time

AFTER_COMPLETE_S = 2.0


def urllib_request_bytes(host, path, method="POST", version=None):
    """The request urllib.request sends for gi(path, method="POST"): data=b"".

    Header order as urllib emits it: putrequest adds Accept-Encoding (urllib
    supplies Host itself, so putrequest skips it), then urllib's headers in its
    own order -- Content-Type and Content-Length (do_request_), Host, User-Agent,
    then Connection (do_open). test_raw_http_probe.py checks these bytes against
    what urllib actually sends to a local server.
    """
    if version is None:
        version = "%d.%d" % sys.version_info[:2]
    lines = ["%s %s HTTP/1.1" % (method, path),
             "Accept-Encoding: identity"]
    if method == "POST":
        lines += ["Content-Type: application/x-www-form-urlencoded",
                  "Content-Length: 0"]
    lines += ["Host: %s" % host,
              "User-Agent: Python-urllib/%s" % version,
              "Connection: close"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def split_reply(raw):
    """(status_line, headers dict lower-cased, body bytes, header_end) or Nones."""
    i = raw.find(b"\r\n\r\n")
    if i < 0:
        return None, None, None, None
    head = raw[:i].decode("latin-1").split("\r\n")
    hdrs = {}
    for h in head[1:]:
        if ":" in h:
            k, v = h.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    return head[0], hdrs, raw[i + 4:], i + 4


def dechunk(body):
    """Decode a chunked body as far as it goes: (payload, complete)."""
    out, pos = b"", 0
    while True:
        j = body.find(b"\r\n", pos)
        if j < 0:
            return out, False
        try:
            n = int(body[pos:j].split(b";")[0], 16)
        except ValueError:
            return out, False
        if n == 0:
            return out, True
        if len(body) < j + 2 + n:
            return out + body[j + 2:], False
        out += body[j + 2:j + 2 + n]
        pos = j + 2 + n + 2


class _ReplayRaw(io.RawIOBase):
    """The recorded bytes, then the recorded ending, as a raw socket stream.

    Wrapped in io.BufferedReader exactly as socket.makefile("rb") wraps a real
    socket, because the buffering decides which exception surfaces: a RST after
    a partial body raises ConnectionResetError out of BufferedReader.read(n), not
    IncompleteRead -- a plain BytesIO replay got that wrong (caught by the
    cut_then_rst case against real urllib, 2026-10-14)."""

    def __init__(self, raw, ending):
        self.buf = io.BytesIO(raw)
        self.ending = ending

    def readable(self):
        return True

    def readinto(self, b):
        n = self.buf.readinto(b)
        if n:
            return n
        if self.ending == "rst":
            raise ConnectionResetError("replayed RST")
        if self.ending == "timeout":
            raise socket.timeout("replayed timeout")
        return 0


class _ReplaySock:
    """Hands http.client the recorded bytes, then the recorded ending."""

    def __init__(self, raw, ending):
        self.raw = _ReplayRaw(raw, ending)

    def makefile(self, mode, *a, **k):
        return io.BufferedReader(self.raw)

    def close(self):
        pass


def http_client_verdict(raw, ending, method="POST"):
    """What http.client (so urllib) makes of these bytes: "ok" or the exception
    class name, and whether gi() would have caught it (OSError) or not."""
    if ending == "connect_error":
        return {"result": "connect_error", "caught_by_gi": True}
    r = http.client.HTTPResponse(_ReplaySock(raw, ending), method=method)
    try:
        r.begin()
        r.read()
        return {"result": "ok", "caught_by_gi": None, "status": r.status}
    except Exception as e:
        return {"result": type(e).__name__, "caught_by_gi": isinstance(e, OSError),
                "detail": str(e)[:120]}


def raw_request(host, path, timeout=30.0, method="POST", port=80, base=0.0,
                clock=time.perf_counter, after_complete_s=AFTER_COMPLETE_S):
    rec = {"host_port": port, "path": path, "timeout_s": timeout,
           "t_start": round(clock() - base, 4), "t_connected": None, "t_sent": None,
           "epoch_sent": None, "t_first_byte": None, "t_complete": None,
           "t_end": None, "ending": None, "error": None, "local_port": None, "chunks": []}
    raw = b""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        rec.update(ending="connect_error", error="%s: %s" % (type(e).__name__, e),
                   t_end=round(clock() - base, 4))
        return finish(rec, raw)
    try:
        rec["t_connected"] = round(clock() - base, 4)
        # The client port names this connection in a packet capture.
        rec["local_port"] = s.getsockname()[1]
        s.sendall(urllib_request_bytes(host if port == 80 else "%s:%d" % (host, port),
                                       path, method))
        t_sent = clock()
        rec["t_sent"] = round(t_sent - base, 4)
        rec["epoch_sent"] = time.time()
        deadline = t_sent + timeout
        want = None
        while True:
            now = clock()
            if rec["t_complete"] is not None:
                left = min(deadline, t_complete_abs + after_complete_s) - now
            else:
                left = deadline - now
            if left <= 0:
                rec["ending"] = "complete_open" if rec["t_complete"] is not None else "timeout"
                break
            s.settimeout(left)
            try:
                d = s.recv(65536)
            except socket.timeout:
                rec["ending"] = "complete_open" if rec["t_complete"] is not None else "timeout"
                break
            except (ConnectionResetError, ConnectionAbortedError) as e:
                rec["ending"] = "rst"
                rec["error"] = "%s: %s" % (type(e).__name__, e)
                break
            t = clock()
            if not d:
                rec["ending"] = "fin"
                break
            if rec["t_first_byte"] is None:
                rec["t_first_byte"] = round(t - base, 4)
            rec["chunks"].append([round(t - base, 4), len(d)])
            raw += d
            if want is None:
                _, hdrs, _, hend = split_reply(raw)
                if hdrs is not None and "content-length" in hdrs:
                    try:
                        want = hend + int(hdrs["content-length"])
                    except ValueError:
                        want = -1
            if rec["t_complete"] is None and want is not None and want >= 0 and len(raw) >= want:
                rec["t_complete"] = round(t - base, 4)
                t_complete_abs = t
        rec["t_end"] = round(clock() - base, 4)
    finally:
        s.close()
    return finish(rec, raw)


def finish(rec, raw):
    status_line, hdrs, body, _ = split_reply(raw)
    rec["raw_len"] = len(raw)
    rec["status_line"] = status_line
    rec["headers"] = hdrs
    cl = None
    if hdrs and "content-length" in hdrs:
        try:
            cl = int(hdrs["content-length"])
        except ValueError:
            cl = hdrs["content-length"]
    rec["content_length"] = cl
    chunked = bool(hdrs) and "chunked" in hdrs.get("transfer-encoding", "").lower()
    rec["chunked"] = chunked
    payload, chunk_ok = (dechunk(body) if chunked else (body, None))
    rec["chunked_complete"] = chunk_ok
    rec["body_len"] = None if body is None else len(body)
    rec["payload_len"] = None if payload is None else len(payload)
    rec["cl_matches"] = (None if not isinstance(cl, int) or body is None
                         else len(body) == cl)
    ok = None
    if payload is not None:
        try:
            json.loads(payload.decode("utf-8"))
            ok = True
        except (ValueError, UnicodeDecodeError):
            ok = False
    rec["body_json_ok"] = ok
    rec["raw"] = raw.decode("latin-1")
    rec["http_client"] = http_client_verdict(raw, rec["ending"])
    return rec


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path")
    ap.add_argument("--host", default="192.168.80.1")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()
    r = raw_request(a.host, a.path, a.timeout, a.method)
    r.pop("raw")
    print(json.dumps(r, indent=1))
