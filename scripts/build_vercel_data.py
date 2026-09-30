"""Build the slim GTFS JSON bundle for the Vercel serverless API.

Reads backend/cta_gtfs.zip once and writes:
  api/data/stops.json        slim stop list for /api/nearby
  api/data/routes.json       route metadata for /api/routes
  api/data/rail_shapes.json  decimated polylines for the 8 rail routes (/api/shape)

Re-run when CTA publishes a new GTFS feed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from gtfs import GTFS  # noqa: E402

OUT = ROOT / "api" / "data"
OUT.mkdir(parents=True, exist_ok=True)

g = GTFS()

stops = []
for s in g.stops.values():
    try:
        lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
    except (ValueError, TypeError, KeyError):
        continue
    stops.append({
        "stop_id": s["stop_id"],
        "stop_code": s.get("stop_code") or "",
        "name": s.get("stop_name") or "",
        "lat": lat, "lon": lon,
        "location_type": s.get("location_type") or "0",
        "parent_station": s.get("parent_station") or "",
    })
(OUT / "stops.json").write_text(json.dumps(stops, separators=(",", ":")))
print(f"stops.json: {len(stops)} stops")

routes = {}
for rid, r in g.routes.items():
    routes[rid] = {
        "short_name": r.get("route_short_name") or "",
        "long_name": r.get("route_long_name") or "",
        "type": r.get("route_type") or "",
        "color": r.get("route_color") or "",
    }
(OUT / "routes.json").write_text(json.dumps(routes, separators=(",", ":")))
print(f"routes.json: {len(routes)} routes")

rail_shapes = {}
for rid in g.rail_route_ids():
    pts = g.shape_for_route(rid)
    if pts:
        rail_shapes[rid] = pts
(OUT / "rail_shapes.json").write_text(json.dumps(rail_shapes, separators=(",", ":")))
print(f"rail_shapes.json: {len(rail_shapes)} rail routes")
print("done ->", OUT)
