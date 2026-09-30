# chicago-transit skill

Live Chicago Transit (CTA) data: L trains + buses. Arrivals, live vehicle
positions on a map, fleet counts, service alerts, nearby stops, and
head-to-head route comparisons — answered with animated HTML widgets.

## Data sources

- **CTA Train Tracker API** — L arrivals (`ttarrivals`), train positions
  (`ttpositions`). Predictions ~20 min out, accuracy <±1 min.
- **CTA Bus Tracker API (BusTime v2)** — bus predictions, vehicle GPS
  (~30s refresh), routes, stops, patterns. Predictions ~30 min out.
- **CTA Customer Alerts API** — service alerts, no key needed, polled 2 min.
- **CTA GTFS static** — stops, stations, route shapes for maps.

No crowding/occupancy data exists in any CTA feed — never claim it.

## CLI

`cli/cta.py` hits the connector API. Auth: `CONNECTOR_API_KEY` env var sent
as `X-API-Key` (the Custom Connector key). Base URL: `CTA_API_BASE`.

```bash
export CTA_API_BASE=https://<railway-app>.up.railway.app
export CONNECTOR_API_KEY=<custom.cta key>

cta.py bus-arrivals --stpid 8417 --rt 22 [--top 5]
cta.py train-arrivals --mapid 40380 [--rt Red] [--max 8]   # 40380 = Clark/Lake
cta.py vehicles --mode all [--rt Red,22]                  # live lat/lon/heading
cta.py fleet                                              # counts by mode + route
cta.py alerts [--rt 22]
cta.py nearby --lat 41.8781 --lon -87.6298 [--radius-m 800]
cta.py compare --mode bus --stpid 8417 --rt1 22 --rt2 36
cta.py routes [--mode bus|train]
cta.py shape --mode bus --rt 22 | --mode train --rt Red
```

Stop IDs: bus `stpid` is the BusTime stop code (find via `nearby`, which
returns `stop_code`). Train `mapid` is the 5-digit parent station (4xxxx);
`stpid` is the platform (3xxxx).

## REST API (for widgets / direct use)

All `GET`, JSON. Same `X-API-Key` auth.

| Endpoint | Params |
|---|---|
| `/api/bus/arrivals` | `stpid*`, `rt`, `top` |
| `/api/train/arrivals` | `mapid` or `stpid`, `rt`, `max` |
| `/api/vehicles` | `mode=all\|bus\|train`, `rt` (csv) |
| `/api/fleet` | — |
| `/api/alerts` | `rt` (csv) |
| `/api/nearby` | `lat*`, `lon*`, `radius_m`, `limit` |
| `/api/compare` | `mode`, `stpid`/`mapid`, `rt1*`, `rt2*`, `top` |
| `/api/routes` | `mode` |
| `/api/shape` | `mode`, `rt*`, `pid` (bus pattern) |
| `/health` | — (no auth) |

Vehicle object: `{id, mode, route, lat, lon, heading, speed_mph, dest}`.
Bus `speed_mph` is computed between 30s polls; trains report `next_station`
and `delayed`.

## Widgets

Templates live in `widgets/`. When the user asks a transit question:

1. Run the matching `cta.py` command to get live data.
2. Fill the template's placeholders and return via `widget.create`:
   - `__API_BASE__` → the Railway URL (so the widget's JS can re-fetch
     directly from the phone every 20–30s and stay live).
   - Other `__PARAMS__` per template header comment.

| User asks | CLI | Template |
|---|---|---|
| next bus/train here | `bus-arrivals` / `train-arrivals` | `arrivals.html` |
| show it moving live | `vehicles --rt …` + `shape` | `livemap.html` |
| how many vehicles now | `fleet` | `fleet.html` |
| any alerts | `alerts` | `alerts.html` |
| stops near X | `nearby` | `nearby.html` |
| which is faster, A or B | `compare` | `arrivals.html` (two sections) |

Answer in plain words first ("next 22 to downtown is DUE, then 7 min"),
then attach the widget. Never invent arrivals — if the API returns none,
say so (predictions only cover ~20–30 min out, and buses off-route vanish).
