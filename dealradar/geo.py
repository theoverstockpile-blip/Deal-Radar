"""Offline location lookup for the prototype.

The free Census geocoder is the intended production source (see `geocode_online`),
but it was not reachable from the build sandbox, so this ships with approximate
ZIP-code centroids for the Arizona ZIPs seen so far. Accuracy is roughly 1-3 miles,
which is fine for a 10-mile "nearby deals" comparison but NOT for street-level work.

Unknown ZIPs fall back to a city centroid; if both are missing the deal simply has
no location and is not compared (the app says so).
"""
from __future__ import annotations

import math

# ZIP -> (lat, lon), approximate centroids
ZIP_CENTROIDS: dict[str, tuple[float, float]] = {
    "85007": (33.4415, -112.0920), "85008": (33.4595, -111.9960), "85009": (33.4770, -112.1180),
    "85015": (33.5090, -112.1000), "85016": (33.5090, -112.0300), "85019": (33.5200, -112.1500),
    "85020": (33.5650, -112.0550), "85022": (33.6350, -112.0450), "85023": (33.6350, -112.1050),
    "85032": (33.6260, -111.9900), "85034": (33.4350, -111.9900), "85041": (33.3700, -112.1150),
    "85042": (33.3830, -112.0600), "85045": (33.3040, -112.1300), "85051": (33.5600, -112.1700),
    "85131": (32.7500, -111.5500), "85138": (33.0600, -112.0500), "85143": (33.1900, -111.5300),
    "85206": (33.3900, -111.7300), "85212": (33.3550, -111.6400), "85225": (33.3100, -111.8250),
    "85253": (33.5450, -111.9600), "85258": (33.5600, -111.8800), "85260": (33.6060, -111.8600),
    "85281": (33.4300, -111.9300), "85282": (33.3900, -111.9350), "85295": (33.3300, -111.7600),
    "85326": (33.3700, -112.5800), "85340": (33.4900, -112.3600), "85354": (33.4800, -112.9400),
    "85361": (33.7900, -112.5300), "85363": (33.6000, -112.3000), "85379": (33.6300, -112.3800),
    "85382": (33.6500, -112.2400), "85383": (33.7300, -112.2500), "85621": (31.3400, -110.9400),
    "85631": (32.6000, -110.6300), "85641": (32.0400, -110.7000), "85653": (32.4300, -111.2200),
    "85710": (32.2200, -110.8300), "85716": (32.2350, -110.9350), "85735": (32.1600, -111.1500),
    "86326": (34.7400, -112.0100), "86401": (35.1900, -114.0500),
}

# City -> (lat, lon); used as fallback and to catch ZIP/city typos in the source email
CITY_CENTROIDS: dict[str, tuple[float, float]] = {
    "phoenix": (33.4484, -112.0740), "tempe": (33.4255, -111.9400), "mesa": (33.4152, -111.8315),
    "chandler": (33.3062, -111.8413), "gilbert": (33.3528, -111.7890), "scottsdale": (33.4942, -111.9261),
    "paradise valley": (33.5310, -111.9426), "peoria": (33.5806, -112.2374), "surprise": (33.6292, -112.3679),
    "youngtown": (33.5800, -112.3010), "litchfield park": (33.4934, -112.3577), "buckeye": (33.3703, -112.5838),
    "tonopah": (33.4870, -112.9360), "wittmann": (33.7900, -112.5300), "maricopa": (33.0581, -112.0476),
    "san tan valley": (33.1920, -111.5340), "eloy": (32.7559, -111.5548), "marana": (32.4367, -111.2258),
    "tucson": (32.2226, -110.9747), "vail": (32.0331, -110.7115), "san manuel": (32.6002, -110.6362),
    "nogales": (31.3404, -110.9343), "cottonwood": (34.7392, -112.0099), "kingman": (35.1894, -114.0530),
}

