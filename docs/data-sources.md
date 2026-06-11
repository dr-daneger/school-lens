# Data Source Registry: school-lens

Authoritative list of ingest sources, the linking strategy each uses, and a
per-source confidence on whether the access path is verified. Re-verify URLs and
vintages before a fresh ingest; mark anything not personally confirmed UNVERIFIED.

Recon date: 2026-05-29. Confidence legend:
- VERIFIED: source and access path confirmed this session.
- SOURCE-OK: the dataset definitely exists and is public; exact file URL or layer
  id still needs a confirming fetch.
- UNVERIFIED: the data exists in a viewer, but a machine-readable endpoint has
  not been confirmed; discovery step required.

## Linking strategies

- School-keyed: joins to the canonical school on NCES ID via `source_crosswalk`.
- Geography-keyed: joins by spatial containment through the catchment polygon
  (Census tract -> area-weight -> school).

## Boundaries (the critical-path dependency)

| Source | Coverage | Strategy | Endpoint | Confidence | Notes |
|:---|:---|:---|:---|:---|:---|
| PortlandMaps COP_OpenData, School Attendance Areas | Multnomah / Portland-area, elem/mid/high | geometry | `https://www.portlandmaps.com/arcgis/rest/services/Public/COP_OpenData/MapServer` | VERIFIED (source) | Compiled by Multnomah County GIS with district input. Confirm the layer id for "School Attendance Areas" against `.../MapServer/layers`. Pull with reused `query_arcgis_layer`. |
| Metro RLIS BoundaryDataWebMerc, School District Boundaries (layer 9) | Tri-county | geometry (district level) | `https://gis.oregonmetro.gov/arcgis/rest/services/OpenData/BoundaryDataWebMerc/MapServer/9` | VERIFIED | District polygons, not attendance areas. Use to resolve the district an address sits in. |
| Beaverton SD attendance areas (FLO Analytics school locator) | Beaverton SD, elem/mid/high | geometry | `https://services1.arcgis.com/DjfAyvUwdiY6gnFC/arcgis/rest/services/BeavertonSD_SchoolLocator_Data/FeatureServer` (layers 1=ES, 2=MS, 3=HS; name field `School_Name_Abbreviated`) | VERIFIED (2026-06-06, live pull) | Live-pulled 48 named polygons (33 ES, 9 MS, 6 HS). Service refreshed ~May 2026 (current). Found via the BSD GIS owner's public ArcGIS items. CAUTION: the previously recorded web-app id `20778e80...` is "School Board Zones Lookup" (board-director electoral zones), NOT attendance areas. CLI: `ingest-boundaries beaverton`. |
| Hillsboro SD attendance boundaries (Angelo Planning Group) | Hillsboro SD, elem/mid/high | geometry | Three FeatureServers on `services5.arcgis.com/bQMB4G4scQKPv0h5/arcgis/rest/services/`: `Elementary_School_Attendance_Boundary` (layer 36), `Middle_School_Attendance_Boundary` (layer 38), `High_School_Attendance_Boundary` (layer 37); name field `SchoolName` | VERIFIED (2026-06-06, live pull) | Live-pulled 36 named polygons (27 ES, 5 MS, 4 HS). Vintage: ES May 2023, MS/HS Sep 2022. Polygon layer id is non-zero and per-service, so it is discovered at run time, not hard-coded. CLI: `ingest-boundaries hillsboro`. |
| NCES EDGE SABS (School Attendance Boundary Survey) | National | geometry | nces.ed.gov/programs/edge/ | SOURCE-OK | National fallback. Vintage is ~2015-16 and stale where boundaries have since changed; use only to fill gaps, flagged stale. |

## School-keyed sources

