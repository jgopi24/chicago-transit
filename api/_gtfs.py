"""Slim GTFS loader for the Vercel serverless API.

Backed by precomputed JSON (see scripts/build_vercel_data.py) instead of the
68MB GTFS zip, so cold starts stay fast. Same query interface as backend/gtfs.py
for the endpoints that need it: nearby stops, routes, rail shapes.
"""
import json
import math
from pathlib import Path

DATA = Path(__file__).parent / "data"


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class GTFS:
    def __init__(self):
        self.stops = json.loads((DATA / "stops.json").read_text())
        self.routes = json.loads((DATA / "routes.json").read_text())
        self.rail_shapes = json.loads((DATA / "rail_shapes.json").read_text())

    def nearby(self, lat, lon, radius_m=800, limit=12):
        lat, lon = float(lat), float(lon)
        hits = []
        for s in self.stops:
            d = haversine_m(lat, lon, s["lat"], s["lon"])
            if d <= radius_m:
                loc_type = s.get("location_type") or "0"
                kind = "rail_station" if loc_type == "1" else "bus_stop"
                if kind == "bus_stop" and s.get("parent_station"):
                    kind = "rail_platform"
                hits.append({
                    "stop_id": s["stop_id"],
                    "stop_code": s.get("stop_code") or "",
                    "name": s.get("name") or "",
                    "kind": kind,
                    "lat": s["lat"], "lon": s["lon"],
                    "distance_m": round(d),
                })
        hits.sort(key=lambda h: (h["distance_m"], 0 if h["kind"] == "rail_station" else 1))
        seen, deduped = set(), []
        for h in hits:
            key = (h["kind"], h["name"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(h)
            if len(deduped) >= limit:
                break
        return deduped

    def shape_for_route(self, route_id):
        """Precomputed decimated polyline for a rail route_id."""
        return self.rail_shapes.get(route_id, [])

    def find_rail_route_id(self, rt):
        """Match a rail route by id or short name (e.g. 'Red')."""
        for rid, r in self.routes.items():
            if r.get("type") == "1" and (r.get("short_name") == rt or rid == rt):
                return rid
        return None
