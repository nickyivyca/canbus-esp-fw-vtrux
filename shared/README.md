# shared/

Code shared by the two ESP32 projects in this repo, gen-inhibit and the
charge interposer.

- **Branch.** `shared/main` is the integration branch for shared code.
  Changes arrive from `shared/<component>/<topic>` branches cut from it. The
  reviewer whose project needs a change merges it, after the other project's
  reviewer has read it.
- **How projects use it.** A project takes shared code only as a pinned tag
  cut from `shared/main`. It never merges this branch, and this branch never
  merges a project branch. Tags are dated, `YYYY.MM.DD` (then `.2`, `.3` the
  same day), cut by the reviewer of a project about to pin one, and never
  moved or deleted (`tools/SPEC.md` R25). They are lightweight tags, with no
  message, since the pre-push check scans commits and a tag adds none.
- **No history before the first commit, by design** (user, 2026-10-23). Git
  then refuses a merge between `shared/main` and a project branch, which
  would otherwise apply this branch's changes to the project's tree. The
  first commit copies `shared/` unchanged from `charge-interposer/main` at
  `b9896a03afd0c8245e14a5941021fe6a9e19ae32`. The earlier history of
  `bench/` is on that branch.

| Folder | What |
|---|---|
| `bench/` | The shared bench toolkit (`vtrux_bench`). See `bench/README.md`. |
| `boards/` | The board layer: one ESP-IDF component per board, to `boards/SPEC.md`. See `boards/README.md`. |
| `tools/` | The shared build tooling (`vtrux_build`): the image library, the release build and its record, to `tools/SPEC.md`. See `tools/README.md`. |
