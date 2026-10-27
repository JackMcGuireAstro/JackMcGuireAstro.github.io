#!/bin/bash
# Reference data for the CTAS light-curve science (Track 3c part 1), fetched at pinned
# commits and checked byte for byte, into one folder the CTAS cloud workflow caches:
#
#   <dir>/sfd/SFD_dust_4096_{ngp,sgp}.fits   Schlegel, Finkbeiner & Davis (1998) dust map,
#                                            from github.com/kbarbary/sfddata
#   <dir>/sncosmo/                           sncosmo's data folder with the ZTF bandpasses
#                                            and the SALT3 (f22) model, from
#                                            github.com/sncosmo/sncosmo.github.io
#
# Usage: ctas_reference_data.sh <dir> [<github-env-file>]
# Prints CTAS_SFD_DIR and CTAS_SNCOSMO_DATA_DIR (and appends them to the env file when
# given). A missing or altered file is refused: the light-curve runs then say "not
# computed" (the exporter never substitutes a default).
set -euo pipefail
DIR=${1:?usage: ctas_reference_data.sh <dir> [<github-env-file>]}
ENV_FILE=${2:-}
SFD_COMMIT=7a5fe7fadf086561ba4748756e59a4c51d0ec632
SNCOSMO_COMMIT=a399a399660caf104fb6e874d05ebdab421d45f9
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)

verify() {  # verify <file> <sha256>
  [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]
}

fetch_commit() {  # fetch_commit <repo-url> <commit> <dest> [sparse paths...]
  local url=$1 commit=$2 dest=$3; shift 3
  rm -rf "$dest.tmp" && mkdir -p "$dest.tmp"
  git -C "$dest.tmp" init -q
  git -C "$dest.tmp" remote add origin "$url"
  if [ "$#" -gt 0 ]; then
    git -C "$dest.tmp" sparse-checkout set --no-cone "$@" >/dev/null 2>&1 || git -C "$dest.tmp" sparse-checkout set "$@"
  fi
  git -C "$dest.tmp" fetch -q --depth 1 --filter=blob:none origin "$commit"
  git -C "$dest.tmp" checkout -q FETCH_HEAD
  rm -rf "$dest.tmp/.git"
}

SFD="$DIR/sfd"
SFD_NGP=50b6aaad0b880762d0fd081177802dcc17c39d7044a410dd5649e2dfd0503e97
SFD_SGP=84891a59054adab44a7be54051e4dcf0e66e3f13eee0d845ce3739242f553b83
if ! verify "$SFD/SFD_dust_4096_ngp.fits" "$SFD_NGP" || ! verify "$SFD/SFD_dust_4096_sgp.fits" "$SFD_SGP"; then
  fetch_commit https://github.com/kbarbary/sfddata "$SFD_COMMIT" "$SFD" /SFD_dust_4096_ngp.fits /SFD_dust_4096_sgp.fits
  mkdir -p "$SFD"
  mv "$SFD.tmp/SFD_dust_4096_ngp.fits" "$SFD.tmp/SFD_dust_4096_sgp.fits" "$SFD/"
  rm -rf "$SFD.tmp"
  verify "$SFD/SFD_dust_4096_ngp.fits" "$SFD_NGP" || { echo "SFD north map does not match its checksum" >&2; exit 1; }
  verify "$SFD/SFD_dust_4096_sgp.fits" "$SFD_SGP" || { echo "SFD south map does not match its checksum" >&2; exit 1; }
fi

SNC="$DIR/sncosmo"
SALT3=c7de53438761aeaacb5b28347fb0e9b00b5f5b1036843688e3d5d5edd4f7a37e
ZTF_G=cb233f2aa195709dfd91cdb89b9744b247aaeeba412ec56efa15a7256259213a
ZTF_R=838d332ce90372753a13fbe4975f4ea93de2e1b6f48c0198797fb80283ff02a7
ZTF_I=fd2478c019837518a93ff0b9d2b11dcbc6ff540749763fdf861eea0f10b66e2a
if ! verify "$SNC/models/salt3/salt3-f22.tar.gz" "$SALT3" || ! verify "$SNC/bandpasses/ztf/P48_g.dat" "$ZTF_G" \
   || ! verify "$SNC/bandpasses/ztf/P48_R.dat" "$ZTF_R" || ! verify "$SNC/bandpasses/ztf/P48_I.dat" "$ZTF_I" \
   || [ ! -d "$SNC/models/salt3/salt3-f22" ]; then
  fetch_commit https://github.com/sncosmo/sncosmo.github.io "$SNCOSMO_COMMIT" "$SNC" \
    /data/models/salt3/salt3-f22.tar.gz /data/bandpasses/ztf/P48_g.dat /data/bandpasses/ztf/P48_R.dat /data/bandpasses/ztf/P48_I.dat
  rm -rf "$SNC" && mkdir -p "$SNC/models/salt3" "$SNC/bandpasses/ztf"
  mv "$SNC.tmp/data/models/salt3/salt3-f22.tar.gz" "$SNC/models/salt3/"
  mv "$SNC.tmp/data/bandpasses/ztf/"P48_*.dat "$SNC/bandpasses/ztf/"
  rm -rf "$SNC.tmp"
  verify "$SNC/models/salt3/salt3-f22.tar.gz" "$SALT3" || { echo "SALT3 model does not match its checksum" >&2; exit 1; }
  for pair in "P48_g.dat $ZTF_G" "P48_R.dat $ZTF_R" "P48_I.dat $ZTF_I"; do
    set -- $pair
    verify "$SNC/bandpasses/ztf/$1" "$2" || { echo "ZTF bandpass $1 does not match its checksum" >&2; exit 1; }
  done
  tar -xzf "$SNC/models/salt3/salt3-f22.tar.gz" -C "$SNC/models/salt3" 2>/dev/null
fi

echo "CTAS_SFD_DIR=$SFD"
echo "CTAS_SNCOSMO_DATA_DIR=$SNC"
if [ -n "$ENV_FILE" ]; then
  { echo "CTAS_SFD_DIR=$SFD"; echo "CTAS_SNCOSMO_DATA_DIR=$SNC"; } >> "$ENV_FILE"
fi
