#!/usr/bin/env bash
# How the host_bridge tests are built on the shared vehicle port (port step
# 2b; tester, 2026-11-08), in one place, for run.sh and for the harness
# checks that build test_l2_bridge from a copy (l2_mutations.sh,
# rx_order_prefix_control.sh, tx_fail_banking_mutation.sh,
# switch_discard_witness.py): a second hand-kept source or include list is
# how a check ends up building something other than what run.sh builds.
#
# Sourced:
#   . l2_build.sh
#   hb_shared || exit 2     the shared folders, or a refusal (rc 2). First
#                           ../../shared_pin.py idf-match: the
#                           shared/twai_port and shared/can_port pins are
#                           one tag, the versions main/idf_component.yml
#                           takes, or nothing is built. Then:
#                           HB_SH  the TWAI driver fake, shared/twai_port/test:
#                                  TWAI_FAKE_DIR if set, else the pinned
#                                  checkout's (shared_pin.py where
#                                  shared/twai_port)
#                           HB_TP  twai_port: HB_SH/.., or the pinned one
#                           HB_CP  can_port: HB_SH/../../can_port (the same
#                                  checkout, so the same tag), or the pinned
#                                  one (where shared/can_port)
#                           HB_INC_SHARED  their include folders, after a
#                                  build's own (-I <host_bridge> -I <main>)
#                           HB_C_SRCS  name:path of each C source
#   hb_c_objs OBJ HB FW     each C source compiled once, as C (gcc -std=c11),
#                           to OBJ/<name>.o, with HB and FW first on the
#                           include path; prints "<name> (C, <path>) DID NOT
#                           BUILD" for one that does not and returns 1
#   HB_FW_L2                main/'s sources of the whole bridge, as
#                           test_l2_bridge links them (main/CMakeLists.txt's
#                           bridge variant)
#
# Run:
#   bash l2_build.sh OUT HB FW [g++ args]
#     builds test_l2_bridge from HB (a host_bridge folder) and FW (a main/
#     folder) into OUT, with the C objects in OUT.obj/, and -DINTP_NO_SERIAL=1
#     as run.sh builds it (the truck image's code path), plus any further
#     g++ arguments. Exit 0 built, 1 did not build, 2 refused (no shared
#     folders).

HB_BUILD_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HB_FW_L2=(main.cpp port_mcp2515.cpp vehicle_port.cpp port_view.cpp
          charger_port.cpp machine.cpp)

hb_shared() {
  local pin="$HB_BUILD_HERE/../../shared_pin.py"
  # The pins are the firmware's versions (shared_pin.py idf-match), or
  # nothing is built.
  python3 "$pin" idf-match >&2 || {
    echo "refused: the shared/twai_port and shared/can_port pins are not the" \
         "versions firmware/main/idf_component.yml takes" >&2
    return 2
  }
  HB_SH="${TWAI_FAKE_DIR:-}"
  if [ -z "$HB_SH" ]; then
    HB_TP="$(python3 "$pin" where shared/twai_port)" &&
      HB_CP="$(python3 "$pin" where shared/can_port)" || {
      echo "no shared twai_port and can_port (set TWAI_FAKE_DIR to" \
           "<checkout>/shared/twai_port/test, or create the pinned checkout)" >&2
      return 2
    }
    HB_SH="$HB_TP/test"
  else
    HB_TP="$HB_SH/.."
    HB_CP="$HB_SH/../../can_port"
  fi
  local f
  for f in "$HB_SH/twai_fake.c" "$HB_SH/cpp/twai_fake.cpp" "$HB_TP/twai_port.c" \
           "$HB_TP/twai_port_ll_unsupported.c" "$HB_TP/include/twai_port.h" \
           "$HB_CP/can_port.c" "$HB_CP/include/can_port.h"; do
    if [ ! -f "$f" ]; then
      echo "$f is missing: TWAI_FAKE_DIR names the shared fake's folder," \
           "<checkout>/shared/twai_port/test, of a checkout that also holds" \
           "shared/can_port" >&2
      return 2
    fi
  done
  HB_INC_SHARED=(-I "$HB_SH/include" -I "$HB_SH/host" -I "$HB_SH/cpp"
                 -I "$HB_TP/include" -I "$HB_CP/include")
  # The S3's register file is the unsupported one (twai_port T8).
  HB_C_SRCS=("twai_fake:$HB_SH/twai_fake.c" "twai_port:$HB_TP/twai_port.c"
             "twai_port_ll:$HB_TP/twai_port_ll_unsupported.c"
             "can_port:$HB_CP/can_port.c")
  return 0
}

hb_c_objs() {
  local obj="$1" hb="$2" fw="$3" c rc=0
  mkdir -p "$obj" || return 1
  for c in "${HB_C_SRCS[@]}"; do
    rm -f "$obj/${c%%:*}.o"
    if ! gcc -std=c11 -O2 -Wall -Wextra -I "$hb" -I "$fw" "${HB_INC_SHARED[@]}" \
         -c -o "$obj/${c%%:*}.o" "${c#*:}"; then
      echo "${c%%:*} (C, ${c#*:}) DID NOT BUILD"
      rc=1
    fi
  done
  return $rc
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -u
  if [ "$#" -lt 3 ]; then
    echo "usage: l2_build.sh OUT HB FW [g++ args]" >&2
    exit 2
  fi
  out="$1" hb="$2" fw="$3"
  shift 3
  hb_shared || exit 2
  rm -f "$out"
  hb_c_objs "$out.obj" "$hb" "$fw" || exit 1
  srcs=("$hb/test_l2_bridge.cpp" "$HB_SH/cpp/twai_fake.cpp")
  for f in "$hb"/*.cpp; do
    case "$(basename "$f")" in test_*) continue ;; esac
    srcs+=("$f")
  done
  for f in "${HB_FW_L2[@]}"; do srcs+=("$fw/$f"); done
  g++ -std=c++11 -O2 -Wall -Wextra -I "$hb" -I "$fw" "${HB_INC_SHARED[@]}" \
      -DINTP_NO_SERIAL=1 "$@" -o "$out" "${srcs[@]}" "$out.obj"/*.o || exit 1
  [ -x "$out" ] || exit 1
  exit 0
fi
