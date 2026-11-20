# Sourced by the bench shell scripts: the host's Python command and path form, and the
# desk scripts' tshark. Gen-inhibit tester, 2026-11-12 (the reviewer's Linux fix item).
#
# The scripts were written for Git Bash on NICKY-XPS: `py -3.14`, and paths handed to
# Windows Python in mixed form (`cygpath -m`). The bench host is madhouse-debian from
# 2026-11-11, Linux only for new bench tooling (user, 2026-11-11), where neither exists.
#
#   $PY            the Python command, unquoted where used: `$PY -B tool.py ...`
#                  Linux: python3.13 (local-paths.md). Git Bash: py -3.14.
#   hpath <path>   prints <path> in the form the host's Python takes: as it is on
#                  Linux, `cygpath -m` on Git Bash.
#   GI_HOST_TSHARK the tshark a desk script uses when GI_TSHARK is unset (ota_pcap_stall.sh,
#                  reading old captures). Git Bash: NICKY-XPS's Wireshark. Linux: on PATH.
#                  No script captures since topic (E) (2026-11-19: B11raw and B21 judged
#                  without a Wi-Fi capture), so GI_HOST_DUMPCAP, GI_HOST_IFACE and need_iface
#                  are gone.
#
# Any other host refuses (exit 2).
case "$(uname -s)" in
  Linux)
    PY="python3.13"
    hpath() { printf '%s\n' "$1"; }
    GI_HOST_TSHARK="tshark"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    PY="py -3.14"
    hpath() { cygpath -m "$1"; }
    GI_HOST_TSHARK="/c/Program Files/Wireshark/tshark.exe"
    ;;
  *)
    echo "bench_host.sh: unsupported host $(uname -s)" >&2
    exit 2
    ;;
esac
