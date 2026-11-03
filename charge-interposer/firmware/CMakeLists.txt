# The charge interposer's ESP-IDF project (port step 2a): the project root is
# charge-interposer/firmware/ (AGENTS.md "Project layout"), main/ is its one
# own component, and arduino-esp32 is a Component Manager dependency of main
# (port plan goal 1). The board is the dependency vtrux/x2 (shared/boards SPEC
# B1 and B2), whose sdkconfig.board values the committed sdkconfig carries.
cmake_minimum_required(VERSION 3.16)

# THE VARIANT IS A CMAKE BUILD OPTION (user, 2026-10-25; shared/tools R20).
# One build folder per variant, all from the one committed sdkconfig:
#   idf.py -B build/<variant> -D INTP_VARIANT=<variant> build
# main/CMakeLists.txt picks each variant's sources and switches. CACHE, so a
# build folder keeps the variant it was configured with until it is deleted.
set(INTP_VARIANTS bridge witness truck selftest diag slcan)
set(INTP_VARIANT "bridge" CACHE STRING
    "the image to build: bridge, witness, truck, selftest, diag or slcan (spec 8.2)")
if(NOT INTP_VARIANT IN_LIST INTP_VARIANTS)
    message(FATAL_ERROR "INTP_VARIANT '${INTP_VARIANT}' is not one of: ${INTP_VARIANTS}")
endif()

# COMPILE FLAGS GO IN RESPONSE FILES. arduino-esp32 brings about forty managed
# components, each adding an include folder and a -fdebug-prefix-map to every
# compile, and on Windows the command line passed the 32,767-character limit
# ("ninja: fatal: CreateProcess: The parameter is incorrect", 2026-11-03).
# The compiler reads the same flags from a file, so the image is unchanged.
set(CMAKE_NINJA_FORCE_RESPONSE_FILE ON)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# THE VERSION IS ESP-IDF'S OWN (shared/tools R4). Nothing here sets
# PROJECT_VER, there is no version.txt and project() gets no VERSION, so
# ESP-IDF runs `git describe --always --tags --dirty` at configure and embeds
# it as esp_app_desc.version, the only place the commit enters the image. The
# project name is interposer_<variant> (spec 8.2), fixed per variant, with no
# commit; the longest, interposer_selftest, is 19 of project_name's 31.
project(interposer_${INTP_VARIANT})

# FIXED LINES ON THE ARDUINO COMPONENT, in every variant (user, 2026-10-25:
# the 2a -D review). arduino-esp32 has no Kconfig option for either, and
# unset, Serial is UART0 on GPIO43/44, where nothing is attached on the X2
# (the 2026-09-10 silence). main/CMakeLists.txt sets the same two on main.
# They are not build switches (shared/tools R20, ruling b).
idf_component_get_property(arduino_lib espressif__arduino-esp32 COMPONENT_LIB)
target_compile_definitions(${arduino_lib} PRIVATE
    ARDUINO_USB_MODE=1
    ARDUINO_USB_CDC_ON_BOOT=1)
