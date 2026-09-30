"""Chicago Transit connector API.

Polls CTA Bus Tracker + Train Tracker every POLL_VEHICLES_SEC, serves a
unified REST API for the Muse skill, CLI, and HTML widgets.
"""
import math
import os
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cta_client import CTAClient, CTAError
from .gtfs import GTFS, haversine_m

CONNECTOR_API_KEY = os.environ.get("CONNECTOR_API_KEY", "")
POLL_VEHICLES_SEC = int(os.environ.get("POLL_VEHICLES_SEC", "30"))
POLL_ALERTS_SEC = int(os.environ.get("POLL_ALERTS_SEC", "120"))
ARRIVAL_CACHE_SEC = 20

app = FastAPI(title="Chicago Transit Connector", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

client = CTAClient()
gtfs = GTFS()


@app.exception_handler(CTAError)
async def _cta_error(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)

store = {
    "bus_vehicles": [],
    "train_vehicles": [],
    "alerts": [],
    "updated_at": {},
    "errors": {},
}
prev_bus_pos = {}   # vid -> (lat, lon, ts)
arrival_cache = {}  # key -> (ts, payload)


@app.middleware("http")
async def _auth(request: Request, call_next):
    if CONNECTOR_API_KEY and not request.url.path.startswith(("/health", "/docs", "/openapi")):
        if request.headers.get("X-API-Key") != CONNECTOR_API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


def _speed_mph(vid, lat, lon, now):
    prev = prev_bus_pos.get(vid)
    prev_bus_pos[vid] = (lat, lon, now)
    if not prev:
        return None
    plat, plon, pt = prev
    dt = now - pt
    if dt <= 0:
        return None
    miles = haversine_m(plat, plon, lat, lon) / 1609.344
    mph = miles / dt * 3600
    return round(mph, 1) if mph < 80 else None  # GPS jitter guard


def refresh_vehicles():
    now = time.time()
    # ---- buses ----
    try:
        vehicles = client.bus_vehicles()
        out = []
        for v in vehicles:
            try:
                lat, lon = float(v["lat"]), float(v["lon"])
            except (ValueError, TypeError, KeyError):
                continue
            out.append({
                "id": f"bus-{v.get('vid')}",
                "mode": "bus",
                "route": v.get("rt"),
                "route_name": v.get("rt"),  # enriched below if cheap
                "lat": lat, "lon": lon,
                "heading": int(v.get("hdg") or 0),
                "speed_mph": _speed_mph(v.get("vid"), lat, lon, now),
                "dest": v.get("des"),
                "pattern": v.get("pid"),
            })
        store["bus_vehicles"] = out
        store["updated_at"]["vehicles_bus"] = now
        store["errors"].pop("bus", None)
    except Exception as e:
        store["errors"]["bus"] = str(e)
    # ---- trains ----
    try:
        trains = client.train_positions()
        out = []
        for t in trains:
            try:
                lat, lon = float(t["lat"]), float(t["lon"])
            except (ValueError, TypeError, KeyError):
                continue
            rt = t.get("_rt") or t.get("rt")
            out.append({
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
            })
        store["train_vehicles"] = out
        store["updated_at"]["vehicles_train"] = now
        store["errors"].pop("train", None)
    except Exception as e:
        store["errors"]["train"] = str(e)


def refresh_alerts():
    try:
        body = client.alerts()
        alerts = body.get("Alert", [])
        if isinstance(alerts, dict):
            alerts = [alerts]
        store["alerts"] = alerts
        store["updated_at"]["alerts"] = time.time()
        store["errors"].pop("alerts", None)
    except Exception as e:
        store["errors"]["alerts"] = str(e)


def poll_loop():
    last_alerts = 0
    while True:
        refresh_vehicles()
        if time.time() - last_alerts > POLL_ALERTS_SEC:
            refresh_alerts()
            last_alerts = time.time()
        time.sleep(POLL_VEHICLES_SEC)


@app.on_event("startup")
def _startup():
    threading.Thread(target=poll_loop, daemon=True).start()


def _cached(key, fn):
    now = time.time()
    if key in arrival_cache and now - arrival_cache[key][0] < ARRIVAL_CACHE_SEC:
        return arrival_cache[key][1]
    payload = fn()
    arrival_cache[key] = (now, payload)
    return payload


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


# ---------------- endpoints ----------------

@app.get("/health")
def health():
    return {
        "ok": True,
        "time": _iso_now(),
        "bus_vehicles": len(store["bus_vehicles"]),
        "train_vehicles": len(store["train_vehicles"]),
        "alerts": len(store["alerts"]),
        "updated_at": store["updated_at"],
        "errors": store["errors"],
    }


@app.get("/api/vehicles")
def vehicles(mode: str = "all", rt: str = None):
    items = []
    if mode in ("all", "bus"):
        items += store["bus_vehicles"]
    if mode in ("all", "train"):
        items += store["train_vehicles"]
    if rt:
        rts = {r.strip() for r in rt.split(",")}
        items = [v for v in items if str(v.get("route")) in rts]
    return {"count": len(items), "vehicles": items, "time": _iso_now()}


@app.get("/api/fleet")
def fleet():
    buses, trains = store["bus_vehicles"], store["train_vehicles"]
    def breakdown(items):
        d = {}
        for v in items:
            d[str(v.get("route"))] = d.get(str(v.get("route")), 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))
    return {
        "bus": {"count": len(buses), "by_route": breakdown(buses)},
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
                "countdown": p.get("prdctdn"),  # "DUE"/"DLY"/minutes
                "predicted_at": p.get("prdtm"),
                "distance_ft": p.get("dstp"),
                "delayed": p.get("dly") == "1",
            })
        return {"arrivals": out, "time": _iso_now()}
    return _cached(f"bus:{stpid}:{rt}:{top}", _fetch)


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
    return _cached(f"train:{mapid}:{stpid}:{rt}:{max}", _fetch)


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
        if c in ("DUE", "Due"): return 0
        try: return int(c)
        except (TypeError, ValueError): return 999
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
    items = store["alerts"]
    if rt:
        rts = {r.strip().lower() for r in rt.split(",")}
        def _match(a):
            blob = json_blob(a).lower()
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


