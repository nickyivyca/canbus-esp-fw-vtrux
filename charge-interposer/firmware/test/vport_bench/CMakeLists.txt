# vport_bench: the X2's vehicle port (ESP32-S3 TWAI) on its own, as a
# standalone ESP-IDF test image for spec 9's "what the board can put on the
# vehicle segment by itself". Never goes on the truck. See README.md.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(vport_bench)
