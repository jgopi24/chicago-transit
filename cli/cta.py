#!/usr/bin/env python3
"""cta.py — CLI for the Chicago Transit connector.

Calls the connector API (auth via X-API-Key, the "custom.cta" key) and prints JSON.
Muse's chicago-transit skill invokes this; humans can use --pretty.

    export CTA_API_BASE=https://cta.example.com
    export CONNECTOR_API_KEY=...

    ./cta.py bus-arrivals --stpid 8417 --rt 22
    ./cta.py train-arrivals --mapid 40380 --rt Red
    ./cta.py vehicles --mode all --rt Red,22
    ./cta.py fleet
    ./cta.py alerts --rt 22
    ./cta.py nearby --lat 41.8781 --lon -87.6298
    ./cta.py compare --mode bus --stpid 8417 --rt1 22 --rt2 36
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def call(path, params=None, pretty=False):
    base = os.environ.get("CTA_API_BASE", "http://localhost:8000").rstrip("/")
    key = os.environ.get("CONNECTOR_API_KEY", "")
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{base}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")
    except Exception as e:
        sys.exit(f"request failed: {e}")
    if pretty:
        print(json.dumps(data, indent=2))
    else:
        print(json.dumps(data))
    return data


def main():
    ap = argparse.ArgumentParser(prog="cta.py", description="Chicago Transit connector CLI")
    ap.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("bus-arrivals", help="bus predictions for a stop")
    p.add_argument("--stpid", required=True, help="BusTime stop id")
    p.add_argument("--rt", help="route, e.g. 22")
    p.add_argument("--top", default=5)

    p = sub.add_parser("train-arrivals", help="L predictions for a station")
    p.add_argument("--mapid", help="station id (4xxxx)")
    p.add_argument("--stpid", help="platform id (3xxxx)")
    p.add_argument("--rt", help="e.g. Red")
    p.add_argument("--max", default=8)

    p = sub.add_parser("vehicles", help="live vehicle positions")
    p.add_argument("--mode", default="all", choices=["all", "bus", "train"])
    p.add_argument("--rt", help="comma-separated routes")

    sub.add_parser("fleet", help="live fleet counts")

    p = sub.add_parser("alerts", help="service alerts")
    p.add_argument("--rt", help="comma-separated routes")

    p = sub.add_parser("nearby", help="stops near a coordinate")
    p.add_argument("--lat", required=True)
    p.add_argument("--lon", required=True)
    p.add_argument("--radius-m", default=800)
    p.add_argument("--limit", default=12)

    p = sub.add_parser("compare", help="head-to-head of two routes")
    p.add_argument("--mode", default="bus", choices=["bus", "train"])
    p.add_argument("--stpid", help="bus stop id")
    p.add_argument("--mapid", help="train station id")
    p.add_argument("--rt1", required=True)
    p.add_argument("--rt2", required=True)
    p.add_argument("--top", default=3)

    sub.add_parser("routes", help="list routes").add_argument(
        "--mode", default="all", choices=["all", "bus", "train"])

    p = sub.add_parser("shape", help="route polyline")
    p.add_argument("--mode", default="bus", choices=["bus", "train"])
    p.add_argument("--rt", required=True)
    p.add_argument("--pid", help="bus pattern id")

    args = ap.parse_args()
    kw = vars(args)
    cmd, pretty = kw.pop("cmd"), kw.pop("pretty")
    paths = {
        "bus-arrivals": ("/api/bus/arrivals", ["stpid", "rt", "top"]),
        "train-arrivals": ("/api/train/arrivals", ["mapid", "stpid", "rt", "max"]),
        "vehicles": ("/api/vehicles", ["mode", "rt"]),
        "fleet": ("/api/fleet", []),
        "alerts": ("/api/alerts", ["rt"]),
        "nearby": ("/api/nearby", ["lat", "lon", "radius_m", "limit"]),
        "compare": ("/api/compare", ["mode", "stpid", "mapid", "rt1", "rt2", "top"]),
        "routes": ("/api/routes", ["mode"]),
        "shape": ("/api/shape", ["mode", "rt", "pid"]),
    }
    path, fields = paths[cmd]
    call(path, {f: kw.get(f) for f in fields}, pretty=pretty)


if __name__ == "__main__":
    main()
