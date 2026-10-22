import os, subprocess, sys
src, comps, out = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8").read()
M = [
 ("branch rule names the wrong cause", '"branch": ("head", "on " + PROD_BRANCH)', '"branch": ("uncommitted",)', ""),
 ("tree made detached again", '"checkout", "-q", "-B", PROD_BRANCH, full]', '"checkout", "-q", "--detach", full]', ""),
 ("branch cases dropped", 'Edit("HEAD detached at the same commit"', 'None and Edit("HEAD detached at the same commit"', "--self-test"),
 ("other-branch case undone by detaching", 'lambda: git(["checkout", "-q", "-b", OTHER_BRANCH], tree)', 'lambda: git(["checkout", "-q", "--detach"], tree)', ""),
]
killed = 0
for name, a, b, flag in M:
    if text.count(a) != 1:
        print("BAD MUTANT %s" % name); continue
    p = os.path.join(os.path.dirname(src), "_mut_test_prod_build_refusals.py")
    open(p, "w", encoding="utf-8").write(text.replace(a, b))
    try:
        r = subprocess.run([sys.executable, p, "--commit", "c65c45e", "--components", comps] + ([flag] if flag else []),
                           capture_output=True, text=True, cwd=os.path.dirname(src))
    finally:
        os.remove(p)
    k = r.returncode != 0
    killed += k
    last = [l for l in r.stdout.splitlines() if "FAIL" in l][:2]
    print("%s  %s  rc %d  %s" % ("killed  " if k else "SURVIVED", name, r.returncode, last))
print("%d of %d killed" % (killed, len(M)))
