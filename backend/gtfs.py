"""CTA GTFS static loader: stops, routes, trips, and decimated shape polylines."""
import csv
import io
import json
import math
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
GTFS_ZIP = HERE / "cta_gtfs.zip"
SHAPE_CACHE = HERE / "data" / "shapes_cache.json"
MAX_SHAPE_POINTS = 250


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class GTFS:
    def __init__(self, zip_path=None):
        self.zip_path = Path(zip_path or GTFS_ZIP)
        self.stops = {}          # stop_id -> row
        self.routes = {}         # route_id -> row
        self.route_shapes = {}   # route_id -> [shape_id, ...]
        self._shape_cache = {}
        if SHAPE_CACHE.exists():
            try:
                self._shape_cache = json.loads(SHAPE_CACHE.read_text())
            except Exception:
                self._shape_cache = {}
        self._load()

    def _read(self, name):
        with zipfile.ZipFile(self.zip_path) as z:
            with z.open(name) as f:
                return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))

    def _load(self):
        for row in self._read("stops.txt"):
            self.stops[row["stop_id"]] = row
        for row in self._read("routes.txt"):
            self.routes[row["route_id"]] = row
        for row in self._read("trips.txt"):
            self.route_shapes.setdefault(row["route_id"], set()).add(row["shape_id"])
        self.route_shapes = {k: sorted(v) for k, v in self.route_shapes.items()}

    # ---- stops ----
    def rail_stations(self):
        """Parent stations (location_type=1), used for train mapid."""
        return [s for s in self.stops.values() if s.get("location_type") == "1"]

    def nearby(self, lat, lon, radius_m=800, limit=12):
        lat, lon = float(lat), float(lon)
        hits = []
        for s in self.stops.values():
            try:
                slat, slon = float(s["stop_lat"]), float(s["stop_lon"])
            except (ValueError, TypeError):
                continue
            d = haversine_m(lat, lon, slat, slon)
            if d <= radius_m:
                loc_type = s.get("location_type") or "0"
                kind = "rail_station" if loc_type == "1" else "bus_stop"
                if kind == "bus_stop" and s.get("parent_station"):
                    kind = "rail_platform"
                hits.append({
                    "stop_id": s["stop_id"],
                    "stop_code": s.get("stop_code") or "",
                    "name": s.get("stop_name") or "",
                    "kind": kind,
                    "lat": slat, "lon": slon,
                    "distance_m": round(d),
                })
        # prefer parent stations over individual platforms for rail
        hits.sort(key=lambda h: (h["distance_m"], 0 if h["kind"] == "rail_station" else 1))
        seen = set()
        deduped = []
        for h in hits:
            key = (h["kind"], h["name"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(h)
            if len(deduped) >= limit:
                break
        return deduped

    # ---- shapes ----
    def shape_for_route(self, route_id):
        """Decimated [[lat, lon], ...] polyline for a route (cached on disk)."""
        if route_id in self._shape_cache:
            return self._shape_cache[route_id]
        shape_ids = self.route_shapes.get(route_id, [])
        if not shape_ids:
            return []
        pts = []
        with zipfile.ZipFile(self.zip_path) as z:
            with z.open("shapes.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                wanted = set(shape_ids)
                buf = {}
                for row in reader:
                    sid = row["shape_id"]
                    if sid in wanted:
                        buf.setdefault(sid, []).append(row)
        # pick the longest shape (most representative), decimate
        best = max(buf.values(), key=len) if buf else []
        best.sort(key=lambda r: int(r["shape_pt_sequence"]))
        pts = [[float(r["shape_pt_lat"]), float(r["shape_pt_lon"])] for r in best]
        if len(pts) > MAX_SHAPE_POINTS:
            step = len(pts) / MAX_SHAPE_POINTS
            pts = [pts[int(i * step)] for i in range(MAX_SHAPE_POINTS)]
        self._shape_cache[route_id] = pts
        try:
            SHAPE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SHAPE_CACHE.write_text(json.dumps(self._shape_cache))
        except Exception:
            pass
        return pts

    def rail_route_ids(self):
        return [rid for rid, r in self.routes.items() if r.get("route_type") == "1"]

    def bus_route_ids(self):
        return [rid for rid, r in self.routes.items() if r.get("route_type") == "3"]
