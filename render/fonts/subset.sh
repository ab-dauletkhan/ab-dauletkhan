#!/bin/sh
set -eu
src=${1:?usage: subset.sh <dir with Bangers-Regular.ttf and DelaGothicOne-Regular.ttf>}
here=$(cd "$(dirname "$0")" && pwd)
jp=$(cd "$here/.." && python3 -c 'import render; print("".join(render.JP.values()))')
subset="uvx --from fonttools --with brotli pyftsubset"
$subset "$src/Bangers-Regular.ttf" --unicodes="U+0020-007E,U+00B7,U+2019,U+2212" \
  --flavor=woff2 --no-hinting --desubroutinize --output-file="$here/bangers.woff2"
$subset "$src/DelaGothicOne-Regular.ttf" --text="${jp}0123456789" \
  --flavor=woff2 --no-hinting --desubroutinize --output-file="$here/dela.woff2"
ls -l "$here"/*.woff2
