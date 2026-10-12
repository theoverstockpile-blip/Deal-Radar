"""Flask API + static PWA."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, request, send_from_directory

from .push import PushNotifier
from .scanner import run_scan
from .scheduler import Scheduler
from .scoring import analyze
from .sources import ImapError, ImapSource, SettingsBackedSource
from .store import Store, utcnow

STATIC = Path(__file__).resolve().parent.parent / "static"

CARD_FIELDS = ("id", "address", "street", "city", "zip", "price", "arv", "arv_high", "beds", "baths", "sqft",
               "lot_sqft", "year_built", "type", "type_raw", "occupancy", "access", "coe", "emd", "hoa",
               "condition", "notes", "details", "flags", "pictures_url", "photos", "maps_url", "apn", "zoning",
               "ppsf", "ppsf_lot", "median_ppsf", "discount_pct", "n_comps", "n_in_radius", "rank", "pool_size",
               "comps", "low_ppsf", "in_buy_box", "box_misses", "hot", "tier", "value_tier", "compare_note",
               "first_seen", "contacted_at", "price_prev", "sqft_parts", "comparable")


def deal_view(d: dict, contact_override: str = "") -> dict:
    v = {k: d.get(k) for k in CARD_FIELDS}
    v["geo_warning"] = (d.get("geo") or {}).get("warning")
    c = d.get("contact") or {}
    phone = contact_override or c.get("phone")
    v["contact_name"] = c.get("name")
    v["contact_phone"] = phone
    v["tel"] = f"tel:{phone}" if phone else None
    v["sms"] = f"sms:{phone}?&body=" + quote(f"Is {d['address']} still available?") if phone else None
    return v


def create_app(db_path: str = "dealradar.db", source=None, start_scheduler: bool = True,
               notifier: PushNotifier | None = None, now_fn=utcnow, dev: bool | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    store = Store(db_path)
    source = source or SettingsBackedSource(store, os.environ.get("DEALRADAR_INBOX", "inbox"))
    dev = bool(os.environ.get("DEALRADAR_DEV")) if dev is None else dev
    token = os.environ.get("DEALRADAR_TOKEN")

    if notifier is None:
        try:
            notifier = PushNotifier(store, vapid_path=os.environ.get("DEALRADAR_VAPID", "vapid_private.pem"))
        except Exception as e:  # noqa: BLE001
            print(f"[push] disabled: {e}")

    def scan(trigger="manual", now=None):
        return run_scan(store, source, notifier, trigger=trigger, now=now or now_fn())

    sched = Scheduler(store, scan, now_fn=now_fn)
    app.extensions["dealradar"] = dict(store=store, scheduler=sched, notifier=notifier, source=source)
    if start_scheduler:
        sched.start()

    @app.before_request
    def _auth():
        if token and request.path.startswith("/api/") and request.headers.get("X-Token") != token:
            abort(401)

    # ------------------------------------------------------------------ state
    def scored():
        s = store.get_settings()
        return s, analyze(store.deals_in_window(s["lookback_days"], now_fn()), s)

    @app.get("/api/status")
    def status():
        s, deals = scored()
        return jsonify(now=now_fn().isoformat(), settings=s, last_scan=store.last_scan(),
                       next_scan=sched.next_run(), scheduler_error=sched.last_error,
                       unread=sum(1 for a in store.list_alerts(200) if not a["read_at"]),
                       counts=dict(deals=len(deals), hot=sum(d["hot"] for d in deals)),
                       push_key=notifier.public_key if notifier else None,
                       push_subscriptions=len(store.push_subs()), dev=dev)

    @app.get("/api/deals")
    def deals():
        s, ds = scored()
        return jsonify([deal_view(d, s["contact_phone_override"]) for d in ds])

    @app.get("/api/deals/<deal_id>")
    def deal(deal_id):
        s, ds = scored()
        d = next((x for x in ds if x["id"] == deal_id), None)
        if not d:
            abort(404)
        return jsonify(deal_view(d, s["contact_phone_override"]))

    @app.post("/api/deals/<deal_id>/contacted")
    def contacted(deal_id):
        if not store.mark_contacted(deal_id, now_fn()):
            abort(404)
        return jsonify(ok=True)

    # ------------------------------------------------------------------ alerts
    @app.get("/api/alerts")
    def alerts():
        return jsonify(store.list_alerts(limit=int(request.args.get("limit", 100)),
                                         after_id=int(request.args.get("after", 0))))

    @app.post("/api/alerts/read")
    def alerts_read():
        store.mark_alert_read((request.get_json(silent=True) or {}).get("id"), now_fn())
        return jsonify(ok=True)

    # ------------------------------------------------------------------ settings / scan
    @app.get("/api/settings")
    def get_settings():
        return jsonify(store.get_settings())

    @app.put("/api/settings")
    def put_settings():
        patch = request.get_json(force=True) or {}
        try:
            _validate(patch)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(store.set_settings(patch))

    @app.post("/api/scan")
    def manual_scan():
        return jsonify(scan("manual"))

    @app.post("/api/email/test")
    def test_email():
        s = store.get_settings()
        if not (s.get("email_address") and s.get("email_app_password")):
            return jsonify(ok=False, error="Enter an email address and app password first.")
        try:
            ImapSource(s["email_address"], s["email_app_password"], host=s.get("imap_host") or None,
                       folder=s.get("imap_folder") or "INBOX").test_connection()
        except ImapError as e:
            return jsonify(ok=False, error=str(e))
        return jsonify(ok=True)

    # ------------------------------------------------------------------ push
    @app.post("/api/push/subscribe")
    def subscribe():
        sub = request.get_json(force=True)
        if not sub or "endpoint" not in sub:
            return jsonify(error="bad subscription"), 400
        store.add_push_sub(sub)
        return jsonify(ok=True)

    @app.post("/api/push/unsubscribe")
    def unsubscribe():
        store.remove_push_sub((request.get_json(force=True) or {}).get("endpoint", ""))
        return jsonify(ok=True)

    # ------------------------------------------------------------------ dev helpers
    if dev:
        @app.post("/api/dev/reset")
        def dev_reset():
            store.reset_data()
            return jsonify(ok=True)

    # ------------------------------------------------------------------ static PWA
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/<path:path>")
    def static_files(path):
        if path.startswith("api/"):
            abort(404)
        resp = send_from_directory(STATIC, path)
        if path == "sw.js":
            resp.headers["Service-Worker-Allowed"] = "/"
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    return app


def _validate(p: dict):
    import re

    if "scan_time" in p and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(p["scan_time"])):
        raise ValueError("scan_time must be HH:MM (24h)")
    for k, lo, hi in (("scan_window_min", 0, 60), ("radius_mi", 0.1, 50), ("max_radius_mi", 0.1, 100),
                      ("min_comps", 1, 50), ("low_ppsf_pct", 1, 90), ("lookback_days", 1, 365)):
        if k in p and not (isinstance(p[k], (int, float)) and lo <= p[k] <= hi):
            raise ValueError(f"{k} must be between {lo} and {hi}")
    if "buy_box" in p and not isinstance(p["buy_box"], dict):
        raise ValueError("buy_box must be an object")
    if "geocode_provider" in p and p["geocode_provider"] not in ("census", "google"):
        raise ValueError("geocode_provider must be 'census' or 'google'")
    if "geocode_api_key" in p and not isinstance(p["geocode_api_key"], str):
        raise ValueError("geocode_api_key must be a string")
    if "geocode_setup_progress" in p and not (isinstance(p["geocode_setup_progress"], list)
                                               and all(isinstance(x, str) for x in p["geocode_setup_progress"])):
        raise ValueError("geocode_setup_progress must be a list of strings")
    for k in ("email_address", "email_app_password", "imap_host", "imap_folder"):
        if k in p and not isinstance(p[k], str):
            raise ValueError(f"{k} must be a string")
    if "imap_setup_progress" in p and not (isinstance(p["imap_setup_progress"], list)
                                            and all(isinstance(x, str) for x in p["imap_setup_progress"])):
        raise ValueError("imap_setup_progress must be a list of strings")
