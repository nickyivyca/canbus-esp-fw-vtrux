# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

set(WICAN_V210 1)
set(WICAN_V300 2)
set(WICAN_USB_V100 3)
set(WICAN_PRO 4)

set(WICAN_V210_STR "OBD")
set(WICAN_V300_STR "OBD")
set(WICAN_USB_V100_STR "USB")
set(WICAN_PRO_STR "OBD-PRO")

# Set these without quotes, as they're string literals
set(WICAN_V210_PREF "obd")
set(WICAN_V300_PREF "obd")
set(WICAN_USB_V100_PREF "usb")
set(WICAN_PRO_PREF "obd_pro")

add_definitions(-DWICAN_V210=${WICAN_V210})
add_definitions(-DWICAN_V300=${WICAN_V300})
add_definitions(-DWICAN_USB_V100=${WICAN_USB_V100})
add_definitions(-DWICAN_PRO=${WICAN_PRO})

add_definitions(-DWICAN_V210_STR="${WICAN_V210_STR}")
add_definitions(-DWICAN_V300_STR="${WICAN_V300_STR}")
add_definitions(-DWICAN_USB_V100_STR="${WICAN_USB_V100_STR}")
add_definitions(-DWICAN_PRO_STR="${WICAN_PRO_STR}")

execute_process(
    COMMAND git describe --tags --always --dirty
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    OUTPUT_VARIABLE GIT_SHA
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

set(GIT_SHA1 "${GIT_SHA}")
string(REPLACE "." "" GIT_SHA "${GIT_SHA}")

add_definitions(-DGIT_SHA="${GIT_SHA1}")

# Uncomment and use one of the following as needed
# set(HARDWARE_VER ${WICAN_V210})
set(HARDWARE_VER ${WICAN_V300})
# set(HARDWARE_VER ${WICAN_USB_V100})
# set(HARDWARE_VER ${WICAN_PRO})

add_definitions(-DHARDWARE_VER=${HARDWARE_VER})

set(PROJECT_PREF "wican-fw")

# Set HW_PREF based on HARDWARE_VER
if (HARDWARE_VER EQUAL ${WICAN_PRO})
    set(HW_PREF "${WICAN_PRO_PREF}")
    set(HW_VER "${WICAN_PRO_STR}")
elseif (HARDWARE_VER EQUAL ${WICAN_USB_V100})
    set(HW_PREF "${WICAN_USB_V100_PREF}")
    set(HW_VER "${WICAN_USB_V100_STR}")
elseif (HARDWARE_VER EQUAL ${WICAN_V210})
    set(HW_PREF "${WICAN_V210_PREF}")
    set(HW_VER "${WICAN_V210_STR}")
else()
    set(HW_PREF "${WICAN_V300_PREF}")
    set(HW_VER "${WICAN_V300_STR}")
endif()

add_definitions(-DHARDWARE_VERSION="${HW_VER}")

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
# WHY "_auto"/"_man" AND NOT "_autoarm"/"_manual". The budget below is 31
# characters. "wican-fw_obd_" is 13 and a DIRTY GIT_SHA is 13 ("<sha>-dirty"),
# so a dirty tree leaves exactly 5. "_manual" is 7 and would therefore break
# EVERY dirty build, not merely dirty auto-arm ones -- that is, the ordinary
# edit-and-build loop. The first version of this block used the long forms and
# called the fallout deliberate, having worked the arithmetic for the auto-arm
# case only. "_auto" is 5 and "_man" is 4, so both fit a dirty tree.
if(GEN_INHIBIT_AUTOARM)
    set(GI_ARM_SUFFIX "_auto")
else()
    set(GI_ARM_SUFFIX "_man")
endif()

# THE FAULT INJECTION IS IN THE NAME TOO, and for a sharper reason than the arm
# state. Both `git_version` over HTTP and the 0x7F3 wire hash are derived from
# GIT_SHA, so a rollback-rehearsal build and a good build AT THE SAME COMMIT are
# indistinguishable through every channel the device exposes. The only static
# discriminator that survives into the image is esp_app_desc.project_name; the
# only runtime one is the `FAULT INJECT:` console line, which requires a serial
# capture to be running at the right moment to be seen at all.
#
# THIS ONE IS MARKED BY ABSENCE AND THE ARM STATE IS NOT, which contradicts the
# block above unless the difference is stated. Two reasons. (1) Both arm states
# are legitimate things to deploy, so a bare name is ambiguous between two live
# options; a fault-injected image must never ship, so absence means "an ordinary
# build" and is ambiguous between nothing. (2) The 31-character budget: spelling
# out the good case as "_ok" costs 3 characters on EVERY build, which takes a
# dirty tree to 33 and so breaks the ordinary edit-and-build loop -- exactly the
# arithmetic that chose "_man" over "_manual". A fault build at 28 clean fits;
# at 34 dirty it does not, and the FATAL_ERROR below is the right answer there,
# because a rehearsal image whose provenance is "some uncommitted tree" is
# worthless regardless of its name.
if(OTA_HEALTH_FAULT_INJECT)
    set(GI_FAULT_SUFFIX "_flt")
else()
    set(GI_FAULT_SUFFIX "")
endif()
# THE DIAGNOSTIC BUILD IS IN THE NAME, unconditionally, because this branch
# (`gen-inhibit/diag-reset`) exists only to produce one and must never be
# merged. There is no good-case build to leave unmarked here, so there is no
# marker-by-absence to argue about: every image this tree can produce is a diag
# image.
#
# "_DIAG" AND NOT "_DIAG_NOT_FOR_VEHICLE", which is what was asked for. The
# budget checked below is 31 characters, and it is esp_app_desc.project_name's,
# not a convention: 13 ("wican-fw_obd_") + 7 (clean sha) + 4 ("_man") + 5
# ("_DIAG") = 29, while "_DIAG_NOT_FOR_VEHICLE" is 21 and would make 45 -- a
# _Static_assert, not a truncation, so the full string cannot go in the image
# at all. The full warning therefore lives on the BUILD DIRECTORY
# (`build-DIAG_NOT_FOR_VEHICLE/`), and "_DIAG" is the part that survives into
# the image, where `build_output.py`'s project-name row and anything else
# reading esp_app_desc can see it without flashing. A directory name alone
# would be exactly the "separated only by a directory name" failure the arm
# suffix above was added to end; this is a directory name PLUS a static marker
# inside the image.
#
# Note that "_DIAG" takes a DIRTY tree to 35 and so fails the check below. That
# is the right outcome: a diagnostic image whose provenance is "some
# uncommitted tree" tells the reader nothing when its console output is read
# back days later.
set(GI_DIAG_SUFFIX "_DIAG")

set(PRJ_BIN "${PROJECT_PREF}_${HW_PREF}_${GIT_SHA}${GI_ARM_SUFFIX}${GI_FAULT_SUFFIX}${GI_DIAG_SUFFIX}")

#
# esp_app_desc.project_name is a 32-byte field -- 31 characters plus the NUL -- and
# `components/esp_app_format/esp_app_desc.c:70` enforces it with a _Static_assert, so
# an over-long name is a COMPILE error rather than a truncation. That is the right
# failure, but it arrives as an assert about a struct field, which is not where
# anyone would look for it. So check it here, where the name is built, and say what
# to do.
#
# With "_auto"/"_man" nothing in normal use trips this: 20 + 5 = 25 clean, 26 + 5 = 31
# dirty, which is exactly the limit. It is kept because the margin is zero on a dirty
# tree, so anything that lengthens GIT_SHA takes it over -- and the obvious candidate
# is a TAG. `git describe --tags --always --dirty` returns the short sha only while
# the repo has no tags (checked 2026-10-04: it has none). Tag it `v1.0` and GIT_SHA
# becomes something like `v10-3-g82e4e09`, which overflows at once. This message is
# what that day should produce instead of a static assert about a struct field.
#
string(LENGTH "${PRJ_BIN}" PRJ_BIN_LEN)
if(PRJ_BIN_LEN GREATER 31)
    message(FATAL_ERROR
        "project name '${PRJ_BIN}' is ${PRJ_BIN_LEN} characters; "
        "esp_app_desc.project_name holds 31 plus a NUL.\n"
        "  Commit the working tree (that drops '-dirty', 6 characters), or shorten "
        "GI_ARM_SUFFIX in CMakeLists.txt.\n"
        "  A rollback-rehearsal build adds GI_FAULT_SUFFIX ('_flt', 4 more) and so "
        "needs a CLEAN tree: 13 + 7 + 4 + 4 = 28 fits, 13 + 13 + 4 + 4 = 34 does "
        "not. Commit rather than shortening -- an image built to be flashed over "
        "the live one must have a commit behind it.\n"
        "  Left unchecked this surfaces as a _Static_assert in "
        "components/esp_app_format/esp_app_desc.c, which does not point here.")
endif()

project("${PRJ_BIN}")
