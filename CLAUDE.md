# CLAUDE.md: school-lens

## Directive

Build `school-lens`: a local school data engine. Given a home address, it
identifies the assigned public school(s) by attendance boundary, pulls together
the structured and unstructured data that bears on school quality and adult
outcomes, and compares the assigned public option against nearby charter and
private alternatives on a transparent, address-anchored scorecard. Reuse the
existing project machinery before writing new code (Section 0). Operate under the
standing auto-approve model: read, build, test, commit, and push without asking
for routine confirmation. Surface only the design forks that need Dane's
judgment, plus anything marked UNVERIFIED.

## 0. Reuse mandate (read before writing any code)

These projects are personal, single-operator, and meant to compose into one
system. The first objective is reuse, not greenfield code. Before implementing a
feature, read the actual source of the sibling projects in the Local Repo tree,
not just their READMEs.

Reuse target is `personal-projects/house-hunter/`. The reuse decisions are
catalogued in `docs/reuse-inventory.md` and summarized here:

- `house_hunter.envelope.ingest._arcgis.query_arcgis_layer`: a generic,
  paginated, self-tiling ArcGIS REST puller returning a WGS84 GeoDataFrame.
  Import in place for all attendance-boundary and district-boundary ingest.
- `house_hunter.envelope.geom.normalize`: `to_target` (-> EPSG:2913),
  `to_display` (-> EPSG:4326), `bbox_around`. Import in place; CRS discipline is
  identical.
- `house_hunter.db.get_connection` pattern: DuckDB + spatial extension. Fork a
  thin connection helper that opens `school_lens.duckdb` and ATTACHes
  `house_hunter.duckdb` read-only.
- `house_hunter.duckdb -> parcels_base`: 655k parcels across Washington and
  Multnomah counties, each with `siteaddr` + `geometry` (EPSG:2913). This is the
  local address-geocoding substrate; reuse it before reaching for an external
  geocoder.
- `house_hunter.api.app`: FastAPI skeleton and the canonical 2913 -> Leaflet
  serialization idiom `ST_AsGeoJSON(ST_FlipCoordinates(ST_Transform(geom,
  'EPSG:2913','EPSG:4326')))`. Fork the pattern, mount a new router.
- `house_hunter/ui/frontend/dist/index.html`: vanilla JS + Leaflet 1.9.4 +
  Leaflet-Draw, zero build step, `preferCanvas`, AbortController debouncing.
  Fork a copy for school-lens rather than refactor a working dashboard.
- Toolchain: setuptools + pip + `.venv`, ruff (line 100, py311), pytest. Match
  it. One toolchain per language.

