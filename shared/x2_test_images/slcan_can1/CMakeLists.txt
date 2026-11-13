# slcan_can1: a plain SLCAN transmitter on the X2's CAN1, the ESP32-S3's
# TWAI (x2_test_images SPEC T1). An ESP-IDF v5.4.2 project (SPEC T6), moved
# from the charge interposer's slcan variant
# (charge-interposer/firmware/main/slcan_main.cpp) without the Arduino core.
# main/ is its own component; the board, the CAN port interface and the TWAI
# port are the dependencies vtrux/x2, vtrux/can_port and vtrux/twai_port at
# one shared/main tag (shared/boards SPEC B1, B2; twai_port T1), and the
# committed sdkconfig carries the board's sdkconfig.board values.
#
# One variant, dev (vtrux_build.json), with no option. The project name is
# the image's name: the .bin's name and the app description's project_name,
# from which ../tools/ judge the variant; it must match x2_image.json.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(slcan_can1)
