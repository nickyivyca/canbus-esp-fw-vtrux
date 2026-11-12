# Sourced by the bench shell scripts: the host's Python command and path form, and the
# capture tool's defaults. Gen-inhibit tester, 2026-11-12 (the reviewer's Linux fix item).
#
# The scripts were written for Git Bash on NICKY-XPS: `py -3.14`, and paths handed to
# Windows Python in mixed form (`cygpath -m`). The bench host is madhouse-debian from
# 2026-11-11, Linux only for new bench tooling (user, 2026-11-11), where neither exists.
#
#   $PY            the Python command, unquoted where used: `$PY -B tool.py ...`
#                  Linux: python3.13 (local-paths.md). Git Bash: py -3.14.
#   hpath <path>   prints <path> in the form the host's Python takes: as it is on
#                  Linux, `cygpath -m` on Git Bash.
#   GI_HOST_DUMPCAP, GI_HOST_TSHARK, GI_HOST_IFACE
#                  the capture defaults the scripts use when GI_DUMPCAP, GI_TSHARK or
#                  GI_CAPTURE_IFACE is unset. Git Bash: NICKY-XPS's Wireshark and Wi-Fi.
#                  Linux: dumpcap and tshark on PATH, and no interface: madhouse-debian has no
#                  Wi-Fi adapter yet, so a capture step needs GI_CAPTURE_IFACE set
#                  (need_iface refuses it otherwise).
#   need_iface     exits 2, saying why, when no capture interface is known.
#
# Any other host refuses (exit 2).
case "$(uname -s)" in
  Linux)
    PY="python3.13"
    hpath() { printf '%s\n' "$1"; }
    GI_HOST_DUMPCAP="dumpcap"
    GI_HOST_TSHARK="tshark"
    GI_HOST_IFACE=""
    ;;
  MINGW*|MSYS*|CYGWIN*)
    PY="py -3.14"
    hpath() { cygpath -m "$1"; }
    GI_HOST_DUMPCAP="/c/Program Files/Wireshark/dumpcap.exe"
    GI_HOST_TSHARK="/c/Program Files/Wireshark/tshark.exe"
    GI_HOST_IFACE='\Device\NPF_{2D126A09-0299-425A-88DC-51DA04E0C70C}'   # Wi-Fi on NICKY-XPS (dumpcap -D)
    ;;
  *)
    echo "bench_host.sh: unsupported host $(uname -s)" >&2
    exit 2
    ;;
esac
need_iface() {
  [ -n "${IFACE:-}" ] || { echo "no capture interface: set GI_CAPTURE_IFACE (dumpcap -D lists them)" >&2; exit 2; }
}
