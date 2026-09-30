"""Chicago Transit connector API — Vercel serverless edition.

Same REST surface as the Docker backend, but serverless: no background poller.
Every request fetches live from CTA on demand (with a short per-instance TTL
cache for arrivals/alerts). Personal-use traffic is dozens of calls/day against
CTA's 100k/day limits, so this is fast and cheap — $0 on Vercel's free tier.

Deploy: `vercel` from the repo root (picks up vercel.json), then set env vars
CTA_TRAIN_API_KEY (required), CTA_BUS_API_KEY (optional, for bus endpoints),
CONNECTOR_API_KEY (your own secret — CLI/widgets send it as X-API-Key).
"""
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _cta_client import CTAClient, CTAError  # noqa: E402
from _gtfs import GTFS  # noqa: E402

CONNECTOR_API_KEY = os.environ.get("CONNECTOR_API_KEY", "")
ARRIVAL_CACHE_SEC = 20
ALERT_CACHE_SEC = 60

app = FastAPI(title="Chicago Transit Connector", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

client = CTAClient()
gtfs = GTFS()
_cache = {}  # key -> (ts, payload)


@app.exception_handler(CTAError)
async def _cta_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)


@app.middleware("http")
async def _auth(request: Request, call_next):
    if CONNECTOR_API_KEY and not request.url.path.startswith(("/health", "/docs", "/openapi")):
        if request.headers.get("X-API-Key") != CONNECTOR_API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


def _cached(key, ttl, fn):
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        return _cache[key][1]
    payload = fn()
    _cache[key] = (now, payload)
    return payload


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _bus_vehicle(v):
    try:
        lat, lon = float(v["lat"]), float(v["lon"])
    except (ValueError, TypeError, KeyError):
        return None
    return {
        "id": f"bus-{v.get('vid')}",
        "mode": "bus",
        "route": v.get("rt"),
        "route_name": v.get("rt"),
        "lat": lat, "lon": lon,
        "heading": int(v.get("hdg") or 0),
        "speed_mph": None,  # needs cross-request state; unavailable serverless
        "dest": v.get("des"),
        "pattern": v.get("pid"),
    }


def _train_vehicle(t):
    try:
        lat, lon = float(t["lat"]), float(t["lon"])
    except (ValueError, TypeError, KeyError):
        return None
    rt = t.get("_rt") or t.get("rt")
    return {
        "id": f"train-{t.get('rn')}",
        "mode": "train",
        "route": rt,
        "lat": lat, "lon": lon,
        "heading": int(t.get("heading") or 0),
        "speed_mph": None,
        "dest": t.get("destNm"),
        "next_station": t.get("nextStaNm"),
        "delayed": t.get("isDly") == "1",
        "run": t.get("rn"),
    }


# ---------------- endpoints ----------------

@app.get("/health")
def health():
    return {"ok": True, "time": _iso_now(), "mode": "serverless"}


@app.get("/")
def index():
    return {"service": "chicago-transit-connector", "docs": "/docs", "health": "/health"}


@app.get("/api/vehicles")
def vehicles(mode: str = "all", rt: str = None):
    """Live vehicles. Buses REQUIRE rt (CTA needs a route filter); trains are system-wide."""
    items = []
    if mode in ("all", "train"):
        trains = [_train_vehicle(t) for t in client.train_positions()]
        items += [t for t in trains if t]
    if mode in ("all", "bus"):
        if not rt:
            return JSONResponse(
                {"error": "rt is required for bus vehicles (CTA has no system-wide bus feed)"},
                status_code=400)
        rts = [r.strip() for r in rt.split(",")]
        buses = [_bus_vehicle(v) for v in client.bus_vehicles(rt=",".join(rts[:10]))]
        items += [b for b in buses if b]
    if rt and mode == "train":
        rts = {r.strip() for r in rt.split(",")}
        items = [v for v in items if str(v.get("route")) in rts]
    return {"count": len(items), "vehicles": items, "time": _iso_now()}


@app.get("/api/train/positions")
def train_positions(rt: str = None):
    """Live train positions, optionally filtered to one route (e.g. rt=Red)."""
    trains = [_train_vehicle(t) for t in client.train_positions(rt=rt)]
    trains = [t for t in trains if t]
    return {"count": len(trains), "trains": trains, "time": _iso_now()}


