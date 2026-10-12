"""Cost-per-square-foot scoring, appraiser-style comp selection and buy-box matching.

Definitions (all editable in Settings):
  ppsf         price / building sq ft (land gets a lot-based number, never compared)
  comps        the `min_comps` closest other deals of the same class (house / condo /
               manufactured) that also have a ppsf, from no older than `lookback_days`.
               Appraisers generally want comps within `radius_mi` (0.5mi); when fewer
               than `min_comps` fall inside that ring, the pool widens outward (closest
               first, capped at `max_radius_mi`) until enough are found. Every comp
               carries `dist_mi` and `in_radius` so the UI can show both groups and how
               far each one actually is.
  median_ppsf  median ppsf of those comps
  discount_pct how far below the median this deal sits (positive = cheaper)
  low_ppsf     enough comps AND discount_pct >= low_ppsf_pct
  in_buy_box   passes the buy-box filters
  hot          in_buy_box AND low_ppsf   -> instant alert; everything else -> digest

NOTE on radius_mi: the offline ZIP-centroid geocoder in geo.py is only accurate to about
1-3 miles, so "within 0.5mi" offline mostly collapses to "same ZIP." Turn on the
`geocode_online` setting (street-level, via the free US Census geocoder, cached per
address -- see `geo.geocode_listing`) once the app is hosted somewhere with internet
access, and radius_mi actually means what it says.
"""
from __future__ import annotations

import copy
import statistics

from .geo import haversine_mi, locate

DEFAULT_SETTINGS: dict = {
    # scan schedule
    "scan_time": "11:30",          # local HH:MM; the source blast lands ~11:15 Arizona time
    "scan_window_min": 5,          # scan starts at scan_time, never early, at most this many minutes late
    "catch_up": True,              # if the window was missed (app asleep), scan once when it wakes
    "timezone": "America/Phoenix",
    # comparison
    "radius_mi": 0.5,              # appraiser-standard "ideal" ring; comps inside this are flagged in_radius
    "max_radius_mi": 25,           # hard cap on how far the search will widen to reach min_comps
    "min_comps": 5,                 # go closest-to-furthest (within max_radius_mi) until we have at least this many
    "low_ppsf_pct": 15,            # % below area median that counts as "low"
    "lookback_days": 150,          # ~5 months (3-6mo target); how long a comp stays usable
    "geocode_online": False,       # street-level geocoding (needs internet); see geo.geocode_listing
    "geocode_provider": "census",  # "census" (free, no key) or "google" (paid, needs geocode_api_key)
    "geocode_api_key": "",         # Google Maps Geocoding API key; only used when geocode_provider == "google"
    "geocode_setup_progress": [],  # ids of completed steps in the Settings "add a paid geocoder" checklist
    # email connection (Settings > "Connect your email" -- IMAP with an app-specific password)
    "email_address": "",           # the inbox to scan, e.g. Mason's own Gmail
    "email_app_password": "",      # an app-specific password, NOT the real account password
    "imap_host": "",               # usually left blank -- guessed from the email's domain (sources.IMAP_HOSTS)
    "imap_folder": "INBOX",
    "imap_setup_progress": [],     # ids of completed steps in the Settings "connect your email" checklist
    # buy-box (starter defaults for a fix-and-flip; Mason's exact box is still being pinned down)
    "buy_box": {
        "types": ["sfr"],
        "max_price": 400000,
        "min_beds": 3,
        "min_sqft": 900,
        "allow_occupied": True,
    },
    "alert_only_buy_box": True,
    "contact_phone_override": "",
}

CLASS_OF = {"sfr": "house", "multi": "house", "condo": "condo", "manufactured": "manufactured", "land": None}


def merged_settings(overrides: dict | None) -> dict:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    return s


def ppsf_of(l: dict) -> float | None:
    if l.get("type") == "land" or not l.get("price") or not l.get("sqft"):
        return None
    return l["price"] / l["sqft"]


def buy_box_check(l: dict, box: dict) -> list[str]:
    """Return the list of reasons a deal is OUTSIDE the box ([] means it fits)."""
    why = []
    if l.get("type") not in box.get("types", []):
        why.append(f"type is {l.get('type')}")
    if l.get("price") and l["price"] > box.get("max_price", 1e12):
        why.append(f"price above ${box['max_price']:,.0f}")
    if l.get("type") != "land":
        if l.get("beds") is not None and l["beds"] < box.get("min_beds", 0):
            why.append(f"only {l['beds']:g} beds")
        if l.get("sqft") is not None and l["sqft"] < box.get("min_sqft", 0):
            why.append(f"only {l['sqft']:,.0f} sq ft")
    if not box.get("allow_occupied", True) and l.get("occupancy") in ("occupied",):
        why.append("occupied")
    return why


