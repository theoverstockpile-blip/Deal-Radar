"""One scan = fetch new emails -> parse -> store deals -> score against the last N days -> alert."""
from __future__ import annotations

from datetime import datetime, timedelta

from .geo import geocode_listing
from .parser import parse_text
from .scoring import analyze
from .store import Store, utcnow

MAX_INDIVIDUAL_PUSHES = 5


def _money(x):
    return f"${x:,.0f}"


def hot_alert_text(d: dict, price_dropped_from: float | None = None) -> tuple[str, str]:
    title = ("Price drop: " if price_dropped_from else "Low cost per sq ft: ") + d["street"]
    # Describe the actual comps used, not the nominal radius setting -- the search may have had
    # to widen past the appraisal-ideal ring to find enough, so say how far it actually reached.
    farthest = d["comps"][-1]["dist_mi"] if d.get("comps") else None
    comps_desc = (f"{d['n_comps']} deals within {farthest:g} mi" if d["n_in_radius"] == d["n_comps"]
                  else f"{d['n_comps']} deals, {d['n_in_radius']} within the ideal radius, out to {farthest:g} mi")
    body = (f"{_money(d['ppsf'])}/sq ft vs {_money(d['median_ppsf'])} area median "
            f"({d['discount_pct']:.0f}% below, {comps_desc}). Asking {_money(d['price'])}"
            + (f", was {_money(price_dropped_from)}." if price_dropped_from else "."))
    return title, body


def run_scan(store: Store, source, notifier=None, trigger: str = "manual", now: datetime | None = None) -> dict:
    now = now or utcnow()
    s = store.get_settings()
    result = dict(trigger=trigger, emails_found=0, emails_new=0, deals_new=0, price_changes=0,
                  hot=0, alerts_created=0, pushed=0, message="")

    msgs = source.fetch(since=now - timedelta(days=s["lookback_days"]))
    msgs.sort(key=lambda m: m.date.timestamp() if m.date else 0)  # oldest first: price drops must read forward in time
    result["emails_found"] = len(msgs)
    changed: dict[str, str] = {}
    for m in msgs:
        if store.email_seen(m.msg_id):
            continue
        parsed = parse_text(m.text, m.html)
        if not parsed["listings"]:
            store.add_email(m.msg_id, m.subject, m.sender, str(m.date), 0, now)  # remember, don't re-parse
            continue
        result["emails_new"] += 1
        for l in parsed["listings"]:
            # Geocoded once here, at ingestion, and cached (store.kv) keyed by address -- not
            # re-looked-up on every scan. Offline (ZIP centroid) unless s["geocode_online"] is on.
            l["geo"] = geocode_listing(l, s["geocode_online"], provider=s.get("geocode_provider", "census"),
                                       api_key=s.get("geocode_api_key") or None,
                                       cache_get=lambda k: store.kv_get(f"geo:{k}"),
                                       cache_set=lambda k, v: store.kv_set(f"geo:{k}", v))
            status = store.upsert_deal(l, m.msg_id, now)
            if status == "new":
                result["deals_new"] += 1
                changed[l["id"]] = status
            elif status in ("price_drop", "price_up"):
                result["price_changes"] += 1
                changed[l["id"]] = status
        store.add_email(m.msg_id, m.subject, m.sender, str(m.date), len(parsed["listings"]), now)

    pool = store.deals_in_window(s["lookback_days"], now)
    scored = analyze(pool, s)
    hot = [d for d in scored if d["hot"]]
    cheap = [d for d in scored if d["tier"] == "cheap_outside_box"] if not s["alert_only_buy_box"] else []
    result["hot"] = len(hot)

    made: list[tuple[int, dict, str, str]] = []
    for d in hot + cheap:
        prev = d.get("price_prev") if changed.get(d["id"]) == "price_drop" else None
        title, body = hot_alert_text(d, prev)
        aid = store.add_alert("hot" if d["hot"] else "cheap", title, body, now, deal_id=d["id"], price_at=d["price"],
                              data={"ppsf": d["ppsf"], "median_ppsf": d["median_ppsf"], "discount_pct": d["discount_pct"],
                                    "n_comps": d["n_comps"], "price": d["price"]})
        if aid:
            made.append((aid, d, title, body))

    digest_id = None
    if result["emails_new"]:
        best = sorted([d for d in scored if d["discount_pct"] is not None], key=lambda d: -d["discount_pct"])[:3]
        digest_body = (f"{result['deals_new']} new deal(s) today, {len(hot)} hot. "
                + ("Best vs area: " + "; ".join(f"{d['street']} ({d['discount_pct']:.0f}% below, {_money(d['ppsf'])}/sq ft)" for d in best)
                   if best else "Not enough nearby deals to compare yet."))
        digest_id = store.add_alert("digest", "Daily wholesale digest", digest_body, now,
                                    data={"deal_ids": [d["id"] for d in best], "hot_ids": [d["id"] for d in hot],
                                          "new": result["deals_new"]})

    result["alerts_created"] = len(made) + (1 if digest_id else 0)

    if notifier and store.push_subs():
        if len(made) <= MAX_INDIVIDUAL_PUSHES:
            for aid, d, title, body in made:
                r = notifier.send_all({"title": title, "body": body, "alert_id": aid, "deal_id": d["id"],
                                       "url": f"/#/deal/{d['id']}", "tag": f"deal-{d['id']}"})
                result["pushed"] += r["sent"]
        else:  # a flood becomes one grouped push
            r = notifier.send_all({"title": f"{len(made)} hot deals", "body": "Open Deal Radar to review them.",
                                   "url": "/#/alerts", "tag": "hot-many"})
            result["pushed"] += r["sent"]
        if digest_id and not made:  # one signal per day: hot deals OR the digest, not both
            r = notifier.send_all({"title": "Daily wholesale digest", "body": digest_body, "url": "/#/alerts", "tag": "digest"})
            result["pushed"] += r["sent"]

    result["message"] = (f"{result['emails_new']} new email(s), {result['deals_new']} new deal(s), {len(hot)} hot"
                         if result["emails_new"] else "No new wholesale email found")
    store.add_scan(now, trigger, result["emails_new"], result["deals_new"], len(hot), result["alerts_created"], result["message"])
    return result
