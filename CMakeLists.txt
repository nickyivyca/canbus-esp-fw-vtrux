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
set(PRJ_BIN "${PROJECT_PREF}_${HW_PREF}_${GIT_SHA}${GI_ARM_SUFFIX}")

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
        "  Left unchecked this surfaces as a _Static_assert in "
        "components/esp_app_format/esp_app_desc.c, which does not point here.")
endif()

project("${PRJ_BIN}")