Integration seam (mirrors irrigation-recon's proven choice): editable-install
house-hunter as a library (`pip install -e ../house-hunter`), open our own
`school_lens.duckdb`, and ATTACH `house_hunter.duckdb` read-only. This decouples
the databases (no school tables in house-hunter's DB) while reusing live data.
Do not refactor house-hunter to fit this project.

Default policy: reuse in place > fork > reinvent. Fork only when a sibling's
version carries an assumption that does not fit; name the assumption.

## 1. Identity and scope

school-lens is an address-anchored school comparison tool. The unit of analysis
is a school; the entry point is a home address; the deliverable is an honest
side-by-side of the assigned public school against nearby charter and private
options.

In scope: canonical school identity and source crosswalk; attendance-boundary
geometry; address -> assigned-school resolution; structured outcome ingest
(academics, discipline and safety, staffing, finance); geography-linked adult
outcomes (joined through the catchment polygon); a transparent multi-metric
scorecard; a map UI that illuminates the catchment, the home, and the school,
with driving distance.

Out of scope for now: national data load (the schema is national-ready, the data
load is local-first), public multi-tenant deployment, real-time data feeds,
predicting an individual child's outcome.

Geography (data load): Washington County (Beaverton SD, Hillsboro SD) and
Multnomah County (Portland-area districts), plus the charter and private schools
located in those areas. The schema keys on NCES IDs so the load can extend to any
county later without a model change.

Address baseline: comparisons are anchored to a user-entered address.

## 2. Cohesion and shared dependencies

Match house-hunter's package manager and pin to compatible versions; do not
introduce a second Python toolchain. The map should look and behave like
house-hunter's so the projects feel like one system. Propose extracting a shared
geo-UI workspace package only if a third consumer clearly earns it; until then,
reuse house-hunter in place.

## 3. The two epistemic constraints (these shape the whole design)

These are first-class constraints, not caveats. They are the reason a naive
"school score" is misleading, and they drive the data model and the UI.

### 3.1 Outcomes measure neighborhoods and demographics, not schools

Raw proficiency levels and neighborhood adult-outcome statistics (Opportunity
Atlas) are dominated by family income and selection, not by what a school does.
Attributing them to a school is an ecological fallacy. The signal that actually
reflects a school is growth and value-added (how much students improve) and
on-track indicators, not levels.

Design rule: every metric is tagged `school_effect` or `context`. The scorecard
presents them in separate groups and never sells a context metric as a
school-quality metric. Neighborhood adult outcomes are shown as context for the
catchment, explicitly labeled as a property of where children grow up.

### 3.2 Public, charter, and private are not symmetrically measured

Charters are public schools: they appear in NCES, CRDC, and ODE data and compare
directly. Private schools are a data desert: no state assessments, no CRDC, only
the thin federal Private School Survey (PSS). The comparison is therefore
structurally lopsided.

Design rule: every school carries a per-domain `data_completeness` and an overall
`confidence`. The UI shows "no comparable data" honestly rather than implying a
private school is worse because its cells are blank. The meets-or-beats
comparison (Section 6) must neither penalize a private school for missing cells
nor credit it for unmeasured quality. Private quality is assessed from available
proxies (accreditation, PSS student-teacher ratio, voluntarily reported scores,
published college matriculation, structured reviews) at explicitly lower
confidence. Include private schools agnostically: a private option earns a place
in the comparison on evidence, not on type.

## 4. Architecture: entity resolution + spatial join

Two facts drive the design:

1. Every source names schools differently. The spine is a canonical school
   identity (NCES School ID as primary key; NCES PSS ID for private schools) plus
   a crosswalk mapping each source's id to the canonical id. This is master-data
   management / entity resolution.
2. The richest outcome data is keyed to geography (Census tracts), not to
   schools. The catchment polygon is the join key that attributes tract-level
   adult outcomes to a school (find the tracts inside the catchment, area-weight
   their values). The polygon is both the headline UI feature and the spatial
   join.

Two linking strategies, therefore:
- School-keyed data joins on NCES ID (NCES, CRDC, ODE, EDFacts).
- Geography-keyed data joins by spatial containment through the catchment
  (Opportunity Atlas, ACS, local crime).

Storage layers: raw landing (immutable source pulls, tagged with vintage) ->
normalized -> serving views. DuckDB + spatial extension. Every fact row carries
provenance: source, source vintage (school year), ingest timestamp, and URL.

CRS discipline: do spatial math (point-in-polygon, area weighting) in EPSG:2913
(Oregon State Plane North, feet); reproject to EPSG:4326 only for Leaflet
display, using the reused flip+transform idiom. Driving distance is a routing
concern, not a projected-distance concern (Section 7).

## 5. Data contracts (canonical model)

DuckDB tables (see `src/school_lens/db.py` for authoritative DDL):

- `schools` (dim): `nces_id` PK, `name`, `school_type`
  (traditional_public | charter | magnet | private_religious | private_independent),
  `level` (elementary | middle | high | other), `grade_low`, `grade_high`,
  `district_name`, `street`, `city`, `zip`, `lat`, `lon`,
  `point` GEOMETRY (EPSG:2913), `enrollment`, `data_completeness` JSON,
  provenance columns.
- `source_crosswalk`: `source`, `source_id`, `nces_id`, `match_method`,
  `confidence`. Resolves any source's identifier to the canonical school.
- `boundaries`: `nces_id`, `level` (grade band), `school_year`, `geometry`
  (EPSG:2913), provenance. Boundaries change yearly and differ by grade band.
- Domain fact tables, each time-stamped by `school_year` with provenance:
  - `fact_academics` (assessment proficiency level, growth/value-added,
    grad rate, 9th-grade on-track, college-going rate).
  - `fact_discipline_safety` (suspensions, expulsions, referrals to law
    enforcement, school-related arrests, drug/alcohol/weapon incidents,
    chronic absenteeism, restraint/seclusion).
  - `fact_staffing` (student-teacher ratio, counselor ratio, SRO presence).
  - `fact_finance` (per-pupil spending where available).
- Geography tables (joined spatially through the catchment):
  - `tract_outcomes` (Opportunity Atlas: adult income percentile, incarceration
    rate, college attendance, by tract).
  - `tract_acs` (ACS income, educational attainment, poverty, by tract).
- `school_mentions` (Phase 2, unstructured): raw text + LLM-extracted structured
  signal, with source URL, extraction model, and confidence. Provenance-first.

Every metric is registered in `domain/metrics.py` with: id, label, domain,
`kind` (school_effect | context), `direction` (higher_is_better |
lower_is_better), unit, and the source it comes from. The registry, not ad hoc
code, decides how a metric is grouped, scored, and labeled.

## 6. The comparison engine

Given an address:
1. Geocode to a point (house-hunter `parcels_base` first; external geocoder
   fallback).
2. Resolve the assigned public school per grade band by point-in-polygon against
   `boundaries`.
3. Build the candidate set: the assigned public school(s) plus charter and
   private schools within a driving radius.
4. Score each candidate on the registered metrics, grouped school_effect vs
   context, each cell carrying a value and a confidence; missing data is missing,
   never zero.
5. Apply the meets-or-beats filter: surface charter/private options that match or
   exceed the assigned public on the school_effect metrics they both report,
   respecting confidence; never compare across a metric only one side reports.
6. Present a transparent scorecard (per-metric table plus a radar), with
   user-adjustable weights. No black-box composite rating.

## 7. Geospatial UX (reused house-hunter shell)

Address in -> illuminate the catchment polygon, drop a home marker and a school
marker, draw the route, and report driving distance and time. Driving distance
uses a routing engine (OSRM self-hosted is the target; a haversine straight-line
is the labeled-approximate fallback until routing is wired). Stretch: drive-time
isochrones ("schools within a 15-minute drive"), and the house-hunter
Leaflet-Draw selection idiom for ad hoc areas.

## 8. Ingest sources

The authoritative source registry, with verified URLs and per-source confidence,
lives in `docs/data-sources.md`. Summary by link strategy:

- School-keyed: NCES CCD (public directory + geocodes, the canonical id), NCES
  PSS (private directory), CRDC (discipline, arrests, law-enforcement referrals),
  ODE report cards (Oregon assessment, graduation, on-track, college-going,
  incidents).
- Geography-keyed: Opportunity Atlas (tract adult outcomes), ACS (tract income
  and attainment).
- Boundaries: PortlandMaps COP_OpenData School Attendance Areas (Multnomah,
  verified REST), Metro RLIS district boundaries (verified REST), Beaverton and
  Hillsboro district FeatureServers (exist, endpoint discovery pending), NCES
  EDGE SABS (national fallback, stale vintage).
- Unstructured (Phase 2): local news, district board minutes, structured reviews.

## 9. Build plan (staged, ship each before the next)

1. Spine: canonical model + crosswalk + boundary ingest (PPS/Multnomah first);
   address -> assigned-school resolution; reused map shell renders the catchment,
   home, and school with driving distance. Prove the data and UX reuse end to
   end before any scoring.
2. School-keyed facts: NCES directory, ODE report cards, CRDC discipline/safety.
   Populate `fact_*` with provenance.
3. Geography-keyed outcomes: Opportunity Atlas + ACS, area-weighted through the
   catchment into context metrics.
4. Comparison engine: scorecard, meets-or-beats, confidence and asymmetry
   handling, private-school proxies.
5. Unstructured enrichment: mentions ingest + LLM extraction into signals.

## 10. Accuracy and uncertainty contract

- Distinguish authoritative from approximate in code, in the data
  (`confidence`), and in the UI. Source geometry and official statistics are as
  authoritative as their vintage; straight-line distance, area-weighted tract
  attribution, and any inferred crosswalk match are approximations and must be
  labeled.
- Do not invent endpoint URLs, file paths, metric values, or match keys. If a
  value is unknown, mark it UNVERIFIED and surface it rather than guessing.
- Surface the Section 3.1 selection caveat in the UI wherever a context metric is
  shown, so the tool never launders demographics into an implied school score.

## 11. Working style and epistemic contract

- Tone: analytical, precise, neutral. State trade-offs and certainty levels
  explicitly. "We" means Dane plus AI collaborators.
- Lead with the answer when clear; build from first principles when warranted.
  Calibrated confidence over hedging. Distinguish "I don't know" from
  "unknowable."
- On disagreement or likely error: identify the claim, cite the reference, reason
  from first principles. Agreement should track evidence, not social pressure.
- Prefer structured outputs (tables, code) without meta-narration. Cite
  authoritative sources.
- Formatting: avoid em-dashes; use commas, parentheses, semicolons, colons, or
  sentence breaks. ASCII only in any print() output (Windows console is cp1252;
  no box-drawing characters). No emoji unless asked.
