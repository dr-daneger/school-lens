"""Source-specific ingest. One module per source; all idempotent and resumable.

Boundaries flow through the reused house_hunter ArcGIS puller; flat-file sources
(NCES, CRDC, ODE, Opportunity Atlas, ACS) download then upsert. Every write
carries provenance (source, vintage, ingest_ts, url). See docs/data-sources.md.
"""