| Source | What | Strategy | Endpoint | Confidence | Notes |
|:---|:---|:---|:---|:---|:---|
| NCES Common Core of Data (CCD) | Public + charter directory, enrollment, demographics, FRL, ratios, geocodes | school-keyed (defines the id) | nces.ed.gov/ccd/ ; geocodes via NCES EDGE | SOURCE-OK | The canonical NCES School ID + lat/lon backbone. Flat-file CSV. |
| NCES Private School Survey (PSS) | Private school directory, enrollment, student-teacher ratio, affiliation | school-keyed | nces.ed.gov/surveys/pss/ | SOURCE-OK | The only national private list. Biennial; thin on outcomes. Primary private-school spine. |
| Civil Rights Data Collection (CRDC) | Suspensions, expulsions, referrals to law enforcement, school-related arrests, restraint/seclusion, chronic absenteeism, course access, SROs | school-keyed | civilrightsdata.ed.gov ; ocrdata.ed.gov | VERIFIED (content) | Public + charter only (no private). Latest full collection 2020-21; 2017-18 also available. The drug/police-involvement source. Flat-file CSV. |
| Oregon ODE Report Cards / At-A-Glance | Assessment proficiency, graduation, 9th-grade on-track, regular attenders, college-going, class size; ODE incident reporting (drug/alcohol/weapon) | school-keyed | oregon.gov/ode report cards; `https://www.ode.state.or.us/data/ReportCard/Reports/Archive` | VERIFIED (source) | Public + charter. Downloadable report card archive. College-going rate is gettable; college rank of attendance per HS is not (paywalled NSC). |

## Private-school quality proxies (CLAUDE.md 3.2)

Private schools are a data desert: no state assessments, no CRDC. The online and
research consensus is that no single metric cleanly measures private-school
quality. Test-score LEVELS are selection-confounded just like public ones, and
value-added (the cleanest school signal) cannot be computed without the
assessment data private schools do not report. The least-bad signal parents and
researchers actually cite is college matriculation / longer-run outcomes, with
accreditation as a quality floor. Both are themselves selection-confounded, so
school-lens registers them as `private_proxy` metrics (domain `private_signals`):
explicitly lower confidence (`PRIVATE_PROXY_CONFIDENCE`), kept out of the public
school_effect comparison and out of the default quality score, surfaced in a
dedicated caveated panel.

| Proxy | Metric id | Source | Confidence | Notes |
|:---|:---|:---|:---|:---|
| College matriculation (PRIMARY) | `college_matriculation` | School-published profile / "report to families"; aggregated by Niche / PrivateSchoolReview; NCES PSS for the directory spine | UNVERIFIED (per-school; Phase 2 unstructured) | The best-cited private-quality signal. Published per school, not a clean feed; extract into `school_mentions` -> `fact_private_signals` with provenance. Selection-confounded; never folded into the public score. |
| Accreditation (FLOOR) | `accreditation` | NCES PSS affiliation; regional accreditors NWAC / Cognia (AdvancED); NAIS membership for independents | SOURCE-OK | A recognized-accreditation quality floor, not a differentiator. Stored boolean 0/1. |
| Student-teacher ratio | `student_teacher_ratio` (existing) | NCES PSS | SOURCE-OK | Already registered (staffing/context); weak but the one structured number PSS gives for privates. |

## Geography-keyed sources

| Source | What | Strategy | Endpoint | Confidence | Notes |
|:---|:---|:---|:---|:---|:---|
| Opportunity Atlas (Opportunity Insights + Census) | Tract-level adult outcomes: income percentile, incarceration rate, college attendance, by parental income/race/sex | geography-keyed | opportunityinsights.org/data ; census.gov Opportunity Atlas Data Tables | VERIFIED (source) | Childhood-tract keyed; cohort born 1978-1985. This is the adult-income and incarceration signal. CONTEXT metrics only (Section 3.1). Tract-level CSV. |
| ACS 5-year (Census) | Tract household income, educational attainment, poverty | geography-keyed | data.census.gov ; Census API | SOURCE-OK | Characterizes the catchment. CONTEXT metrics. |

## Outcome-chained / hard

| Source | What | Confidence | Notes |
|:---|:---|:---|:---|
| College Scorecard | Earnings 10 yrs out, institution rank-ish | SOURCE-OK | Per-institution. The missing link is high-school -> college destination counts (National Student Clearinghouse, mostly paywalled). College-going RATE comes from ODE; college RANK of attendance per HS is not freely available. Mark the destination chain UNVERIFIED. |

## Unstructured (Phase 2)

Local news, district board minutes, structured reviews (Niche/Google), bond
measures, principal turnover. Ingest raw text + LLM extraction into
`school_mentions` with source URL, extraction model, and confidence. Phased after
the structured spine per CLAUDE.md Section 9.

## Sources consulted (2026-05-29)

