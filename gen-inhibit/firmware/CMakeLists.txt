# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# THE BOARD IS THE DEPENDENCY (shared/boards SPEC B1 and B2; port step 1a).
# Stock WiCAN picked its hardware here with HARDWARE_VER and passed the model
# numbers and the hardware name down as -D definitions. This project names
# its board by its one shared/boards/* dependency, vtrux/wican_v3 in
# main/idf_component.yml, and nowhere else: the pins and per-model values come
# from the board's headers, the hardware name from board_hw_name(), and the
# model numbers project_hardware_rev holds are in main/hw_config.h. The
# partition table is the board's, through sdkconfig.

# THE VERSION IS ESP-IDF'S OWN (spec 12.3 item 3, port step 0c; shared/tools
# R4). Nothing here sets PROJECT_VER, there is no version.txt and project()
# below gets no VERSION, so ESP-IDF runs `git describe --always --tags --dirty`
# at configure and embeds it as esp_app_desc.version. That is the only place
# the commit enters the image; the project name below carries none. Both are
# labels (shared/tools R6): the image is named by its image hash, which 0x7F3
# carries (spec 10).

set(PROJECT_PREF "wican-fw")

# The image name's model part, "obd" as stock names its OBD builds. It is
# fixed here, not taken from the board: project() below runs before the
# Component Manager fetches the board, and the name is this product's label,
# which the build checks and records read (shared/tools R6). main.c reads
# "usb" or "hv210" in it to set project_hardware_rev; "obd" gives WICAN_V300.
set(HW_PREF "obd")

# STOCK FEATURES, OFF IN THIS PRODUCT (spec 3.2 and 12.3 item 3, user
# 2026-10-16). main/CMakeLists.txt is the shared code and defaults each switch
# ON; gen-inhibit's build turns all four OFF here, so their code is not
# compiled into this product. CACHE, so `idf.py -D<switch>=ON` still overrides
# it. That also means a build directory once configured ON stays ON until it
# is deleted. A release build's image carrying a stock feature's code is
# refused by the shared build tooling's build-switch check (shared/tools SPEC
# R20, rows in vtrux_checks.json), which judges the image, not the flag.
set(WICAN_FEATURE_MQTT     OFF CACHE BOOL "MQTT CAN logging and transmit (mqtt.c); off in gen-inhibit, spec 3.2")
set(WICAN_FEATURE_ELM327   OFF CACHE BOOL "ELM327 command path (elm327.c); off in gen-inhibit, spec 3.2")
set(WICAN_FEATURE_AUTOPID  OFF CACHE BOOL "AutoPID (autopid.c), needs ELM327 and MQTT; off in gen-inhibit, spec 3.2")
set(WICAN_FEATURE_REALDASH OFF CACHE BOOL "RealDash boot branch (main.c); off in gen-inhibit, spec 3.2")

# Form the project binary name
#
# THE ARM STATE IS IN THE NAME (user, 2026-10-04). Before this, a shipping build and
# an auto-arm build at the same commit produced the SAME filename --
# `wican-fw_obd_<sha>.bin` in `build/` and in `build-autoarm/` -- and E3 passed with
# every row identical on both, because no check could tell them apart. Two images
# that must never be swapped, separated only by a directory name.
#
# BOTH STATES ARE SPELLED OUT, not just the auto-arm one. If only auto-arm carried a
# suffix then a bare name would mean either "manual" or "built before this change",
# which is the same marker-by-absence that lets a silent failure read as a pass. An
# unsuffixed image is now distinguishable from both.
#
# The name also lands in `esp_app_desc.project_name` inside the image, so it is a
# STATIC discriminator readable without flashing -- which the runtime marker
# (0x7F1 diag_flags bit3) is not. `build_output.py`'s project-name row reads it.
#
# WHY "_auto"/"_man" AND NOT "_autoarm"/"_manual". Until port step 0c the name
# also carried the commit, and with a dirty one ("<sha>-dirty", 13) the
# 31-character budget below left exactly 5 for this suffix. The name no longer
# carries a commit (spec 12.3 item 3, shared/tools R4), but the short forms stay:
# the build checks and the records already read them.
if(GEN_INHIBIT_AUTOARM)
    set(GI_ARM_SUFFIX "_auto")
else()
    set(GI_ARM_SUFFIX "_man")
endif()

# THE FAULT INJECTION IS IN THE NAME TOO, and for a sharper reason than the arm
# state. `git_version` over HTTP is the commit, so a rollback-rehearsal build and
# a good build AT THE SAME COMMIT report the same one. Their image hashes differ,
# and 0x7F3 carries the hash's first four bytes (spec 10), but a hash says which
# variant it is only once it is looked up in a record. The only static
# discriminator that survives into the image and reads as a variant is
# esp_app_desc.project_name; the only runtime one is the `FAULT INJECT:` console
# line, which requires a serial capture to be running at the right moment to be
# seen at all.
#
# THIS ONE IS MARKED BY ABSENCE AND THE ARM STATE IS NOT, which contradicts the
# block above unless the difference is stated. Both arm states are legitimate
# things to deploy, so a bare name is ambiguous between two live options; a
# fault-injected image must never ship, so absence means "an ordinary build" and
# is ambiguous between nothing.
if(OTA_HEALTH_FAULT_INJECT)
    set(GI_FAULT_SUFFIX "_flt")
else()
    set(GI_FAULT_SUFFIX "")
endif()
# THE RESTART TEST BUILD IS IN THE NAME TOO (spec 12.4, "no error frames at a
# restart": its crash trigger and record reader are never in a prod build), for
# the fault build's reason: at the same commit it and the normal manual image
# would otherwise share a filename, a project_name and a git_version, and the
# test build's own symbols and strings are the only other thing that tells them
# apart. Marked by absence, like "_flt", and for the same reason.
if(GI_TEST_RESTART_QUIESCE)
    set(GI_TEST_SUFFIX "_rt")
else()
    set(GI_TEST_SUFFIX "")
endif()
set(PRJ_BIN "${PROJECT_PREF}_${HW_PREF}${GI_ARM_SUFFIX}${GI_FAULT_SUFFIX}${GI_TEST_SUFFIX}")

#
# esp_app_desc.project_name is a 32-byte field -- 31 characters plus the NUL -- and
# `components/esp_app_format/esp_app_desc.c:70` enforces it with a _Static_assert, so
# an over-long name is a COMPILE error rather than a truncation. That is the right
# failure, but it arrives as an assert about a struct field, which is not where
# anyone would look for it. So check it here, where the name is built, and say what
# to do.
#
# The name holds no commit (spec 12.3 item 3), so nothing in a build's tree
# changes its length: the longest today is "wican-fw_obd" (12) with "_auto",
# "_flt" and "_rt", 24. It is kept for the suffix that would take it over 31,
# which should produce this message instead of a static assert about a struct
# field.
#
string(LENGTH "${PRJ_BIN}" PRJ_BIN_LEN)
if(PRJ_BIN_LEN GREATER 31)
    message(FATAL_ERROR
        "project name '${PRJ_BIN}' is ${PRJ_BIN_LEN} characters; "
        "esp_app_desc.project_name holds 31 plus a NUL.\n"
        "  It is PROJECT_PREF, HW_PREF and this build configuration's "
        "GI_ARM_SUFFIX, GI_FAULT_SUFFIX and GI_TEST_SUFFIX (CMakeLists.txt): "
        "shorten a suffix.\n"
        "  Left unchecked this surfaces as a _Static_assert in "
        "components/esp_app_format/esp_app_desc.c, which does not point here.")
endif()

project("${PRJ_BIN}")
