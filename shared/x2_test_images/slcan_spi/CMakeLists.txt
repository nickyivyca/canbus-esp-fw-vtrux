# slcan_spi: SLCAN over USB to the X2's CAN2 (the MCP2515), with jamming on
# CAN1. NOT FOR VEHICLE (x2_test_images SPEC T14). An ESP-IDF v5.4.2 project
# (SPEC T6); main/ is its own component, the jammer's pulse engine
# ../jammer/components/jam_pulse is taken unchanged (SPEC T13), and the board
# is the dependency vtrux/x2 at a shared/main tag (shared/boards SPEC B1, B2),
# whose sdkconfig.board values the committed sdkconfig carries.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../jammer/components/jam_pulse)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# The build variant, a CMake option (shared/tools R20, R31; vtrux_build.json),
# as the jammer's (../jammer/CMakeLists.txt): jam_pulse's two test-only builds,
# each showing one of its stops at the deadline alone, never pinnable:
#   none    the image, both stops
#   a_only  test only: stop (b) moved to deadline + 2 s
#   b_only  test only: no deadline timer
# The project name is the image's name, so it says NOT FOR VEHICLE (SPEC T14):
# it is the .bin's name and the app description's project_name, which the
# identity answer (V, N) and the banner print, and from which ../tools/ judge
# the variant. The names must match x2_image.json.
set(SLCAN_SPI_TEST_STOP "none" CACHE STRING "test-only stop variant: none, a_only or b_only")
if(SLCAN_SPI_TEST_STOP STREQUAL "none")
    set(SLCAN_SPI_PROJECT_NAME slcan_spi_NOT_FOR_VEHICLE)
elseif(SLCAN_SPI_TEST_STOP STREQUAL "a_only")
    set(SLCAN_SPI_PROJECT_NAME slcan_spi_a_NOT_FOR_VEHICLE)
    idf_build_set_property(COMPILE_DEFINITIONS "JAM_TEST_STOP_A_ONLY" APPEND)
elseif(SLCAN_SPI_TEST_STOP STREQUAL "b_only")
    set(SLCAN_SPI_PROJECT_NAME slcan_spi_b_NOT_FOR_VEHICLE)
    idf_build_set_property(COMPILE_DEFINITIONS "JAM_TEST_STOP_B_ONLY" APPEND)
else()
    message(FATAL_ERROR "SLCAN_SPI_TEST_STOP is '${SLCAN_SPI_TEST_STOP}': not none, a_only or b_only")
endif()
# ESP-IDF cuts project_name at 31 characters without an error.
string(LENGTH "${SLCAN_SPI_PROJECT_NAME}" SLCAN_SPI_PROJECT_NAME_LEN)
if(SLCAN_SPI_PROJECT_NAME_LEN GREATER 31)
    message(FATAL_ERROR "project name ${SLCAN_SPI_PROJECT_NAME} is over 31 characters")
endif()
project(${SLCAN_SPI_PROJECT_NAME})
