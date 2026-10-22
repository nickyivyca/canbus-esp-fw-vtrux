#!/usr/bin/env python3
"""Tests spec11_page_check.judge, and read_page against a local fake page.

    py -3.14 test_spec11_page_check.py      # exits 1 on any failure
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import spec11_page_check as C

FAILS = []
A = "a" * 64
B = "b" * 64


def check(label, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", label, ("  (%s)" % (detail,)) if detail != "" else ""))
    if not cond:
        FAILS.append(label)


def main():
    v = lambda page, mine=A, other=B: C.judge(page, mine, other)["verdict"]
    check("page equals image, differs from other: AS SPECIFIED", v({"elf_sha256": A}) == "AS SPECIFIED")
    check("page is the other build's: NOT", v({"elf_sha256": B}).startswith("NOT AS SPECIFIED")
          and "OTHER build" in v({"elf_sha256": B}))
    check("page absent: NOT", v({}).startswith("NOT AS SPECIFIED: no elf_sha256"))
    check("page upper-case hex: NOT", v({"elf_sha256": A.upper()}).startswith("NOT AS SPECIFIED: elf_sha256 is not"))
    check("page truncated: NOT", v({"elf_sha256": A[:63]}).startswith("NOT AS SPECIFIED: elf_sha256 is not"))
    check("page a third value: NOT", v({"elf_sha256": "c" * 64}).startswith("NOT AS SPECIFIED: page"))
    check("two builds the same: VOID even if the page matches", v({"elf_sha256": A}, A, A).startswith("VOID"))

    body = json.dumps({"elf_sha256": A, "mode": 0, "uptime_ms": 5}).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/gen_inhibit"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Length", str(len(body) if ok else 0))
            self.end_headers()
            if ok:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page = C.read_page("127.0.0.1", srv.server_address[1])
    check("read_page reads /gen_inhibit", page.get("elf_sha256") == A, page)
    srv.shutdown()

    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
