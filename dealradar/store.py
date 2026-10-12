"""SQLite storage: settings, seen emails, deals, alerts, push subscriptions, scan log."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .scoring import merged_settings

SCHEMA = """
create table if not exists kv(key text primary key, value text);
create table if not exists emails(msg_id text primary key, subject text, sender text, date text,
                                  scanned_at text, n_listings integer);
create table if not exists deals(id text primary key, data text not null, first_seen text, last_seen text,
                                 msg_id text, price_prev real, contacted_at text);
create table if not exists alerts(id integer primary key autoincrement, deal_id text, kind text, title text,
                                  body text, created_at text, read_at text, price_at real, data text,
                                  unique(deal_id, kind, price_at));
create table if not exists push_subs(endpoint text primary key, data text);
create table if not exists scans(id integer primary key autoincrement, started_at text, trigger text,
                                 emails_new integer, deals_new integer, hot integer, alerts integer, message text);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str = "dealradar.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ key/value + settings
    def kv_get(self, key, default=None):
        with self.lock:
            r = self.db.execute("select value from kv where key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def kv_set(self, key, value):
        with self.lock:
            self.db.execute("insert or replace into kv values(?,?)", (key, json.dumps(value)))
            self.db.commit()

    def get_settings(self) -> dict:
        return merged_settings(self.kv_get("settings", {}))

    def set_settings(self, patch: dict) -> dict:
        cur = self.kv_get("settings", {})
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                cur[k].update(v)
            else:
                cur[k] = v
        self.kv_set("settings", cur)
        return self.get_settings()

    # ------------------------------------------------------------------ emails
    def email_seen(self, msg_id: str) -> bool:
        with self.lock:
            return self.db.execute("select 1 from emails where msg_id=?", (msg_id,)).fetchone() is not None

    def add_email(self, msg_id, subject, sender, date, n_listings, now):
        with self.lock:
            self.db.execute("insert or ignore into emails values(?,?,?,?,?,?)",
                            (msg_id, subject, sender, date, iso(now), n_listings))
            self.db.commit()

    # ------------------------------------------------------------------ deals
    def upsert_deal(self, listing: dict, msg_id: str, now: datetime) -> str:
        """Returns 'new', 'price_drop', 'price_up' or 'seen'."""
        with self.lock:
            r = self.db.execute("select data from deals where id=?", (listing["id"],)).fetchone()
            if not r:
                self.db.execute("insert into deals(id,data,first_seen,last_seen,msg_id) values(?,?,?,?,?)",
                                (listing["id"], json.dumps(listing), iso(now), iso(now), msg_id))
                self.db.commit()
                return "new"
            old = json.loads(r["data"])
            status = "seen"
            if old.get("price") and listing.get("price") and listing["price"] != old["price"]:
                status = "price_drop" if listing["price"] < old["price"] else "price_up"
            self.db.execute("update deals set data=?, last_seen=?, msg_id=?, price_prev=? where id=?",
                            (json.dumps(listing), iso(now), msg_id,
                             old["price"] if status != "seen" else None, listing["id"]))
            self.db.commit()
            return status

    def _deal_row(self, r) -> dict:
        d = json.loads(r["data"])
        d.update(first_seen=r["first_seen"], last_seen=r["last_seen"], contacted_at=r["contacted_at"],
                 price_prev=r["price_prev"])
        return d

    def deals_in_window(self, days: int, now: datetime) -> list[dict]:
        cutoff = iso(now - timedelta(days=days))
        with self.lock:
            rows = self.db.execute("select * from deals where last_seen>=? order by first_seen desc", (cutoff,)).fetchall()
        return [self._deal_row(r) for r in rows]

    def get_deal(self, deal_id: str) -> dict | None:
        with self.lock:
            r = self.db.execute("select * from deals where id=?", (deal_id,)).fetchone()
        return self._deal_row(r) if r else None

    def mark_contacted(self, deal_id: str, now: datetime) -> bool:
        with self.lock:
            cur = self.db.execute("update deals set contacted_at=? where id=?", (iso(now), deal_id))
            self.db.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ alerts
    def add_alert(self, kind, title, body, now, deal_id=None, price_at=None, data=None) -> int | None:
        with self.lock:
            try:
                cur = self.db.execute(
                    "insert into alerts(deal_id,kind,title,body,created_at,price_at,data) values(?,?,?,?,?,?,?)",
                    (deal_id, kind, title, body, iso(now), price_at, json.dumps(data or {})))
                self.db.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None  # already alerted for this deal/kind/price

    def list_alerts(self, limit=100, after_id=0) -> list[dict]:
        with self.lock:
            rows = self.db.execute("select * from alerts where id>? order by id desc limit ?", (after_id, limit)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]

    def mark_alert_read(self, alert_id: int | None, now: datetime):
        with self.lock:
            if alert_id is None:
                self.db.execute("update alerts set read_at=? where read_at is null", (iso(now),))
            else:
                self.db.execute("update alerts set read_at=? where id=? and read_at is null", (iso(now), alert_id))
            self.db.commit()

    # ------------------------------------------------------------------ push
    def add_push_sub(self, sub: dict):
        with self.lock:
            self.db.execute("insert or replace into push_subs values(?,?)", (sub["endpoint"], json.dumps(sub)))
            self.db.commit()

    def remove_push_sub(self, endpoint: str):
        with self.lock:
            self.db.execute("delete from push_subs where endpoint=?", (endpoint,))
            self.db.commit()

    def push_subs(self) -> list[dict]:
        with self.lock:
            return [json.loads(r["data"]) for r in self.db.execute("select data from push_subs").fetchall()]

    # ------------------------------------------------------------------ scans
    def add_scan(self, now, trigger, emails_new, deals_new, hot, alerts, message):
        with self.lock:
            self.db.execute("insert into scans(started_at,trigger,emails_new,deals_new,hot,alerts,message) values(?,?,?,?,?,?,?)",
                            (iso(now), trigger, emails_new, deals_new, hot, alerts, message))
            self.db.commit()

    def last_scan(self) -> dict | None:
        with self.lock:
            r = self.db.execute("select * from scans order by id desc limit 1").fetchone()
        return dict(r) if r else None

    def reset_data(self):
        """Dev only: forget emails, deals, alerts and scans (keeps settings and push subscriptions)."""
        with self.lock:
            for t in ("emails", "deals", "alerts", "scans"):
                self.db.execute(f"delete from {t}")
            self.db.execute("delete from kv where key like 'sched:%'")
            self.db.commit()
