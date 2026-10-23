"""Stands in for ssh in test_run_core: `fake_ssh.py HOST LINE`.

Appends {"host", "line"} as one JSON line to $FAKE_SSH_LOG. A line running
the command `run-forever` sleeps until killed; any other line (mkdir, the
remote kill) exits 0 at once.
"""

import json
import os
import sys
import time

with open(os.environ["FAKE_SSH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"host": sys.argv[1], "line": sys.argv[2]}) + "\n")
if "run-forever" in sys.argv[2]:
    time.sleep(120)
