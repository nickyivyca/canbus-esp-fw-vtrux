"""The suite definition schema: what a project hands the chain runner.

A project describes its whole bench suite as one `Suite`: the images it
flashes, the groups of checks, which groups hold the bench lock, and the
known FAILs. The runner (engine.py) executes it; nothing here does I/O.

    Suite
      images:      {name: Image}           what can be flashed, and how
      groups:      [Group]                 run in order within a lane
        checks:    [Check]                 one command each, judged
      known_fails: [KnownFail]             FAILs that are expected, with a ref

Lanes. Groups that hold the bench lock run one after another in the
"bench" lane. Every other group runs in a lane for its host
("logic:<host>"), in parallel with the bench lane and with each other.

Ids. A check's full id is "<group>/<check>"; a judge may return several
results, each "<group>/<check>/<sub>". Ids are made of [A-Za-z0-9_.-] so
that they are safe in paths, globs and records.
"""

import dataclasses
import re
import sys
from typing import Any, Callable, Dict, List, Optional


class Verdict:
    PASS = "PASS"
    FAIL = "FAIL"
    KNOWN_FAIL = "KNOWN-FAIL"
    VOID = "VOID"
    ERROR = "ERROR"
    NOT_RUN = "NOT-RUN"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT-APPLICABLE"

    ALL = (PASS, FAIL, KNOWN_FAIL, VOID, ERROR, NOT_RUN, INCONCLUSIVE,
           NOT_APPLICABLE)
    # What a judge may return. KNOWN-FAIL is assigned by the runner from
    # the registry, never by a judge, and NOT-RUN only by the runner.
    JUDGE = (PASS, FAIL, VOID, ERROR, INCONCLUSIVE, NOT_APPLICABLE)
    # A carried (resumed) result must have been one of these. VOID, ERROR
    # and NOT-RUN are run again.
    TERMINAL = (PASS, FAIL, KNOWN_FAIL, INCONCLUSIVE, NOT_APPLICABLE)


ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# On Windows, CreateProcess searches System32 before PATH, so a bare "bash"
# runs WSL's bash.exe, not Git Bash, though shutil.which finds Git Bash
# (gen-inhibit tester, 2026-10-18: exit 127, "/bin/bash: C:/...: No such
# file or directory"). validate() refuses a bare shell in a local check
# there; give the full path, e.g. from settings ("{bash}").
WINDOWS = sys.platform == "win32"
BARE_SHELLS = ("bash", "bash.exe", "sh", "sh.exe")
# standing_refused: a suite that still names Suite.standing_image(s) is
# refused (user, 2026-11-11: images are loaded at the start of a run and
# never restored at its end; SPEC.md section 6 and its Ruled entries).
# idle_safe_named: an image marked idle_safe=False needs an idle-safe image
# for its board in Suite.idle_safe_images, an image of that board that is
# itself idle-safe (user, 2026-11-11, the same Ruled entries).
RULES = {"bare_shell": True, "standing_refused": True, "idle_safe_named": True}


@dataclasses.dataclass
class Figure:
    """A named measured value a judge reports, compared between runs."""
    value: Any
    unit: str = ""


@dataclasses.dataclass
class Tol:
    """How far a figure may move between two runs and still be the same.
    Both None means exact. A figure passes if within either bound."""
    abs: Optional[float] = None
    rel: Optional[float] = None


@dataclasses.dataclass
class Result:
    """One judged outcome. `sub` names a sub-result; empty for the check
    itself."""
    verdict: str
    sub: str = ""
    figures: Dict[str, Figure] = dataclasses.field(default_factory=dict)
    detail: str = ""


@dataclasses.dataclass
class CheckRun:
    """What a judge sees after its command ran."""
    check: "Check"
    full_id: str
    rc: Optional[int]
    timed_out: bool
    log_path: str           # stdout + stderr of the command
    outdir: str             # the check's own output folder (local)
    duration_s: float
    ctx: Dict[str, Any]

    def log_text(self):
        with open(self.log_path, encoding="utf-8", errors="replace") as f:
            return f.read()