MISMATCH_MILES = 25  # ZIP and city this far apart => trust the city, flag low confidence


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def locate(listing: dict) -> dict:
    """Return {'lat','lon','confidence','source', 'warning'?} for a parsed listing."""
    z = ZIP_CENTROIDS.get(listing.get("zip") or "")
    c = CITY_CENTROIDS.get((listing.get("city") or "").lower())
    if z and c and haversine_mi(z, c) > MISMATCH_MILES:
        return {"lat": c[0], "lon": c[1], "confidence": "low", "source": "city",
                "warning": f"ZIP {listing['zip']} does not match {listing['city']}; using city center"}
    if z:
        return {"lat": z[0], "lon": z[1], "confidence": "approx", "source": "zip"}
    if c:
        return {"lat": c[0], "lon": c[1], "confidence": "low", "source": "city"}
    return {"lat": None, "lon": None, "confidence": "none", "source": None}


def geocode_online(address: str):  # pragma: no cover - needs network
    """US Census one-line geocoder (free, no key). Returns (lat, lon) or None.

    Street-level and accurate to a few dozen feet in most of the US -- this is what makes a
    sub-mile `radius_mi` meaningful. See `geocode_listing` below for the cached, fallback-safe
    wrapper that's actually wired into the scan pipeline.
    """
    import json
    import urllib.parse
    import urllib.request

    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?"
           + urllib.parse.urlencode({"address": address, "benchmark": "Public_AR_Current", "format": "json"}))
    with urllib.request.urlopen(url, timeout=10) as r:
        matches = json.load(r)["result"]["addressMatches"]
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return c["y"], c["x"]


def geocode_online_google(address: str, api_key: str):  # pragma: no cover - needs network + paid key
    """Google Maps Geocoding API. Returns (lat, lon) or None. Needs a billed API key (see Settings)."""
    import json
    import urllib.parse
    import urllib.request

    url = ("https://maps.googleapis.com/maps/api/geocode/json?"
           + urllib.parse.urlencode({"address": address, "key": api_key}))
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.load(r)
    if data.get("status") != "OK" or not data.get("results"):
        return None
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def address_key(listing: dict) -> str:
    """Cache key for a listing's address -- stable regardless of which deal record it's on."""
    parts = (listing.get("street"), listing.get("city"), listing.get("state"), listing.get("zip"))
    return "|".join((p or "").strip().lower() for p in parts)


def geocode_listing(listing: dict, online: bool, provider: str = "census", api_key: str | None = None,
                     cache_get=None, cache_set=None) -> dict:
    """The production geocoder: cached, street-level when `online` is on, offline-safe otherwise.

    `provider` is "census" (free, no key, default) or "google" (paid, needs `api_key`; see the
    "Location accuracy" section of Settings for how to get one). `cache_get(key)` / `cache_set(key,
    value)` persist results (success or fallback) across scans so every deal is only geocoded once,
    not re-hit on every run. Falls back to the offline ZIP/city centroid in `locate()` whenever
    online geocoding is off, the provider isn't usable (e.g. "google" with no key set), fails, or
    times out, so a flaky connection or a missing key never breaks a scan -- it just quietly loses
    precision for that listing.
    """
    key = address_key(listing)
    if cache_get is not None:
        cached = cache_get(key)
        if cached:
            return cached

    geo = None
    if online and key.strip("|"):
        line = ", ".join(p for p in (listing.get("street"), listing.get("city"), listing.get("state"),
                                      listing.get("zip")) if p)
        hit = None
        try:
            if provider == "google" and api_key:
                hit = geocode_online_google(line, api_key)
            elif provider != "google":
                hit = geocode_online(line)
        except Exception:  # noqa: BLE001 - any network/parse failure just falls back below
            hit = None
        if hit:
            geo = {"lat": hit[0], "lon": hit[1], "confidence": "high", "source": provider}

    if geo is None:
        geo = locate(listing)

    if cache_set is not None:
        cache_set(key, geo)
    return geo
