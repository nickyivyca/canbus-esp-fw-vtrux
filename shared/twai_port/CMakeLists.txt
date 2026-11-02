# vtrux/twai_port: the driver for the chip's built-in CAN controller (TWAI),
# behind vtrux/can_port (SPEC.md T1). A project takes it with vtrux/can_port
# and its board at the same shared/main tag, and lists all three itself, as
# it lists vtrux/debug_logs beside vtrux/wican.
#
# T8 AND T12, THE REGISTER FILE: one per chip, naming the ESP-IDF version it
# was checked against. Only the C3's is measured; any other chip builds the
# unsupported file, whose buffer state, abort and restart stop report
# themselves unsupported.
if(IDF_TARGET STREQUAL "esp32c3")
    set(twai_port_ll "twai_port_ll_esp32c3.c")
else()
    set(twai_port_ll "twai_port_ll_unsupported.c")
endif()

set(twai_port_srcs "twai_port.c" "${twai_port_ll}")
set(twai_port_includes "include")
# The probe's components (esp_http_server, esp_timer and esp_app_format) are
# listed in every build, not only under the switch below. ESP-IDF reads each
# component's requirements in an early pass, a separate `cmake -P` that is
# given no -D cache variables (IDF v5.4.1 tools/cmake/component.cmake,
# __component_get_requirements), so a switch there changes nothing. It is
# only a library edge: the port calls none of them, and the switch still
# decides whether the probe's source is compiled.
set(twai_port_priv_requires log soc esp_rom
                            esp_http_server esp_timer esp_app_format)

# T13, THE TRANSMIT-ABORT PROBE: a measurement, never in a release image
# (shared/tools R20).
#
#   idf.py -B build-txabort -DTWAI_PORT_TXABORT_PROBE=1 build
#
# The source is added to the component only here, rather than compiled always
# and guarded inside with #if: a source that is not in the build cannot be
# linked by accident. It writes the abort command register directly, so a
# release image carrying it is refused by the build-switch check (rows in
# vtrux_checks.json). The project registers it from its URI hook (W8),
# passing its check of whether it owns the bus.
if(TWAI_PORT_TXABORT_PROBE)
    list(APPEND twai_port_srcs "test_app/twai_port_txabort_probe.c")
    list(APPEND twai_port_includes "test_app/include")
endif()

# hal: twai_ll.h for the C3's register file, and the TWAI types the public
# header's driver/twai.h brings in. soc and esp_rom: T12's clock and reset
# bits and its 2 us wait.
idf_component_register(SRCS ${twai_port_srcs}
                       INCLUDE_DIRS ${twai_port_includes}
                       REQUIRES can_port driver hal freertos esp_common
                       PRIV_REQUIRES ${twai_port_priv_requires})

if(TWAI_PORT_TXABORT_PROBE)
    message(STATUS "vtrux/twai_port: TX ABORT probe ENABLED (measurement build) "
                   "-- writes the TWAI abort command register directly")
endif()

# THE BOARD IS THE PROJECT'S (shared/boards B2, B5): the port takes its pins
# and standby line from the board's CAN port the project names, through
# board.h. As vtrux/wican does (its W9), it builds against the one component
# of the build that has include/board_pins.h. None, or more than one, stops
# the configure.
idf_build_get_property(twai_port_build_components BUILD_COMPONENTS)
set(twai_port_boards "")
foreach(c IN LISTS twai_port_build_components)
    idf_component_get_property(c_dir ${c} COMPONENT_DIR)
    if(EXISTS "${c_dir}/include/board_pins.h")
        list(APPEND twai_port_boards ${c})
    endif()
endforeach()
list(LENGTH twai_port_boards twai_port_board_count)
if(NOT twai_port_board_count EQUAL 1)
    message(FATAL_ERROR
        "vtrux/twai_port: the build has ${twai_port_board_count} board components "
        "(components with include/board_pins.h: '${twai_port_boards}'); it needs "
        "exactly one, the project's shared/boards dependency.")
endif()
idf_component_get_property(twai_port_board_lib ${twai_port_boards} COMPONENT_LIB)
target_link_libraries(${COMPONENT_LIB} PRIVATE ${twai_port_board_lib})
message(STATUS "vtrux/twai_port: register file ${twai_port_ll}, board ${twai_port_boards}")
