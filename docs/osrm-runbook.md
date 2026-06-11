# OSRM Runbook: local driving distance and time

school-lens uses a self-hosted OSRM (Open Source Routing Machine) for real
network drive distance, drive time, and route geometry. This box has no Docker,
no WSL, and no conda, so we run the native-Windows OSRM build directly. ASCII
only; no em-dashes.

## What is stood up

- Engine: OSRM v26.6.5, native Windows x64. The binaries come from the upstream
  `node_osrm` win32-x64 release asset, which bundles the full CLI suite
  (`osrm-extract/partition/customize/routed.exe`). No Docker, no WSL, no reboot.
- Location: `%LOCALAPPDATA%\school-lens\osrm\` (kept out of the Google
  Drive-locked repo tree, per the Drive-lock convention).
  - `binding_napi_v8\` : the OSRM executables (`osrm-routed.exe` etc.).
  - `profiles\car.lua` + `profiles\lib\*.lua` : the driving profile (api_version 4),
    pinned to the v26.6.5 tag.
  - `data\oregon-latest.osm.pbf` : the OSM extract (Geofabrik, ~240 MB).
  - `data\oregon-latest.osrm*` : the built MLD graph (output of `build`).
- Coverage: all of Oregon (one Geofabrik unit; covers Washington + Multnomah
  plus routing margin). Algorithm: MLD (multi-level Dijkstra).

## How school-lens consumes it

- `school_lens.config.Settings.osrm_url` defaults to `http://127.0.0.1:5000`
  (override with env `SL_OSRM_URL`; set it to empty to force straight-line).
- `spatial/distance.py` returns `{miles, minutes, method, geometry}`:
  - `method="osrm"` : real route from OSRM (minutes + geometry populated).
  - `method="osrm_unreachable"` : OSRM configured but the request failed; it fell
    back to straight-line. This is the signal that osrm-routed is not running.
  - `method="haversine_approx"` : no OSRM configured at all.
- `/api/assign` requests route geometry so the UI can draw the actual route.

## Operate (Git Bash)

    scripts/osrm-control.sh status     # what is present on disk
    scripts/osrm-control.sh download   # (re)fetch the Oregon OSM extract
    scripts/osrm-control.sh build      # extract + partition + customize (MLD)
    scripts/osrm-control.sh serve      # run osrm-routed on :5000 (foreground)
    scripts/osrm-control.sh test       # one sample route via the running server

Typical first run: `download` -> `build` -> `serve`. The graph only needs
rebuilding when you refresh the PBF (boundaries and routing change slowly; a
quarterly refresh is plenty).

Then, in another shell:

    set SL_OSRM_URL=http://127.0.0.1:5000   # already the default
    school-lens serve

## Keeping it running

`osrm-routed` is a long-lived process. Options, least to most automatic:
1. Run `scripts/osrm-control.sh serve` in a terminal when you need routing.
2. A logon Scheduled Task (same pattern as RestartDetective) that runs
   `osrm-routed.exe --algorithm mld --port 5000 <graph>.osrm`. Not created
   automatically; ask and it can be added.

## Troubleshooting

- Every distance shows `method=osrm_unreachable`: osrm-routed is not running, or
  is on another port. Start it (`serve`) or fix `SL_OSRM_URL`.
- `Error opening .osrm`: the graph is not built. Run `build`.
- Profile / api_version mismatch on extract: the `profiles/` files must match the
  binary version (both pinned to v26.6.5 here). Re-fetch profiles at the binary's
  tag if you upgrade the binaries.
- Disk: the full pipeline (PBF + MLD graph) is roughly 1 GB. This machine runs
  near full; clear space before a rebuild if `build` fails on write.