def exit_code_judge(run: CheckRun):
    """The default judge: exit code 0 is PASS, anything else FAIL."""
    if run.rc is None:
        return [Result(Verdict.ERROR, detail="no exit code")]
    if run.rc == 0:
        return [Result(Verdict.PASS, detail="rc=0")]
    return [Result(Verdict.FAIL, detail="rc=%d" % run.rc)]


@dataclasses.dataclass
class Check:
    """One command, judged.

    argv     the command; each item is formatted with the run context
             ({run_dir}, {outdir}, {check_id}, {group}, {work} on a remote
             host, {jobs}, and every string in the settings).
    python   prefix argv with the host's python from the settings.
    cls      "light", "build" (CPU-heavy: throttled while a wall-clock
             test runs on its host) or "wallclock" (timing-sensitive: its
             presence throttles builds on its host).
    outputs  files (relative to the check's outdir) the check writes; the
             suite's redaction hook sees these and nothing else.
    gate     if this check does not PASS, the rest of its group is NOT-RUN.
    skip     a reason this check is listed but not run (needs hands, needs
             the truck, blocked); it is recorded NOT-RUN with the reason, so
             the index still lists it.
    judge    callable(CheckRun) -> Result or [Result]; returning nothing
             is an ERROR, never a PASS.
    tolerances  per figure name, for compare.
    base_env the environment a local check starts from, in place of the
             runner's own (None: the runner's own, as before); the host's
             env and the check's env are laid over it. The acceptance runner
             gives it, so a check never sees the operator's shell
             (ACCEPT-SPEC A7).
    """
    id: str
    argv: List[str]
    python: bool = False
    cwd: Optional[str] = None
    env: Dict[str, str] = dataclasses.field(default_factory=dict)
    timeout_s: float = 3600.0
    cls: str = "light"
    outputs: List[str] = dataclasses.field(default_factory=list)
    gate: bool = False
    judge: Callable[[CheckRun], Any] = exit_code_judge
    tolerances: Dict[str, Tol] = dataclasses.field(default_factory=dict)
    skip: Optional[str] = None
    note: str = ""
    base_env: Optional[Dict[str, str]] = None


@dataclasses.dataclass
class Image:
    """A firmware image the bench can carry.

    flasher  name of a flasher ("esptool" is built in; a suite may add
             its own in Suite.flashers).
    params   flasher-specific (files, offsets, port...), formatted with
             the run context.
    expect   what the suite's identify hook must read back for this image.
    board    which board carries it; a bench may hold several boards, each
             on one image at a time (e.g. "x2", "wican"). A state that is
             not a flashed image (an X2 held in ROM) can be an Image too,
             with its own flasher and expect.
    idle_safe  False marks an image not safe to leave idle on the bench,
             such as one that floods CAN when nothing ACKs it or one that
             boots armed (user, 2026-11-11). A run that would release at its
             end with a board on it loads that board's idle-safe image
             (Suite.idle_safe_images) first. Default True.
    """
    name: str
    flasher: str
    params: Dict[str, Any] = dataclasses.field(default_factory=dict)
    expect: str = ""
    board: str = "board"
    note: str = ""
    idle_safe: bool = True


@dataclasses.dataclass
class Group:
    name: str
    checks: List[Check]
    host: str = "local"
    needs_lock: bool = False
    image: Any = None        # an image name, or a list of them (one per board)
    # Wait for these groups (any lane) to finish first; with after_must_pass,
    # run only if every one of their results is PASS, KNOWN-FAIL or
    # NOT-APPLICABLE (e.g. the bench flashes images the build group made).
    after: List[str] = dataclasses.field(default_factory=list)
    after_must_pass: bool = True
    cleanup: Optional[List[str]] = None  # argv run after each check
    ship: Optional[Dict[str, str]] = None  # remote: {name: local dir}
    note: str = ""

    @property
    def lane(self):
        return "bench" if self.needs_lock else "logic:%s" % self.host

    @property
    def image_list(self):
        if self.image is None:
            return []
        return [self.image] if isinstance(self.image, str) else list(self.image)


