# Sourced by baseline_b_hand.sh and baseline_b_otagroup.sh: where an image of commit $C is.
#
# Port step 0c (2026-10-25) took the commit out of project_name, and the image file is named
# after it: wican-fw_obd_<C>_man[_rt|...].bin before, wican-fw_obd_man[_rt|...].bin from 0c
# on. The scripts named the first form, so they found nothing for a commit after 0c. A
# folder keeps old images too, so exactly one of the two forms must be there.
#
#   img_in <folder> [<suffix>]   prints the one wican-fw_obd_<C>_man<suffix>.bin or
#                                wican-fw_obd_man<suffix>.bin in <folder>; with no suffix,
#                                any suffix (the variant's own: _rt, _flt, ...). With none or
#                                more than one it prints why on stderr, prints
#                                <folder>/NO-SINGLE-IMAGE and returns 1.
img_in() {
  local d=$1 sfx=${2-*} f n=0 out=
  if [ $# -ge 2 ]; then
    set -- "$d/wican-fw_obd_${C}_man$sfx.bin" "$d/wican-fw_obd_man$sfx.bin"
  else
    set -- "$d"/wican-fw_obd_${C}_man*.bin "$d"/wican-fw_obd_man*.bin
  fi
  for f in "$@"; do [ -f "$f" ] && { out=$f; n=$((n+1)); }; done
  if [ "$n" != 1 ]; then
    echo "img_in: $n images in $d named wican-fw_obd_${C}_man$sfx.bin or wican-fw_obd_man$sfx.bin (one wanted)" >&2
    echo "$d/NO-SINGLE-IMAGE"
    return 1
  fi
  echo "$out"
}
