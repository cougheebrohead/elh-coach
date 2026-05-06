#!/usr/bin/env bash
# Regenerate every marketing screenshot from the ELH Coach SPA with mocks.
#
# Usage:
#   ./tools/snap/snap_all.sh
#
# Pipeline:
#   1. Copy live app.html / client.html into a fresh build dir
#   2. Inject mock fetch + brand via mock_inject.py
#   3. Drive each view via WKWebView (snap.swift) and write PNGs to
#      screenshots/. Existing files are overwritten in place.
#
# Env overrides:
#   OUT       -- screenshot output dir (default: <repo>/screenshots)
#   BUILD     -- build/scratch dir     (default: $TMPDIR/elh_snap_build)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT="${OUT:-$REPO/screenshots}"
BUILD="${BUILD:-${TMPDIR:-/tmp}/elh_snap_build}"
SWIFT="${SWIFT:-/usr/bin/swift}"

mkdir -p "$BUILD" "$OUT"

cp "$REPO/app.html"    "$BUILD/coach.html"
cp "$REPO/client.html" "$BUILD/client.html"
SNAP_BUILD_DIR="$BUILD" python3 "$SCRIPT_DIR/mock_inject.py"

run() {
  local html="$1" out="$2" w="$3" h="$4" js="${5:-}"
  echo "==> $out  (${w}x${h})"
  "$SWIFT" "$SCRIPT_DIR/snap.swift" "$BUILD/$html" "$OUT/$out" "$w" "$h" "$js"
}

# Coach Portal (desktop, sidebar visible at >= 920px)
run coach.html  coach_dashboard.png   1280 700  "switchView('dashboard');"
run coach.html  coach_roster.png      1280 920  "switchView('roster');"
run coach.html  coach_programs.png    1280 720  "switchView('programs');"
run coach.html  coach_client_labs.png 1280 1100 \
  "switchView('roster'); setTimeout(() => { openClient('c1'); setTimeout(() => { var b=document.querySelector('#c-tabs button[data-ct=labs]'); if(b){b.click();} }, 1800); }, 1500);"

# Client App (mobile portrait)
run client.html client_today.png      400 950  ""
run client.html client_lab_snap.png   414 900  \
  "openSnapLab(); setTimeout(() => renderLabReview(__LAB_LATEST__), 600);"

echo "==> done. Wrote 6 screenshots to $OUT"