@dataclasses.dataclass
class KnownFail:
    """A FAIL that is expected, with the reason it is expected. A matching
    FAIL becomes KNOWN-FAIL; a matching PASS is flagged as a change."""
    id: str
    ref: str


@dataclasses.dataclass
class Suite:
    name: str
    role: str                     # bench-holder holder string
    groups: List[Group]
    images: Dict[str, Image] = dataclasses.field(default_factory=dict)
    # {board: image}: the image a board on an image marked idle_safe=False
    # is loaded with before the end release (user, 2026-11-11). Required for
    # every board that has a marked image; itself idle-safe.
    idle_safe_images: Dict[str, str] = dataclasses.field(default_factory=dict)
    # Retired 2026-11-11 (user: images are loaded at the start of a run,
    # never restored at its end). Kept only so that a suite still naming
    # either is refused by validate() with that reason; never read.
    standing_image: Any = None
    standing_images: Any = None
    known_fails: List[KnownFail] = dataclasses.field(default_factory=list)
    flashers: Dict[str, Callable] = dataclasses.field(default_factory=dict)
    # identify(image_name_expected, ctx, logdir) -> str or None
    identify: Optional[Callable] = None
    # Board hooks (user 2026-10-20: a run killed without cleanup must not
    # leave a board armed). Each is called once per board the bench lane
    # uses, as hook(board, image, ctx, logdir), and returns a dict with
    # "ok" (bool) and "detail" (str), plus anything the hook found
    # ("found"), all of which is recorded. A hook that raises has failed.
    #   on_start  before the start pass identifies the board; image is the
    #             first image the run needs on it. A failure stops the bench:
    #             no group that needs the lock runs, and nothing is flashed.
    #   on_end    after the last case and the end pass (each board
    #             identified, and an idle-unsafe image replaced by its
    #             board's idle-safe one), on every run however it ended,
    #             unless the lock is no longer ours; image is the one the
    #             run believes the board is on, or None if it does not know.
    #             A failure is a "RELEASE NOT CONFIRMED" line.
    on_start: Optional[Callable] = None
    on_end: Optional[Callable] = None
    # redact(list_of_paths) -> None; sees only declared outputs
    redact: Optional[Callable] = None
    # code_info(ctx) -> dict recorded with the run
    code_info: Optional[Callable] = None
    # product_files(root) -> repo-relative paths of the product's files in
    # the tree at root (user 2026-10-20: "the same code" is the product
    # digest over them; each project gives its own split).
    product_files: Optional[Callable] = None
    on_flash_failure: str = "stop-bench"   # or "skip-image-groups"
    # the name of a suite whose newest run on the same code must have
    # matched its reference before this suite may start (the bench suite
    # requires the emulated one)
    requires: Optional[str] = None


class SuiteError(Exception):
    pass


