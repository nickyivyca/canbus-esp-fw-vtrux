"""vtrux_pins: pins on outside packages (SPEC R35) and outside images (R36).

- cantools: canbench, from the can-tools repo, written from git blobs into
  <worktree>/scratch/can-tools/<commit>/ and loaded by file;
- canre: canre, from the public reverse-engineering repo, installed by pip
  into <worktree>/scratch/canre/<commit>/, or found at CANRE_DIR;
- images: the files of firmware images a bench flashes but does not build.

The command line is in cli.py; shared/tools/README.md has the detail.
Stdlib only, like vtrux_build, whose image library the image check uses.
"""
