# jammer: the X2's CAN1 error-frame jammer. TEST EQUIPMENT, NOT FOR VEHICLE
# (x2_test_images SPEC T8). An ESP-IDF v5.4.2 project (SPEC T6): main/ and
# components/jam_pulse/ are its own components, and the board is the
# dependency vtrux/x2 at a shared/main tag (shared/boards SPEC B1, B2), whose
# sdkconfig.board values the committed sdkconfig carries.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# The build variant, a CMake option (shared/tools R20, R31; vtrux_build.json):
#   none    the jammer, both stops
#   a_only  test only, never pinnable: layer (b) moved to deadline + 2 s
#   b_only  test only, never pinnable: no deadline timer
# Each variant has its own project name, which is the image's name, its
# .bin's name and its app descriptor's project_name; it says NOT FOR VEHICLE
# (SPEC T8), and the tools in ../tools/ judge the variant from it (gen-inhibit
# reviewer, 2026-11-12). The names must match x2_image.json.
set(JAMMER_TEST_STOP "none" CACHE STRING "test-only stop variant: none, a_only or b_only")
if(JAMMER_TEST_STOP STREQUAL "none")
    set(JAMMER_PROJECT_NAME vtrux_jammer_x2_NOT_FOR_VEHICLE)
elseif(JAMMER_TEST_STOP STREQUAL "a_only")
    set(JAMMER_PROJECT_NAME jammer_stop_a_NOT_FOR_VEHICLE)
    idf_build_set_property(COMPILE_DEFINITIONS "JAM_TEST_STOP_A_ONLY" APPEND)
elseif(JAMMER_TEST_STOP STREQUAL "b_only")
    set(JAMMER_PROJECT_NAME jammer_stop_b_NOT_FOR_VEHICLE)
    idf_build_set_property(COMPILE_DEFINITIONS "JAM_TEST_STOP_B_ONLY" APPEND)
else()
    message(FATAL_ERROR "JAMMER_TEST_STOP is '${JAMMER_TEST_STOP}': not none, a_only or b_only")
endif()
# ESP-IDF cuts the app descriptor's project_name to 31 characters without an
# error (esp_app_format/CMakeLists.txt), which would make two variants read
# alike.
string(LENGTH "${JAMMER_PROJECT_NAME}" JAMMER_PROJECT_NAME_LEN)
if(JAMMER_PROJECT_NAME_LEN GREATER 31)
    message(FATAL_ERROR "project name ${JAMMER_PROJECT_NAME} is over 31 characters")
endif()
project(${JAMMER_PROJECT_NAME})
