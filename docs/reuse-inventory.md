# Reuse Inventory: school-lens

Mandated by CLAUDE.md Section 0. Catalogues the machinery that already exists in
sibling projects, so the school engine is the only substantially new code.
Reviewed before any feature code is written.

Recon date: 2026-05-29. Effort: max. Re-verify before a fresh ingest.

## Decisions taken (2026-05-29)

- Scope: Washington County (Beaverton SD, Hillsboro SD) + Multnomah County
  (Portland-area districts), plus charter and private schools in those areas.
  Schema is national-ready (NCES IDs); data load is local-first.
- Integration seam: editable-install house-hunter as a library
  (`pip install -e ../house-hunter`); open our own `school_lens.duckdb`; ATTACH
  `house_hunter.duckdb` read-only for the parcel geocoding substrate. Mirrors
  irrigation-recon's proven choice. Decouples DBs, reuses live data, does not
  refactor a sibling.
- Private schools are first-class and included agnostically, with explicit lower
  confidence where data is thin (CLAUDE.md Section 3.2).
- Enrichment (unstructured) is phased after the structured spine.

## Git and filesystem topology

- `personal-projects/` is its own git repo (`personal-projects/.git`).
  house-hunter lives there. school-lens is created as a sibling package under the
  same repo, so the two stay cohesive and `pip install -e ../house-hunter`
  resolves by relative path.
- school-lens keeps its own DuckDB; it does not write into house-hunter's DB.

## Reusable machinery from house-hunter

| Path | What it does | Reuse mode | Reason |
|:---|:---|:---|:---|
| `src/house_hunter/envelope/ingest/_arcgis.py` (`query_arcgis_layer`) | Generic paginated, self-tiling ArcGIS REST puller; returns WGS84 GeoDataFrame; recursive 2x2 subdivision on oversize/500 errors | import-in-place | Exactly what attendance-boundary and district-boundary ingest needs. PPS/Multnomah, Metro RLIS, and (once discovered) Beaverton/Hillsboro endpoints all flow through it. |
| `src/house_hunter/envelope/geom/normalize.py` | `to_target` (->2913), `to_display` (->4326), `bbox_around(lon,lat,half_ft)` | import-in-place | CRS discipline is identical: math in 2913, display in 4326. Reuse verbatim. |
| `src/house_hunter/db.py` (`get_connection`) | DuckDB connection + spatial extension load | fork (thin) | Fork a ~20-line helper that opens `school_lens.duckdb` and ATTACHes `house_hunter.duckdb` read-only. Pattern reused. |
| `house_hunter.duckdb` -> `parcels_base` | 655k parcels (Washington + Multnomah), each with `siteaddr` + `geometry` (EPSG:2913) | import-in-place (data, read-only) | The local address-geocoding substrate. Resolve an address to a point against this before reaching for an external geocoder. |
| `src/house_hunter/api/app.py` | FastAPI skeleton; the 2913->Leaflet idiom `ST_AsGeoJSON(ST_FlipCoordinates(ST_Transform(geom,'EPSG:2913','EPSG:4326')))`; bbox-filtered GeoJSON endpoints; `_build_filters()` shared-helper convention | fork (pattern) | Mount a `school_lens` router with the same conventions. The flip+transform idiom is the canonical 2913->Leaflet seam; copy it exactly. |
| `src/house_hunter/ui/frontend/dist/index.html` | Single-page Leaflet 1.9.4 + Leaflet-Draw, zero build step, `preferCanvas:true`, AbortController debouncing, floating detail card, layer toggles, theme tokens | fork (copy) | The map shell. Fork a copy rather than refactor the working dashboard. Keep the same idioms so the projects feel like one system. |
| `src/house_hunter/config.py` | Pydantic settings, `DATA_DIR` conventions, the Washington+Multnomah bbox | reference + reuse bbox | Model school-lens config on it; reuse the bbox values (same two-county footprint). |
| toolchain: `.venv`, ruff (line 100, py311), pytest, setuptools/pip | Lint/format/test/build config | match | One toolchain per language. Mirror ruff + pytest config and the `pyproject` shape. |

## What is genuinely NEW code (this repo only)

- Canonical school identity + `source_crosswalk` (entity resolution).
- The catchment-as-join-key spatial model: point-in-polygon assignment and
  area-weighted tract attribution of geography-keyed outcomes.
- The metric registry with the school_effect vs context split (CLAUDE.md 3.1).
- The comparison engine: transparent scorecard, meets-or-beats, confidence and
  public/private asymmetry handling (CLAUDE.md 3.2, 6).
- Source-specific ingest/parse for NCES, CRDC, ODE, Opportunity Atlas, ACS.

## Gaps (data no sibling produces)

1. Attendance-boundary geometry. house-hunter has parcels and regulatory
   exclusions but no school boundaries. New ingest, via the reused ArcGIS puller.
2. Beaverton/Hillsboro machine-readable boundary endpoints (see
   docs/data-sources.md, UNVERIFIED). Discovery step required.
3. Driving distance/time. No sibling has routing. Target OSRM; haversine
   straight-line is the labeled-approximate fallback until routing is wired.

## Forks already decided by Dane (2026-05-29)

- Scope includes Multnomah County. DECIDED.
- New package + shared library seam (not a module inside house-hunter, not a
  fork). DECIDED.
- District GIS first for boundaries. DECIDED.
- Enrichment phased after the structured spine. DECIDED (default accepted).
