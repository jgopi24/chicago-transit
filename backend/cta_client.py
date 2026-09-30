"""CTA API client: Bus Tracker (BusTime v2) + Train Tracker + Customer Alerts."""
import os
import requests


class CTAError(Exception):
    pass


class CTAClient:
    BUS_BASE = "http://www.ctabustracker.com/bustime/api/v2"
    TRAIN_BASE = "http://lapi.transitchicago.com/api/1.0"
    ALERTS_URL = "http://www.transitchicago.com/api/1.0/alerts.aspx"

    def __init__(self, bus_key=None, train_key=None):
        self.bus_key = bus_key or os.environ.get("CTA_BUS_API_KEY")
        self.train_key = train_key or os.environ.get("CTA_TRAIN_API_KEY")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "chicago-transit-connector/1.0"})

    # ---- low level ----
    def _bus(self, endpoint, **params):
        if not self.bus_key:
            raise CTAError("CTA_BUS_API_KEY not set")
        params.update(key=self.bus_key, format="json")
        r = self.s.get(f"{self.BUS_BASE}/{endpoint}", params=params, timeout=20)
        r.raise_for_status()
        body = r.json().get("bustime-response", {})
        if "error" in body:
            err = body["error"][0] if isinstance(body["error"], list) else body["error"]
            raise CTAError(f"BusTime {endpoint}: {err.get('msg', err)}")
        return body

    def _train(self, endpoint, **params):
        if not self.train_key:
            raise CTAError("CTA_TRAIN_API_KEY not set")
        params.update(key=self.train_key, outputType="JSON")
        r = self.s.get(f"{self.TRAIN_BASE}/{endpoint}", params=params, timeout=20)
        r.raise_for_status()
        body = r.json().get("ctatt", {})
        if str(body.get("errCd", "0")) != "0":
            raise CTAError(f"TrainTracker {endpoint}: {body.get('errNm')}")
        return body

    # ---- bus ----
    def bus_time(self):
        return self._bus("gettime").get("tm")

    def bus_routes(self):
        return self._bus("getroutes").get("routes", [])

    def bus_directions(self, rt):
        return self._bus("getdirections", rt=rt).get("directions", [])

    def bus_stops(self, rt, direction):
        return self._bus("getstops", rt=rt, dir=direction).get("stops", [])

    def bus_pattern(self, rt=None, pid=None):
        kwargs = {}
        if rt:
            kwargs["rt"] = rt
        if pid:
            kwargs["pid"] = pid
        return self._bus("getpatterns", **kwargs).get("ptr", [])

    def bus_vehicles(self, rt=None, vid=None):
        """All active buses. Omit rt/vid for system-wide (if API allows)."""
        kwargs = {}
        if rt:
            kwargs["rt"] = rt
        if vid:
            kwargs["vid"] = vid
        return self._bus("getvehicles", **kwargs).get("vehicle", [])

    def bus_predictions(self, stpid=None, rt=None, vid=None, top=5):
        kwargs = {"top": top}
        if stpid:
            kwargs["stpid"] = stpid
        if rt:
            kwargs["rt"] = rt
        if vid:
            kwargs["vid"] = vid
        return self._bus("getpredictions", **kwargs).get("prd", [])

    def bus_bulletins(self, rt=None):
        kwargs = {}
        if rt:
            kwargs["rt"] = rt
        return self._bus("getservicebulletins", **kwargs).get("sb", [])

    # ---- train ----
    def train_arrivals(self, mapid=None, stpid=None, rt=None, max=8):
        kwargs = {"max": max}
        if mapid:
            kwargs["mapid"] = mapid
        if stpid:
            kwargs["stpid"] = stpid
        if rt:
            kwargs["rt"] = rt
        return self._train("ttarrivals.aspx", **kwargs).get("eta", [])

    def train_positions(self, rt=None):
        kwargs = {}
        if rt:
            kwargs["rt"] = rt
        body = self._train("ttpositions.aspx", **kwargs)
        # response nests trains under route -> train
        out = []
        routes = body.get("route", [])
        if isinstance(routes, dict):
            routes = [routes]
        for route in routes:
            trains = route.get("train", [])
            if isinstance(trains, dict):
                trains = [trains]
            for t in trains:
                t["_rt"] = route.get("@name") or route.get("name")
                out.append(t)
        return out

    def train_follow(self, runnumber):
        body = self._train("ttfollow.aspx", runnumber=runnumber)
        return body.get("eta", [])

    # ---- alerts (no key required) ----
    def alerts(self):
        r = self.s.get(self.ALERTS_URL, params={"outputType": "JSON"}, timeout=25)
        r.raise_for_status()
        body = r.json().get("CTAAlerts", {})
        if str(body.get("ErrorCode", "0")) != "0":
            raise CTAError(f"Alerts: {body.get('ErrorMessage')}")
        return body