def validate(suite: Suite, settings: Dict[str, Any], builtin_flashers=()):
    """Refuse a suite that cannot run as written, before anything runs."""
    errs = []
    if not suite.role:
        errs.append("suite.role is empty")
    if suite.on_flash_failure not in ("stop-bench", "skip-image-groups"):
        errs.append("on_flash_failure %r" % suite.on_flash_failure)
    flashers = set(builtin_flashers) | set(suite.flashers)
    for name, im in suite.images.items():
        if name != im.name:
            errs.append("image key %r != name %r" % (name, im.name))
        if im.flasher not in flashers:
            errs.append("image %s: unknown flasher %r" % (name, im.flasher))
    if RULES["standing_refused"]:
        for key in ("standing_image", "standing_images"):
            if getattr(suite, key, None) is not None:
                errs.append("%s is retired (user, 2026-11-11): the runner "
                            "restores no board at a run's end; mark an image "
                            "not safe to leave idle with Image.idle_safe=False "
                            "and name its board's idle-safe image in "
                            "Suite.idle_safe_images" % key)
    if RULES["idle_safe_named"]:
        idle = suite.idle_safe_images or {}
        for board, name in sorted(idle.items()):
            im = suite.images.get(name)
            if im is None:
                errs.append("idle-safe image %r for board %s is not an image"
                            % (name, board))
            elif im.board != board:
                errs.append("idle-safe image %s is for board %s, not %s"
                            % (name, im.board, board))
            elif not im.idle_safe:
                errs.append("idle-safe image %s for board %s is itself marked "
                            "not safe to leave idle" % (name, board))
        for name, im in sorted(suite.images.items()):
            if not im.idle_safe and im.board not in idle:
                errs.append("image %s is marked not safe to leave idle, and "
                            "board %s has no idle-safe image "
                            "(Suite.idle_safe_images)" % (name, im.board))
    hosts = set((settings.get("hosts") or {}).keys()) | {"local"}
    seen_groups = set()
    seen = set()
    for g in suite.groups:
        if not ID_RE.match(g.name):
            errs.append("group name %r" % g.name)
        if g.name in seen_groups:
            errs.append("duplicate group %r" % g.name)
        seen_groups.add(g.name)
        if g.host not in hosts:
            errs.append("group %s: host %r not in settings" % (g.name, g.host))
        if g.image is not None:
            boards = []
            for name in g.image_list:
                if name not in suite.images:
                    errs.append("group %s: image %r unknown" % (g.name, name))
                else:
                    boards.append(suite.images[name].board)
            if len(boards) != len(set(boards)):
                errs.append("group %s: two images for one board" % g.name)
            if not g.needs_lock:
                errs.append("group %s carries an image but does not hold "
                            "the lock" % g.name)
            if suite.identify is None:
                errs.append("group %s carries an image but the suite has "
                            "no identify hook" % g.name)
        if g.needs_lock and g.host != "local":
            errs.append("group %s holds the lock on a remote host" % g.name)
        if not g.checks:
            errs.append("group %s has no checks" % g.name)
        for c in g.checks:
            if not ID_RE.match(c.id):
                errs.append("%s: id %r" % (g.name, c.id))
            fid = "%s/%s" % (g.name, c.id)
            if fid in seen:
                errs.append("duplicate id %s" % fid)
            seen.add(fid)
            if isinstance(c, Check):
                if c.cls not in ("light", "build", "wallclock"):
                    errs.append("%s: cls %r" % (fid, c.cls))
                if not c.argv:
                    errs.append("%s: empty argv" % fid)
                elif (RULES["bare_shell"] and WINDOWS and g.host == "local"
                      and str(c.argv[0]).lower() in BARE_SHELLS):
                    errs.append("%s: argv[0] %r on Windows: CreateProcess "
                                "searches System32 before PATH and runs WSL's "
                                "bash, not Git Bash; give the full path"
                                % (fid, c.argv[0]))
                for o in c.outputs:
                    if any(ch in o for ch in "*?[") or o.startswith(("/", "..")):
                        errs.append("%s: output %r must be a plain relative "
                                    "path" % (fid, o))
            else:
                errs.append("%s: not a Check" % fid)
    names = [g.name for g in suite.groups]
    for g in suite.groups:
        for d in g.after:
            if d not in names:
                errs.append("group %s: after %r is not a group" % (g.name, d))
            elif d == g.name:
                errs.append("group %s waits for itself" % g.name)
    # A cycle (through `after` or through lane order) would deadlock.
    preds = {g.name: set(d for d in g.after if d in names) for g in suite.groups}
    last = {}
    for g in suite.groups:
        if g.lane in last:
            preds[g.name].add(last[g.lane])
        last[g.lane] = g.name
    state = {}

    def visit(n, path):
        if state.get(n) == 1:
            errs.append("groups wait in a cycle: %s" % " -> ".join(path + [n]))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for p in sorted(preds.get(n, ())):
            visit(p, path + [n])
        state[n] = 2
    for n in names:
        visit(n, [])
    for kf in suite.known_fails:
        parts = kf.id.split("/")
        if len(parts) < 2 or not all(ID_RE.match(p) for p in parts):
            errs.append("known fail id %r" % kf.id)
        if not kf.ref:
            errs.append("known fail %s has no ref" % kf.id)
    if errs:
        raise SuiteError("suite %s is not runnable:\n  %s"
                         % (suite.name, "\n  ".join(errs)))
