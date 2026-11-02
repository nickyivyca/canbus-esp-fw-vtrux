"""Stands in for ssh in the acceptance runner's remote-tree tests:

    loop_ssh.py [-o OPTION]... HOST LINE

runs LINE with bash on this machine (Git Bash on Windows: LOOP_SSH_BASH, or
its usual place), standard input passed through, and exits with its code.
So a "remote host" is this machine, its workroot a scratch folder named the
way bash names it (/c/Users/... on Windows), and what the runner does there
-- mkdir, tar, cat into a file, git clone, rm -- really happens. HOST is
ignored.
"""

import os
import subprocess
import sys

args = sys.argv[1:]
while len(args) > 2 and args[0] == "-o":
    args = args[2:]
_host, line = args[0], args[1]
bash = os.environ.get("LOOP_SSH_BASH") or (
    r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else "bash")
sys.exit(subprocess.run([bash, "-c", line]).returncode)