@app.get("/api/fleet")
def fleet(rt: str = None):
    """Counts by route. Trains: system-wide. Buses: pass rt=22,36 (CTA needs route filters)."""
    trains = [_train_vehicle(t) for t in client.train_positions()]
    trains = [t for t in trains if t]

    def breakdown(items):
        d = {}
        for v in items:
            d[str(v.get("route"))] = d.get(str(v.get("route")), 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    buses = []
    bus_note = None
    if rt:
        rts = [r.strip() for r in rt.split(",")]
        buses = [_bus_vehicle(v) for v in client.bus_vehicles(rt=",".join(rts[:10]))]
        buses = [b for b in buses if b]
    else:
        bus_note = "pass rt=<routes> for bus counts (CTA has no system-wide bus feed)"
    return {
        "bus": {"count": len(buses), "by_route": breakdown(buses), "note": bus_note},
        "train": {"count": len(trains), "by_route": breakdown(trains)},
        "total": len(buses) + len(trains),
        "time": _iso_now(),
    }


@app.get("/api/bus/arrivals")
def bus_arrivals(stpid: str = Query(...), rt: str = None, top: int = 5):
    def _fetch():
        prds = client.bus_predictions(stpid=stpid, rt=rt, top=top)
        out = []
        for p in prds:
            out.append({
                "stop_id": p.get("stpid"), "stop_name": p.get("stpnm"),
                "route": p.get("rt"), "direction": p.get("rtdir"),
                "dest": p.get("des"), "vehicle": p.get("vid"),
                "countdown": p.get("prdctdn"),
                "predicted_at": p.get("prdtm"),
                "distance_ft": p.get("dstp"),
                "delayed": p.get("dly") == "1",
            })
        return {"arrivals": out, "time": _iso_now()}
    return _cached(f"bus:{stpid}:{rt}:{top}", ARRIVAL_CACHE_SEC, _fetch)


@app.get("/api/train/arrivals")
def train_arrivals(mapid: str = None, stpid: str = None, rt: str = None, max: int = 8):
    def _fetch():
        etas = client.train_arrivals(mapid=mapid, stpid=stpid, rt=rt, max=max)
        out = []
        for e in etas:
            out.append({
                "station": e.get("staNm"), "platform": e.get("stpDe"),
                "route": e.get("rt"), "run": e.get("rn"),
                "dest": e.get("destNm"),
                "predicted_at": e.get("prdt"), "arrives_at": e.get("arrT"),
                "approaching": e.get("isApp") == "1",
                "scheduled": e.get("isSch") == "1",
                "delayed": e.get("isDly") == "1",
                "lat": e.get("lat"), "lon": e.get("lon"),
            })
        return {"arrivals": out, "time": _iso_now()}
    return _cached(f"train:{mapid}:{stpid}:{rt}:{max}", ARRIVAL_CACHE_SEC, _fetch)


@app.get("/api/compare")
def compare(mode: str = "bus", stpid: str = None, mapid: str = None,
            rt1: str = Query(...), rt2: str = Query(...), top: int = 3):
    """Head-to-head: next arrivals for two routes at the same stop/station."""
    if mode == "bus":
        a = bus_arrivals(stpid=stpid, rt=rt1, top=top)["arrivals"]
        b = bus_arrivals(stpid=stpid, rt=rt2, top=top)["arrivals"]
    else:
        a = train_arrivals(mapid=mapid, rt=rt1, max=top)["arrivals"]
        b = train_arrivals(mapid=mapid, rt=rt2, max=top)["arrivals"]

    def _mins(x):
        c = x.get("countdown")
        if c in ("DUE", "Due"):
            return 0
        try:
            return int(c)
        except (TypeError, ValueError):
            return 999

    winner = None
    if a and b:
        winner = rt1 if _mins(a[0]) <= _mins(b[0]) else rt2
    elif a:
        winner = rt1
    elif b:
        winner = rt2
    return {"rt1": rt1, "rt2": rt2, "next1": a[:top], "next2": b[:top],
            "faster": winner, "time": _iso_now()}


@app.get("/api/alerts")
def alerts(rt: str = None, mode: str = None):
    def _fetch():
        body = client.alerts()
        items = body.get("Alert", [])
        if isinstance(items, dict):
            items = [items]
        if rt:
            rts = {r.strip().lower() for r in rt.split(",")}

            def _match(a):
                import json as _j
                try:
                    blob = _j.dumps(a, default=str).lower()
                except Exception:
                    blob = str(a).lower()
                return any(r in blob for r in rts)

            items = [a for a in items if _match(a)]
        out = []
        for a in items:
            desc = a.get("FullDescription") or {}
            out.append({
                "id": a.get("AlertId"),
                "headline": a.get("Headline"),
                "short": a.get("ShortDescription"),
                "detail_html": desc.get("#cdata-section") if isinstance(desc, dict) else str(desc),
                "severity": a.get("SeverityScore"),
                "impact": a.get("Impact"),
                "event_start": (a.get("EventStart") or ""),
                "event_end": (a.get("EventEnd") or ""),
            })
        return {"count": len(out), "alerts": out, "time": _iso_now()}
    return _cached(f"alerts:{rt}", ALERT_CACHE_SEC, _fetch)


@app.get("/api/nearby")
def nearby(lat: float = Query(...), lon: float = Query(...),
           radius_m: int = 800, limit: int = 12):
    return {"stops": gtfs.nearby(lat, lon, radius_m, limit), "time": _iso_now()}


@app.get("/api/routes")
def routes(mode: str = "all"):
    out = []
    for rid, r in gtfs.routes.items():
        rtype = r.get("type")
        m = "train" if rtype == "1" else "bus" if rtype == "3" else "other"
        if mode != "all" and m != mode:
            continue
        out.append({
            "route_id": rid,
            "short_name": r.get("short_name"),
            "long_name": r.get("long_name"),
            "mode": m,
            "color": r.get("color"),
        })
    return {"count": len(out), "routes": sorted(out, key=lambda x: x["short_name"] or "")}


@app.get("/api/shape")
def shape(mode: str = "bus", rt: str = Query(None), pid: str = None):
    """Polyline [[lat, lon], ...] for a bus pattern (live) or train route (precomputed)."""
    if mode == "bus":
        if not rt and not pid:
            return JSONResponse({"error": "rt or pid required"}, status_code=400)
        ptrs = client.bus_pattern(rt=rt, pid=pid)
        if isinstance(ptrs, dict):
            ptrs = [ptrs]
        ptr = ptrs[0] if ptrs else {}
        pts = ptr.get("pt", [])
        if isinstance(pts, dict):
            pts = [pts]
        coords = [[float(p["lat"]), float(p["lon"])] for p in pts
                  if p.get("lat") and p.get("lon")]
        return {"coords": coords, "route": rt, "pattern": ptr.get("pid")}
    rid = gtfs.find_rail_route_id(rt) if rt else None
    return {"coords": gtfs.shape_for_route(rid) if rid else [], "route": rt}
