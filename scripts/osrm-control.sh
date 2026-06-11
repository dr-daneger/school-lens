#!/usr/bin/env bash
# OSRM control for school-lens: native-Windows osrm-routed, no Docker/WSL.
#
# The OSRM binaries (node_osrm win32-x64 build, which bundles osrm-extract/
# partition/customize/routed.exe) and data live under %LOCALAPPDATA% to stay out
# of the Google Drive-locked repo tree. Run from Git Bash.
#
#   scripts/osrm-control.sh download   # fetch the Oregon OSM extract (~240 MB)
#   scripts/osrm-control.sh build      # extract + partition + customize (MLD)
#   scripts/osrm-control.sh serve      # run osrm-routed on :5000 (foreground)
#   scripts/osrm-control.sh test       # one sample route via the running server
#   scripts/osrm-control.sh status     # show what is present on disk
#
# Pipeline reference: https://github.com/Project-OSRM/osrm-backend (MLD path).
set -euo pipefail

OSRM_DIR="${OSRM_DIR:-$LOCALAPPDATA/school-lens/osrm}"
BIN="$OSRM_DIR/binding_napi_v8"
PROFILE="$OSRM_DIR/profiles/car.lua"
PBF="$OSRM_DIR/data/oregon-latest.osm.pbf"
OSRM="$OSRM_DIR/data/oregon-latest.osrm"
PORT="${OSRM_PORT:-5000}"
PBF_URL="https://download.geofabrik.de/north-america/us/oregon-latest.osm.pbf"

case "${1:-help}" in
  download)
    mkdir -p "$OSRM_DIR/data"
    curl -fsSL -o "$PBF" "$PBF_URL"
    echo "downloaded $(ls -la "$PBF")"
    ;;
  build)
    # MLD (multi-level Dijkstra) pipeline: extract with the car profile, then
    # partition + customize. Re-run after refreshing the PBF.
    "$BIN/osrm-extract.exe" -p "$PROFILE" "$PBF"
    "$BIN/osrm-partition.exe" "$OSRM"
    "$BIN/osrm-customize.exe" "$OSRM"
    echo "built $OSRM"
    ;;
  serve)
    # Foreground; Ctrl-C to stop. school-lens reads SL_OSRM_URL (default
    # http://127.0.0.1:5000), so no app change is needed once this is up.
    exec "$BIN/osrm-routed.exe" --algorithm mld --port "$PORT" "$OSRM"
    ;;
  test)
    # Beaverton-area sample: Waterleaf St -> a nearby point.
    curl -s "http://127.0.0.1:$PORT/route/v1/driving/-122.86,45.49;-122.80,45.49?overview=false" \
      | head -c 400; echo
    ;;
  status)
    echo "OSRM_DIR: $OSRM_DIR"
    [ -x "$BIN/osrm-routed.exe" ] && echo "  routed:  present ($("$BIN/osrm-routed.exe" --version 2>&1 | head -1))" || echo "  routed:  MISSING"
    [ -f "$PBF" ] && echo "  pbf:     $(du -h "$PBF" | cut -f1)" || echo "  pbf:     MISSING (run: download)"
    [ -f "$OSRM" ] && echo "  graph:   built" || echo "  graph:   MISSING (run: build)"
    ;;
  *)
    echo "usage: osrm-control.sh {download|build|serve|test|status}"
    ;;
esac