def json_blob(a):
    import json as _j
    try:
        return _j.dumps(a, default=str)
    except Exception:
        return str(a)


@app.get("/api/nearby")
def nearby(lat: float = Query(...), lon: float = Query(...),
           radius_m: int = 800, limit: int = 12):
    return {"stops": gtfs.nearby(lat, lon, radius_m, limit), "time": _iso_now()}


@app.get("/api/routes")
def routes(mode: str = "all"):
    out = []
    for rid, r in gtfs.routes.items():
        rtype = r.get("route_type")
        m = "train" if rtype == "1" else "bus" if rtype == "3" else "other"
        if mode != "all" and m != mode:
            continue
        out.append({
            "route_id": rid,
            "short_name": r.get("route_short_name"),
            "long_name": r.get("route_long_name"),
            "mode": m,
            "color": r.get("route_color"),
        })
    return {"count": len(out), "routes": sorted(out, key=lambda x: x["short_name"] or "")}


@app.get("/api/shape")
def shape(mode: str = "bus", rt: str = Query(None), pid: str = None):
    """Polyline [[lat, lon], ...] for a bus pattern or train route."""
    if mode == "bus":
        if not rt and not pid:
            return {"error": "rt or pid required"}, 400
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
    # train: GTFS shape by route_id
    rid = rt
    if not rid or rid not in gtfs.routes:
        # try short-name lookup (e.g. "Red")
        for _rid, r in gtfs.routes.items():
            if r.get("route_type") == "1" and (r.get("route_short_name") == rt or _rid == rt):
                rid = _rid
                break
    return {"coords": gtfs.shape_for_route(rid) if rid else [], "route": rt}


@app.get("/")
def index():
    return {"service": "chicago-transit-connector", "docs": "/docs", "health": "/health"}