def analyze(listings: list[dict], settings: dict | None = None) -> list[dict]:
    """Return NEW dicts (inputs untouched) enriched with ppsf / comps / verdicts."""
    s = merged_settings(settings)
    out = [dict(l) for l in listings]
    for l in out:
        l["geo"] = l.get("geo") or locate(l)
        l["ppsf"] = ppsf_of(l)
        l["ppsf_lot"] = (l["price"] / l["lot_sqft"]) if l.get("type") == "land" and l.get("lot_sqft") and l.get("price") else None

    pool = [l for l in out if l["ppsf"] and l["geo"]["lat"] is not None and CLASS_OF.get(l["type"])]
    for l in out:
        l["comparable"] = bool(l["ppsf"] and l["geo"]["lat"] is not None and CLASS_OF.get(l["type"]))
        l.update(comps=[], n_comps=0, n_in_radius=0, median_ppsf=None, discount_pct=None, rank=None, pool_size=None,
                 low_ppsf=False, all_comps=[])
        if not l["comparable"]:
            l["compare_note"] = (
                "Land is priced per lot sq ft and not compared" if l["type"] == "land"
                else "No square footage in the email, so no cost per sq ft" if not l.get("sqft")
                else "Location unknown, so not compared"
            )
        else:
            here = (l["geo"]["lat"], l["geo"]["lon"])
            candidates = []
            for o in pool:
                if o["id"] == l["id"] or CLASS_OF[o["type"]] != CLASS_OF[l["type"]]:
                    continue
                d = round(haversine_mi(here, (o["geo"]["lat"], o["geo"]["lon"])), 1)  # 0.1 mi; same value the UI shows
                if d <= s["max_radius_mi"]:
                    candidates.append({"id": o["id"], "address": o["address"], "ppsf": round(o["ppsf"], 2),
                                        "price": o["price"], "sqft": o["sqft"], "dist_mi": d,
                                        "in_radius": d <= s["radius_mi"]})
            candidates.sort(key=lambda c: c["dist_mi"])  # closest first; in-radius comps always sort before the rest
            l["all_comps"] = candidates  # full pool out to max_radius_mi -- not part of the public API (see CARD_FIELDS),
            # but build_demo.py bakes this so the demo's radius/min_comps sliders have real data to recompute against.
            n_in_radius = sum(1 for c in candidates if c["in_radius"])
            # Prefer the appraiser-ideal ring; only reach past it, closest-first, if that ring is thin.
            comps = [c for c in candidates if c["in_radius"]] if n_in_radius >= s["min_comps"] else candidates[:s["min_comps"]]
            l["comps"], l["n_comps"] = comps, len(comps)
            l["n_in_radius"] = sum(1 for c in comps if c["in_radius"])
            if len(comps) >= s["min_comps"]:
                med = statistics.median(c["ppsf"] for c in comps)
                l["median_ppsf"] = round(med, 2)
                l["discount_pct"] = round((med - l["ppsf"]) / med * 100, 1)
                l["rank"] = 1 + sum(1 for c in comps if c["ppsf"] < l["ppsf"])  # 1 = cheapest
                l["pool_size"] = len(comps) + 1
                l["low_ppsf"] = l["discount_pct"] >= s["low_ppsf_pct"]
                l["compare_note"] = (
                    None if l["n_in_radius"] == len(comps)
                    else f"Only {l['n_in_radius']} within {s['radius_mi']:g}mi; widened to {comps[-1]['dist_mi']:g}mi "
                         f"to reach {s['min_comps']} comps"
                )
            else:
                l["compare_note"] = f"Only {len(comps)} nearby deal(s) to compare (need {s['min_comps']})"
        l["box_misses"] = buy_box_check(l, s["buy_box"])
        l["in_buy_box"] = not l["box_misses"]
        l["hot"] = bool(l["in_buy_box"] and l["low_ppsf"])
        l["tier"] = "hot" if l["hot"] else ("cheap_outside_box" if l["low_ppsf"] else "other")
        # value_tier: a plain 3-way read on cost per sq ft vs. the area, independent of the buy-box,
        # since the buy-box is loose/optional -- this is the "green/yellow/red" signal shown on every card.
        dp = l["discount_pct"]
        l["value_tier"] = (
            "unknown" if dp is None
            else "green" if dp >= s["low_ppsf_pct"]   # well below the area median
            else "yellow" if dp >= 0                   # at or a little below median
            else "red"                                  # above the area median
        )
    return out
