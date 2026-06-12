# school-lens

An address-anchored local school data engine. Enter a home address; school-lens
finds the assigned public school(s) by attendance boundary, pulls together the
structured and (later) unstructured data that bears on school quality and adult
outcomes, and compares the assigned public option against nearby charter and
private schools on a transparent scorecard, with the catchment, home, and school
drawn on a map.

See `CLAUDE.md` for the full directive and the two epistemic constraints that
shape the design. See `docs/data-sources.md` for the verified source registry and
`docs/reuse-inventory.md` for what is reused from house-hunter.

## Design in one paragraph

This is an entity-resolution plus spatial-join problem. Every source names schools
differently, so the spine is a canonical school identity (NCES ID) plus a
crosswalk. The richest outcome data is keyed to Census tracts, not schools, so the
attendance-boundary polygon doubles as the spatial join key that attributes
tract-level adult outcomes to a school. Spatial math is done in EPSG:2913;
display is EPSG:4326.

## Quickstart

```
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
pip install -e ../house-hunter        # reused library: ArcGIS puller + CRS helpers
school-lens init-db                   # create the schema; ATTACH house_hunter.duckdb read-only
school-lens ingest-boundaries cop_metro  # City of Portland metro attendance areas
school-lens serve                     # FastAPI + Leaflet at http://127.0.0.1:8011
```

## Status

Scaffold (Phase 1 in progress). Canonical model, comparison engine, metric
registry, and the address -> assigned-school path are implemented; school-keyed
and geography-keyed fact ingest are staged per `CLAUDE.md` Section 9.
