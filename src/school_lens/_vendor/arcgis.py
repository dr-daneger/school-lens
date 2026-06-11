"""Minimal ArcGIS REST query helper.

Just enough to support a bbox-filtered GeoJSON pull for any FeatureServer or
MapServer/N layer. Pagination via resultOffset / resultRecordCount.
"""
from __future__ import annotations

import json
from typing import Any

import geopandas as gpd
import httpx
from shapely.geometry import shape


def _get_with_retry(client: httpx.Client, url: str, params: dict, timeout: float,
                    max_retries: int = 3) -> httpx.Response:
    """GET with exponential backoff on 500/502/503/504 and ReadTimeout."""
    import time
    for attempt in range(max_retries + 1):
        try:
            r = client.get(url, params=params, timeout=timeout)
            if r.status_code in (500, 502, 503, 504) and attempt < max_retries:
                time.sleep(1.5 ** attempt)
                continue
            return r
        except (httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt >= max_retries:
                raise
            time.sleep(1.5 ** attempt)
    raise RuntimeError("unreachable")


def _query_single_bbox(
    mapserver_root: str, layer_id: int, bbox_wgs84: tuple[float, float, float, float] | None,
    where: str, out_fields: str, page_size: int, timeout: float,
    client: httpx.Client, fmt: str = "geojson",
) -> list[dict]:
    """Query one bbox (no subdivision). Returns the raw ArcGIS features list."""
    url = f"{mapserver_root.rstrip('/')}/{layer_id}/query"
    params: dict[str, Any] = {
        "where": where,
        "outFields": out_fields,
        "f": fmt,
        "returnGeometry": "true",
        "outSR": 4326,
    }
    if bbox_wgs84 is not None:
        west, south, east, north = bbox_wgs84
        params["geometry"] = json.dumps({
            "xmin": west, "ymin": south, "xmax": east, "ymax": north,
            "spatialReference": {"wkid": 4326},
        })
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = 4326
        params["spatialRel"] = "esriSpatialRelIntersects"

    features: list[dict] = []
    offset = 0
    while True:
        page_params = {**params, "resultOffset": offset,
                       "resultRecordCount": page_size}
        r = _get_with_retry(client, url, page_params, timeout)
        r.raise_for_status()
        body = r.text
        try:
            data = json.loads(body)
        except ValueError as e:
            raise RuntimeError(
                f"ArcGIS non-JSON response (len={len(body)}): {body[:200]!r}"
            ) from e
        if "error" in data:
            raise RuntimeError(f"ArcGIS error from {url}: {data['error']}")
        raw_batch = data.get("features", []) or []
        if fmt == "geojson":
            batch = raw_batch
        else:
            # ArcGIS JSON -> GeoJSON-ish feature dicts
            batch = [_esri_feature_to_geojson(f) for f in raw_batch]
        features.extend(batch)
        if len(raw_batch) < page_size:
            break
        if data.get("exceededTransferLimit") is False:
            break
        offset += page_size
    return features


def _esri_feature_to_geojson(esri: dict) -> dict:
    """Convert one Esri JSON feature into a GeoJSON-like dict."""
    geom = esri.get("geometry") or {}
    if "rings" in geom:
        rings = geom["rings"]
        if len(rings) == 1:
            gj_geom = {"type": "Polygon", "coordinates": rings}
        else:
            # MultiPolygon: Esri doesn't distinguish outer/inner; treat each
            # ring as its own polygon and let shapely fix it downstream.
            gj_geom = {"type": "MultiPolygon",
                       "coordinates": [[r] for r in rings]}
    elif "paths" in geom:
        paths = geom["paths"]
        gj_geom = ({"type": "LineString", "coordinates": paths[0]}
                   if len(paths) == 1
                   else {"type": "MultiLineString", "coordinates": paths})
    elif "x" in geom and "y" in geom:
        gj_geom = {"type": "Point", "coordinates": [geom["x"], geom["y"]]}
    else:
        gj_geom = None
    return {
        "type": "Feature",
        "properties": esri.get("attributes") or {},
        "geometry": gj_geom,
    }


def _tile_bbox(bbox: tuple[float, float, float, float], nx: int, ny: int):
    """Yield nx*ny sub-bboxes covering the given bbox."""
    w, s, e, n = bbox
    dx = (e - w) / nx
    dy = (n - s) / ny
    for ix in range(nx):
        for iy in range(ny):
            yield (w + ix*dx, s + iy*dy, w + (ix+1)*dx, s + (iy+1)*dy)


def query_arcgis_layer(
    mapserver_root: str,
    layer_id: int,
    *,
    bbox_wgs84: tuple[float, float, float, float] | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    page_size: int = 1000,
    timeout: float = 60.0,
    max_tile_depth: int = 3,
    fmt: str = "geojson",
) -> gpd.GeoDataFrame:
    """Query an ArcGIS REST layer, optionally bbox-filtered (WGS84).

    On 500-class errors or oversized responses, recursively subdivides the
    bbox into a 2x2 grid up to `max_tile_depth` levels (16x16 = 256 tiles).

    Returns a GeoDataFrame in WGS84. Caller reprojects to 2913.
    """
    all_features: list[dict] = []
    seen_ids: set = set()

    def _id_of(f: dict) -> Any:
        # Prefer OBJECTID; fall back to a hash of (properties, geometry).
        props = f.get("properties") or {}
        for k in ("OBJECTID", "objectid", "FID", "ObjectID", "OID"):
            if k in props:
                return (k, props[k])
        return None

    def _is_oversize_error(exc: Exception) -> bool:
        """True only for transient / oversize errors worth subdividing on."""
        if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (500, 502, 503, 504)
        # ArcGIS-wrapped RuntimeError: subdivide on "exceeded transfer limit"
        # variants but NOT on 400-class bad-query errors.
        msg = str(exc).lower()
        return ("exceeded" in msg or "timeout" in msg or "memory" in msg
                or "'code': 500" in msg or "'code': 504" in msg)

    def _recurse(box, depth):
        try:
            feats = _query_single_bbox(
                mapserver_root, layer_id, box, where, out_fields,
                page_size, timeout, client, fmt=fmt,
            )
        except (httpx.HTTPStatusError, httpx.ReadTimeout, RuntimeError) as e:
            if not _is_oversize_error(e):
                # Bad query (400) or unknown failure — raise immediately so the
                # caller can fix the query rather than fan out into more bad calls.
                raise
            if depth >= max_tile_depth or box is None:
                raise
            print(f"    [tile depth={depth}] {type(e).__name__} on box "
                  f"{box}, subdividing 2x2...")
            for sub in _tile_bbox(box, 2, 2):
                _recurse(sub, depth + 1)
            return
        for f in feats:
            fid = _id_of(f)
            if fid is None:
                all_features.append(f)
                continue
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            all_features.append(f)

    with httpx.Client(headers={"User-Agent": "buildable-envelope/0.1"}) as client:
        _recurse(bbox_wgs84, 0)

    if not all_features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    rows: list[dict] = []
    geoms = []
    for f in all_features:
        rows.append(f.get("properties", {}) or {})
        geom_dict = f.get("geometry")
        geoms.append(shape(geom_dict) if geom_dict else None)

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    return gdf