- [PortlandMaps COP_OpenData MapServer](https://www.portlandmaps.com/arcgis/rest/services/Public/COP_OpenData/MapServer)
- [City of Portland: School Attendance Areas](https://gis-pdx.opendata.arcgis.com/datasets/school-attendance-areas)
- [Metro RLIS: School District Boundaries (layer 9)](https://gis.oregonmetro.gov/arcgis/rest/services/OpenData/BoundaryDataWebMerc/MapServer/9)
- [Beaverton SD boundary information](https://www.beaverton.k12.or.us/departments/long-range-facility-planning/boundary-information)
- [Hillsboro SD attendance boundaries](https://www.hsd.k12.or.us/for-families/boundaries-and-transfers/attendance-boundaries)
- [Opportunity Atlas (Opportunity Insights)](https://opportunityinsights.org/atlasresources/)
- [Opportunity Atlas Data Tables (Census)](https://www.census.gov/programs-surveys/ces/data/public-use-data/opportunity-atlas-data-tables.html)
- [NCES Civil Rights Data Collection (CRDC)](https://nces.ed.gov/admindata/crdc/)
- [Oregon ODE Report Cards](https://www.oregon.gov/ode/schools-and-districts/reportcards/pages/default.aspx)
- [ODE Report Card Download Archive](https://www.ode.state.or.us/data/ReportCard/Reports/Archive)

## Sources consulted (2026-06-06, boundary + private-proxy recon)

Boundary endpoint discovery (ArcGIS item ids resolved to REST service URLs):
- Beaverton SD School Locator data (FLO Analytics), item `1b79fdb0f30049d4961f49cc040e6cbb`
- Beaverton SD GIS owner `mccrackenro` public items (Elementary/Middle/High School Boundaries)
- Hillsboro SD attendance boundaries (Angelo Planning Group, owner `APGuser3`), items `641b6037...`, `fc6b6273...`, `14524ead...`

Private-school quality proxy (what people online and the research cite as best):
- [The 74: What's the Best Way to Measure a School's Quality?](https://www.the74million.org/article/whats-the-best-way-to-measure-a-schools-quality-5-factors-to-consider/)
- [Stanford CEPA: The Challenges of Measuring School Quality](https://cepa.stanford.edu/sites/default/files/The%20Challenges%20of%20Measuring%20School%20Quality.pdf)
- [GreatSchools: comparing private and public school test scores](https://www.greatschools.org/gk/parenting/school-ratings/comparing-private-public-school-test-scores/)
- [Council for American Private Education: Academic Performance](https://capenetwork.org/academic-performance/)
- [PrivateSchoolReview: How to Choose the Right Private School](https://www.privateschoolreview.com/blog/how-to-choose-the-right-private-school-in-2026)

## Ingest status (2026-06-06 build)

Primary access path discovered for school-keyed federal data: the Urban Institute
Education Data Portal API (`https://educationdata.urban.org/api/v1/`), JSON,
filterable by `fips=41` and `county_code` (41067 Washington, 41051 Multnomah). One
shared client at `src/school_lens/ingest/_urban.py`. Rebuild everything with
`python scripts/build_data.py`.

| Source | Endpoint / file | Status | What it fills |
|:---|:---|:---|:---|
| NCES CCD directory | `/schools/ccd/directory/2023/?fips=41` | WIRED | schools spine (305), enrollment, geocode, student-teacher ratio |
| EDFacts grad rates | `/schools/edfacts/grad-rates/2019/` (total row, midpt) | WIRED | grad_rate (school_effect) |
| EDFacts assessments | `/schools/edfacts/assessments/2018/grade-99/` (total row) | WIRED | ela/math proficiency (context) |
| CRDC | `/schools/crdc/discipline/2021/disability/sex/` (race=99&sex=99&disability=99&lep=99), `/crdc/teachers-staff/2021/`, `/crdc/chronic-absenteeism/2021/race/sex/` | WIRED | suspension/expulsion/referral/arrest/chronic rates, counselor ratio, SRO |
| ODE report-card media | select-and-download UI at ode.state.or.us/data/ReportCard/Media | DEFERRED | median growth percentile, 9th-grade on-track, college-going, drug/alcohol incidents (needs browser automation) |
| Opportunity Atlas | opportunityinsights.org tract CSV | DEFERRED | tract_outcomes (+ catchment area-weight gather to wire) |
| ACS 5-year | Census API, tracts in 41067/41051 | DEFERRED | tract_acs (+ area-weight gather) |
| NCES PSS | nces.ed.gov/surveys/pss flat file (NOT in Urban API) | DEFERRED | private-school spine + accreditation/ratio |
| PortlandMaps attendance | old COP_OpenData MapServer returns 0 layers | BROKEN | Portland attendance polygons; find the new Hub feature service (Portland schools are already in the CCD spine) |
