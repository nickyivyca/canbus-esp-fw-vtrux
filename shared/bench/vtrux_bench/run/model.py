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
    """
    name: str
    flasher: str
    params: Dict[str, Any] = dataclasses.field(default_factory=dict)
    expect: str = ""
    board: str = "board"
    note: str = ""


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
    standing_image: Optional[str] = None   # single-board shorthand
    standing_images: Dict[str, str] = dataclasses.field(default_factory=dict)
    known_fails: List[KnownFail] = dataclasses.field(default_factory=list)
    flashers: Dict[str, Callable] = dataclasses.field(default_factory=dict)
    # identify(image_name_expected, ctx, logdir) -> str or None
    identify: Optional[Callable] = None
    # redact(list_of_paths) -> None; sees only declared outputs
    redact: Optional[Callable] = None
    # code_info(ctx) -> dict recorded with the run
    code_info: Optional[Callable] = None
    on_flash_failure: str = "stop-bench"   # or "skip-image-groups"
    # the name of a suite whose newest run on the same code must have
    # matched its reference before this suite may start (the bench suite
    # requires the emulated one)
    requires: Optional[str] = None

    def standings(self):
        """{board: standing image name}."""
        out = dict(self.standing_images)
        if self.standing_image and self.standing_image in self.images:
            out.setdefault(self.images[self.standing_image].board,
                           self.standing_image)
        return out

    def standing_for(self, board):
        return self.standings().get(board)


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
    if suite.standing_image and suite.standing_image not in suite.images:
        errs.append("standing_image %r is not an image" % suite.standing_image)
    for board, name in suite.standing_images.items():
        if name not in suite.images:
            errs.append("standing image %r is not an image" % name)
        elif suite.images[name].board != board:
            errs.append("standing image %s is for board %s, not %s" % (
                name, suite.images[name].board, board))
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
