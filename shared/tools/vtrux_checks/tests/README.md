# shared/tools/vtrux_checks/tests

**Owner:** tester (gen-inhibit).

The build checks' own tests, one file per check, each runnable alone:

| File | What |
|---|---|
| `test_scope.py` | R17 (`../scope.py`): each rule failing on a planted input and passing on a faithful one, and its readers on small files in scratch folders. Also the tests' scratch folders (below), and their own test, `ScratchFolders` |
| `test_options.py` | R18 and R26-R28 (`../options.py`): each rule failing on a planted input and passing on a faithful one (R26 and R28's fixed options, R27's tick, the Arduino core's two signals, `-D` log levels in compile commands), the profile's reader and its refusals of a profile contradicting R26-R28, and the command line on a small build tree |
| `test_board.py` | R21 (`../board.py`, `../parttable.py`): the partition-table reader on tables built from ESP-IDF's published format and on broken ones, the board data's reader, the slot, the tool prefix from a fake ESP-IDF (and the real one, given `IDF_PATH`), that no check's code names a chip's tool, and the command line on a small build folder |
| `test_boarddata.py` | the project's board (R21, R29, R30; `shared/boards/SPEC.md` B2-B4, `../boarddata.py`): found from `dependencies.lock` and read from `managed_components/`, each rule failing on a planted input and passing on a faithful one (a real board's files too, given `VTRUX_TEST_BOARD`) |
| `test_pins.py` | R30 (`../pins.py`): each rule failing on a planted input and passing on a faithful one, on a small build tree (`build/` and `build/bootloader/`) with ninja's own records |
| `test_switches.py` | R20 (`../switches.py`): the scans on the ways a switch could hide, the table's reader, the ELF reader (against `nm` too, given `VTRUX_TEST_ELF` and `VTRUX_TEST_NM`), each refusal rule and the project rows file, the judge, and the build reader and command line on a small configured build |
| `boardkit.py` | a project's board for the tests: a `dependencies.lock` naming a `shared/boards/*` component, and that component's folder in `managed_components/` |

## Scratch folders

Every folder a test writes is made by `test_scope.scratch(test)`: a new folder in the worktree's
gitignored `scratch/vtrux-checks-tests/`, removed among the test's cleanups, so pass or fail and
after its `tearDown` (`AGENTS.md` "Files and folders"). The remover is `test_scope.remove(path)`,
which calls `verification/independent.py`'s `remove_tree`: it clears read-only flags, which Git for
Windows sets on `.git/objects` (`test_scope.py`'s readers make git repositories), retries, and uses
`\\?\` paths on Windows. A folder it cannot remove fails the test, naming the folder, and the remover
prints why. Outside a git worktree there is no scratch folder, and the system temp folder is not one:
`scratch()` refuses.

`ScratchFolders` checks this with small tests of its own: a folder holding a read-only file and a
path over 260 characters is removed and the test passes; a folder that cannot be removed fails the
test and is named; the folder is in `scratch/vtrux-checks-tests/`; and a start outside any worktree
is refused. Each of its fakes, which breaks one of those rules (a plain `shutil.rmtree`, one with
`ignore_errors`, one that ignores the remover's answer, no removal, and a temp-folder fallback), fails
at least one check. The other test files neither make nor remove a folder any other way, which it
checks too.
