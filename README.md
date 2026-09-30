# Chicago Transit Connector for Muse

Ask Muse about Chicago transit in plain words — live arrivals, vehicles
gliding on a map, fleet counts, service alerts, nearby stops — answered with
animated HTML widgets. Built the same way as the TTC connector: API backend
+ skill + CLI + widget templates.

## Architecture

```
CTA Train Tracker API ─┐
CTA Bus Tracker API  ──┼─► backend/app.py (polls every 30s, REST + cache)
CTA Alerts API       ─┘         │
CTA GTFS static ────────────────┘
        │
        ├── cli/cta.py          ← skill calls this (auth: X-API-Key)
        ├── skill/SKILL.md      ← Muse reads this
        └── widgets/*.html     ← filled via widget.create, JS re-fetches API
```

## Setup

1. **Get free CTA keys**
   - Train: https://www.transitchicago.com/developers/traintrackerapply/
   - Bus: sign up at https://www.ctabustracker.com → My Account → Developer API

2. **Configure**
   ```bash
   cp .env.example .env   # fill CTA_BUS_API_KEY, CTA_TRAIN_API_KEY
   # CONNECTOR_API_KEY = the "custom.cta" key widgets/CLI send as X-API-Key
   ```

3. **Run**
   ```bash
   cd backend && pip install -r requirements.txt
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```

4. **Deploy** (Railway): push this repo to GitHub, create a Railway project
   from the repo (it picks up the `Dockerfile` automatically). Set env vars:
   `CTA_TRAIN_API_KEY` (required), `CTA_BUS_API_KEY` (optional — add later),
   `CONNECTOR_API_KEY` (your "custom.cta" key for the CLI/widgets).
   Note the public URL, e.g. `https://<app>.up.railway.app`.
   Point a Cloudflare DNS record at it if you want a custom domain.

5. **Custom Connector in Muse**: register a connector with
   - base URL = your Railway URL
   - API key = `CONNECTOR_API_KEY`
   
   Install the skill (`skill/SKILL.md`) so Muse knows the CLI, endpoints,
   and which widget template to use per question.

## Try it

```bash
export CTA_API_BASE=https://<your-app>.up.railway.app
export CONNECTOR_API_KEY=<key>
./cli/cta.py --pretty fleet
./cli/cta.py --pretty train-arrivals --mapid 40380 --rt Red
./cli/cta.py --pretty bus-arrivals --stpid 8417 --rt 22
```

## Notes

- Bus predictions cover ~30 min out; train ~20 min. Off-route buses vanish
  from the feed — that's CTA, not a bug.
- No crowding/occupancy data exists in CTA feeds.
- Rate limits: 100k/day (bus), 50k/day (train). The poller uses ~6k/day.
- Data: "Powered by CTA data" (optional credit per CTA's terms).
