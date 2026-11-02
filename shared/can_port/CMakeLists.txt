# vtrux/can_port: the CAN port interface both projects use (SPEC.md C1): a
# backend per port index (can_port.c), the stock WiCAN code's CAN calls on
# port 0 (can_stock.c, C3 to C5), and the restart stop (can_port_restart.c,
# C7). It names no backend: a project registers one, e.g. vtrux/twai_port, at
# start-up. can.h and can_port.h are public; both include driver/twai.h for
# twai_message_t and the timing type.
idf_component_register(SRCS "can_port.c" "can_stock.c" "can_port_restart.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver freertos esp_common
                       PRIV_REQUIRES esp_timer log)

# C7, THE RESTART STOP, needs two things the rest of can_port does not.
#
# THE BOARD: it asks the board each port's kind (shared/boards B5). As
# vtrux/twai_port and vtrux/wican do, it builds against the one component of
# the build that has include/board_pins.h. None, or more than one, stops the
# configure.
#
# THE TWAI DRIVER, IF THE BUILD HAS IT: a TWAI port's stop is twai_port's
# (T12). twai_port requires can_port, so it is found here as the board is, by
# its header: the build component with include/twai_port.h. With one, can_port
# links it and defines CAN_PORT_HAVE_TWAI_PORT=1; with none, =0, and a TWAI
# port's stop answers "not supported" (C7). More than one stops the configure.
idf_build_get_property(can_port_build_components BUILD_COMPONENTS)
set(can_port_boards "")
set(can_port_twai "")
foreach(c IN LISTS can_port_build_components)
    idf_component_get_property(c_dir ${c} COMPONENT_DIR)
    if(EXISTS "${c_dir}/include/board_pins.h")
        list(APPEND can_port_boards ${c})
    endif()
    if(EXISTS "${c_dir}/include/twai_port.h")
        list(APPEND can_port_twai ${c})
    endif()
endforeach()
list(LENGTH can_port_boards can_port_board_count)
if(NOT can_port_board_count EQUAL 1)
    message(FATAL_ERROR
        "vtrux/can_port: the build has ${can_port_board_count} board components "
        "(components with include/board_pins.h: '${can_port_boards}'); it needs "
        "exactly one, the project's shared/boards dependency.")
endif()
idf_component_get_property(can_port_board_lib ${can_port_boards} COMPONENT_LIB)
target_link_libraries(${COMPONENT_LIB} PRIVATE ${can_port_board_lib})
list(LENGTH can_port_twai can_port_twai_count)
if(can_port_twai_count GREATER 1)
    message(FATAL_ERROR
        "vtrux/can_port: the build has ${can_port_twai_count} components with "
        "include/twai_port.h ('${can_port_twai}'); it needs at most one.")
endif()
if(can_port_twai_count EQUAL 1)
    idf_component_get_property(can_port_twai_lib ${can_port_twai} COMPONENT_LIB)
    target_link_libraries(${COMPONENT_LIB} PRIVATE ${can_port_twai_lib})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE CAN_PORT_HAVE_TWAI_PORT=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE CAN_PORT_HAVE_TWAI_PORT=0)
endif()
message(STATUS "vtrux/can_port: board ${can_port_boards}, TWAI driver for the "
               "restart stop '${can_port_twai}'")
