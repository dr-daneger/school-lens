"""Coordinate-system normalization helpers."""
from __future__ import annotations

import geopandas as gpd

TARGET_CRS = "EPSG:2913"   # Oregon State Plane North, feet
DISPLAY_CRS = "EPSG:4326"  # WGS84 for Leaflet / Folium


def to_target(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to EPSG:2913 (Oregon Lambert / State Plane North, feet)."""
    if gdf.empty:
        if gdf.crs is None:
            return gdf.set_crs(TARGET_CRS)
        return gdf.to_crs(TARGET_CRS) if str(gdf.crs) != TARGET_CRS else gdf
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS; cannot reproject safely.")
    if str(gdf.crs) == TARGET_CRS:
        return gdf
    return gdf.to_crs(TARGET_CRS)


def to_display(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to WGS84 for map display."""
    if gdf.empty:
        if gdf.crs is None:
            return gdf.set_crs(DISPLAY_CRS)
        return gdf.to_crs(DISPLAY_CRS) if str(gdf.crs) != DISPLAY_CRS else gdf
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS; cannot reproject safely.")
    if str(gdf.crs) == DISPLAY_CRS:
        return gdf
    return gdf.to_crs(DISPLAY_CRS)


def bbox_around(lon: float, lat: float, half_ft: float) -> tuple[float, float, float, float]:
    """Build a WGS84 bbox roughly `half_ft` feet on each side of (lon,lat).

    Uses a rough conversion (1 deg lat ≈ 364320 ft); fine for fetch bboxes
    where over-inclusion is preferable to under-inclusion.
    """
    import math
    dy_deg = half_ft / 364320.0
    dx_deg = half_ft / (364320.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lon - dx_deg, lat - dy_deg, lon + dx_deg, lat + dy_deg)
