# slcan_spi: SLCAN over USB to the X2's CAN2 (the MCP2515). NOT FOR VEHICLE
# (x2_test_images SPEC T14). An ESP-IDF v5.4.2 project (SPEC T6); main/ is its
# one own component, and the board is the dependency vtrux/x2 at a shared/main
# tag (shared/boards SPEC B1, B2), whose sdkconfig.board values the committed
# sdkconfig carries.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# The project name is the image's name, so it says NOT FOR VEHICLE (SPEC T14):
# it is the .bin's name and the app description's project_name, which the
# identity answer (V, N) and the banner print.
project(slcan_spi_NOT_FOR_VEHICLE)
