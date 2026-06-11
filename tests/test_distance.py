"""OSRM client: real parse on success, labeled straight-line fallback otherwise."""
import httpx

from school_lens.spatial import distance as D


def test_haversine_one_degree_lat_is_about_69_miles():
    assert 68.5 < D.haversine_miles(45.0, -122.0, 46.0, -122.0) < 69.5


def test_drive_distance_fallback_when_no_osrm():
    out = D.drive_distance((45.5, -122.7), (45.4, -122.8))
    assert out["method"] == "haversine_approx"
    assert out["minutes"] is None
    assert out["geometry"] is None
    assert out["miles"] > 0


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def test_osrm_success_parses_distance_time_and_geometry(monkeypatch):
    payload = {"code": "Ok", "routes": [{
        "distance": 1609.344 * 5,   # 5 miles, in meters
        "duration": 600,            # 10 minutes, in seconds
        "geometry": {"type": "LineString",
                     "coordinates": [[-122.7, 45.5], [-122.8, 45.4]]},
    }]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(payload))
    out = D.drive_distance((45.5, -122.7), (45.4, -122.8),
                           osrm_url="http://127.0.0.1:5000", with_geometry=True)
    assert out["method"] == "osrm"
    assert out["miles"] == 5.0
    assert out["minutes"] == 10.0
    assert out["geometry"]["type"] == "LineString"


def test_osrm_no_route_returns_fallback(monkeypatch):
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: _FakeResp({"code": "NoRoute", "routes": []}))
    out = D.drive_distance((45.5, -122.7), (45.4, -122.8),
                           osrm_url="http://127.0.0.1:5000")
    assert out["method"] == "osrm_unreachable"
    assert out["minutes"] is None


def test_osrm_unreachable_is_surfaced_not_hidden(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    out = D.drive_distance((45.5, -122.7), (45.4, -122.8),
                           osrm_url="http://127.0.0.1:5000")
    assert out["method"] == "osrm_unreachable"   # configured-but-failed, distinct from "not configured"
    assert out["minutes"] is None
    assert out["miles"] > 0
