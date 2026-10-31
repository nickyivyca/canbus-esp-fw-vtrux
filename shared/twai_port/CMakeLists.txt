# vtrux/twai_port: the driver for the chip's built-in CAN controller (TWAI),
# behind vtrux/can_port (SPEC.md T1). A project takes it with vtrux/can_port
# and its board at the same shared/main tag, and lists all three itself, as
# it lists vtrux/debug_logs beside vtrux/wican.
#
# T8, THE REGISTER FILE: one per chip, naming the ESP-IDF version it was
# checked against. Only the C3's is measured; any other chip builds the
# unsupported file, whose buffer state and abort report themselves
# unsupported.
if(IDF_TARGET STREQUAL "esp32c3")
    set(twai_port_ll "twai_port_ll_esp32c3.c")
else()
    set(twai_port_ll "twai_port_ll_unsupported.c")
endif()

# hal: twai_ll.h for the C3's register file, and the TWAI types the public
# header's driver/twai.h brings in.
idf_component_register(SRCS "twai_port.c" "${twai_port_ll}"
                       INCLUDE_DIRS "include"
                       REQUIRES can_port driver hal freertos esp_common
                       PRIV_REQUIRES log)

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
