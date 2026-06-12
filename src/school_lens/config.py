"""Central configuration for school-lens.

Mirrors house_hunter.config: Pydantic settings, repo-relative data paths, the
two-county bbox, and the verified boundary endpoints. The house-hunter DuckDB is
ATTACHed read-only as the address-geocoding substrate (see db.py). Its path is
resolved from the first existing candidate, so a runtime DB kept in %LOCALAPPDATA%
(the Drive-lock convention that avoids Google Drive File Stream locking DB files
in the repo tree) still resolves without a code change.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

# Project root is 3 levels up from this file (src/school_lens/config.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"

# Runtime DB lives in %LOCALAPPDATA% (not the repo data dir) so Google Drive File
# Stream cannot lock it mid-write (the Drive-lock convention; the OSRM artifacts
# live there too). Falls back to the repo data dir if LOCALAPPDATA is unset.
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
DB_PATH = (Path(_LOCALAPPDATA) / "school-lens" / "school_lens.duckdb"
           if _LOCALAPPDATA else DATA_DIR / "school_lens.duckdb")

# house-hunter is a sibling package; its DB is the geocoding substrate. Prefer an
# existing candidate, else fall back to the repo-data path so the value is still
# meaningful (db.py only ATTACHes when the file exists).
_HH_CANDIDATES = [
    PROJECT_ROOT.parent / "house-hunter" / "data" / "house_hunter.duckdb",
    Path(os.environ.get("LOCALAPPDATA", "")) / "house-hunter" / "house_hunter.duckdb",
]


def _resolve_house_hunter_db() -> Path:
    for candidate in _HH_CANDIDATES:
        if str(candidate) and candidate.exists():
            return candidate
    return _HH_CANDIDATES[0]


class Settings(BaseSettings):
    """Application settings, overridable via SL_-prefixed environment variables."""

    db_path: Path = DB_PATH
    house_hunter_db_path: Path = Field(default_factory=_resolve_house_hunter_db)

    # Spatial reference: math in 2913 (feet), display in 4326 (WGS84).
    target_crs: str = "EPSG:2913"
    display_crs: str = "EPSG:4326"

    # Two-county footprint (Washington + Multnomah), reused from house-hunter.
    # WGS84 [west, south, east, north].
    bbox_west: float = -123.2
    bbox_south: float = 45.35
    bbox_east: float = -122.4
    bbox_north: float = 45.7

    # Comparison candidate-set radius (driving miles; approximated by straight
    # line until OSRM routing is wired, see spatial/distance.py).
    compare_radius_miles: float = 6.0

    # Verified boundary endpoints (docs/data-sources.md).
    # City of Portland School_Boundaries (AGOL org quVN97tn06YNGj9s): ONE
    # composite cell layer covering the metro region, each cell carrying its
    # assigned school per grade band as attributes. Discovered via the City's
    # "Schools, School Attendance Areas, and School Districts" web map (item
    # e713b535b03945329cfcab4a9a20c58f); verified live 2026-06-11. Replaces the
    # retired COP_OpenData MapServer ("service not started").
    cop_school_boundaries_root: str = (
        "https://services.arcgis.com/quVN97tn06YNGj9s/arcgis/rest/services/"
        "School_Boundaries/FeatureServer"
    )
    metro_boundary_root: str = (
        "https://gis.oregonmetro.gov/arcgis/rest/services/OpenData/BoundaryDataWebMerc/MapServer"
    )
    metro_school_district_layer: int = 9

    # Verified district attendance-boundary services (see docs/data-sources.md).
    # Beaverton SD: one current FeatureServer (FLO Analytics "school locator"
    # data, refreshed 2026). Attendance areas are split across layers 1/2/3
    # (elementary/middle/high); the level is discovered from each layer's name,
    # so layer ids are not hard-coded here.
    beaverton_locator_root: str = (
        "https://services1.arcgis.com/DjfAyvUwdiY6gnFC/arcgis/rest/services/"
        "BeavertonSD_SchoolLocator_Data/FeatureServer"
    )
    # Hillsboro SD: three FeatureServers (Angelo Planning Group), one polygon
    # attendance layer each at a non-zero layer id (36/38/37 at recon time;
    # discovered at run time rather than hard-coded so a republish cannot break us).
    hillsboro_elementary_root: str = (
        "https://services5.arcgis.com/bQMB4G4scQKPv0h5/arcgis/rest/services/"
        "Elementary_School_Attendance_Boundary/FeatureServer"
    )
    hillsboro_middle_root: str = (
        "https://services5.arcgis.com/bQMB4G4scQKPv0h5/arcgis/rest/services/"
        "Middle_School_Attendance_Boundary/FeatureServer"
    )
    hillsboro_high_root: str = (
        "https://services5.arcgis.com/bQMB4G4scQKPv0h5/arcgis/rest/services/"
        "High_School_Attendance_Boundary/FeatureServer"
    )

    # Routing: self-hosted OSRM base URL. Defaults to the local native-Windows
    # osrm-routed standing up on :5000 (see scripts/osrm-control.sh and
    # docs/osrm-runbook.md). When osrm-routed is not running, distance.py
    # fast-fails the localhost connect and falls back to a labeled straight-line
    # approximation (method "osrm_unreachable"). Set SL_OSRM_URL="" to disable.
    osrm_url: str = "http://127.0.0.1:5000"

    model_config = {"env_prefix": "SL_"}


settings = Settings()


def bbox() -> tuple[float, float, float, float]:
    """Two-county footprint as (west, south, east, north) in WGS84."""
    return (settings.bbox_west, settings.bbox_south, settings.bbox_east, settings.bbox_north)
