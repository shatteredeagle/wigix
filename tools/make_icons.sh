#!/bin/bash
# Regenerate the tray icons (pixel art) with Aseprite. Needs Aseprite (set $ASEPRITE if it is not on PATH).
# Sources land in assets/aseprite/, the PNGs the tray uses in src/wigix/tray_icons/.
set -euo pipefail
cd "$(dirname "$0")/.."
ASE="${ASEPRITE:-$(command -v aseprite || echo "$HOME/.local/share/Steam/steamapps/common/Aseprite/aseprite")}"
OUT=assets/aseprite; PNG=src/wigix/tray_icons
mkdir -p "$OUT" "$PNG"
"$ASE" -b --script-param out="$(pwd)/$OUT" --script tools/icons.lua
for f in "$OUT"/wigix-*-*.aseprite; do
  b=$(basename "$f" .aseprite); size=${b##*-}; name=${b%-*}
  "$ASE" -b "$f" --save-as "$PNG/$b.png"                       # native size
  for k in 2 3 4; do                                           # crisp integer upscales for HiDPI trays
    "$ASE" -b "$f" --scale $k --save-as "$PNG/$name-$((size * k)).png"
  done
done
ls "$PNG" | wc -l
