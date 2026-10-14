# cmake/

CMake modules shared by the ESP-IDF projects in this repo. Each one is
self-contained and documents itself in its header.

| File | What it does |
|------|--------------|
| `git_tree_name.cmake` | **Every build is named from the tree it compiles** (gen-inhibit spec 12.3 item 3, 2026-10-13). `git_tree_name(<var>)`, called before `project()`, returns `git describe --tags --always --dirty` and makes every build -- `idf.py build` or `ninja` directly -- run it again before anything is compiled, re-running CMake if the answer changed. If git cannot answer, the build fails. It also sets `PROJECT_VER` to the same string, so the name enters the build once, as `esp_app_desc.version`, which firmware reads at runtime; a name over the field's 31 characters fails configuring. Used by the top-level `CMakeLists.txt`. |
