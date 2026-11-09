# main: the interposer's own code (port plan layers 3 and 4), built as one of
# six variants (spec 8.2), chosen by the top CMakeLists.txt's INTP_VARIANT.
#
# Each variant has exactly one *_main.cpp: two would each define setup() and
# loop(), and the link fails. The bridge's three images, bridge, witness and
# truck, are the same sources, differing only by one switch each.
#
# THE VEHICLE PORT is the shared vtrux/twai_port behind vtrux/can_port (spec
# 2.4, port step 2b), both dependencies of main (idf_component.yml):
# vehicle_port.cpp sets it up and registers it, and port_view.cpp is how the
# bridge reaches a port through can_port by its index. THE CHARGER PORT is
# the interposer's MCP2515 driver, port_mcp2515.cpp, registered with can_port
# by charger_port.cpp.
set(vehicle vehicle_port.cpp port_view.cpp)
set(ports ${vehicle} port_mcp2515.cpp charger_port.cpp)
if(INTP_VARIANT STREQUAL "bridge" OR INTP_VARIANT STREQUAL "witness"
   OR INTP_VARIANT STREQUAL "truck")
    set(srcs main.cpp machine.cpp ${ports})
elseif(INTP_VARIANT STREQUAL "selftest")
    set(srcs selftest_main.cpp ${ports})
elseif(INTP_VARIANT STREQUAL "diag")
    set(srcs diag_main.cpp ${ports})
elseif(INTP_VARIANT STREQUAL "slcan")
    # The generator-inhibit bench's SLCAN transmitter, on CAN1 only: it never
    # touches the charger segment, so no MCP2515.
    set(srcs slcan_main.cpp ${vehicle})
endif()

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ".")

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra)

# The same fixed lines as on the Arduino component (top CMakeLists.txt), so
# the Arduino headers main includes see them too.
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ARDUINO_USB_MODE=1
    ARDUINO_USB_CDC_ON_BOOT=1)

# The two switches a variant sets (shared/tools R20; the tester's table):
# witness: the completion-order witness, a bench image that keeps serial;
# truck: the flight image, with this firmware's serial output compiled out
# (spec 8.3).
if(INTP_VARIANT STREQUAL "witness")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE INTP_ORDER_WITNESS=1)
elseif(INTP_VARIANT STREQUAL "truck")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE INTP_NO_SERIAL=1)
endif()
