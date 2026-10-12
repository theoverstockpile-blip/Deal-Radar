"""Daily scan timing.

Rule: scan once a day at `scan_time` (local), never early. The scan may start up to
`scan_window_min` minutes late (default 5). Extra behavior, because phones/servers sleep and
mail sometimes lands late:
  * catch-up  - if the window was missed entirely (e.g. server was off), scan once when it wakes.
  * retries   - if the scan finds no new email, re-check every 5 min, up to 3 more times.
State lives in the DB so a restart mid-day does not double-scan.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .store import Store, iso, utcnow

RETRIES = 3
RETRY_GAP_MIN = 5


class Scheduler:
    def __init__(self, store: Store, scan_fn, tick_seconds: int = 30, now_fn=utcnow):
        self.store, self.scan_fn, self.tick_seconds, self.now_fn = store, scan_fn, tick_seconds, now_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    # ---------------------------------------------------------------- pure logic (unit-tested)
    def _local(self, now: datetime, s: dict):
        tz = ZoneInfo(s["timezone"])
        loc = now.astimezone(tz)
        hh, mm = (int(x) for x in s["scan_time"].split(":"))
        return loc, loc.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def _state(self, local: datetime) -> dict:
        st = self.store.kv_get("sched:day") or {}
        if st.get("date") != local.date().isoformat():
            st = {"date": local.date().isoformat(), "attempts": 0, "found": False, "last": None}
        return st

    def due(self, now: datetime | None = None) -> str | None:
        """'window' | 'catch_up' | 'retry' | None"""
        now = now or self.now_fn()
        s = self.store.get_settings()
        local, target = self._local(now, s)
        st = self._state(local)
        if st["found"] or local < target:
            return None
        if st["attempts"] == 0:
            if local <= target + timedelta(minutes=s["scan_window_min"]):
                return "window"
            return "catch_up" if s["catch_up"] else None
        if st["attempts"] <= RETRIES and st["last"]:
            last = datetime.fromisoformat(st["last"]).astimezone(local.tzinfo)
            if local >= last + timedelta(minutes=RETRY_GAP_MIN) and local <= target + timedelta(hours=2):
                return "retry"
        return None

    def next_run(self, now: datetime | None = None) -> str:
        """ISO time (local tz) of the next planned scan."""
        now = now or self.now_fn()
        s = self.store.get_settings()
        local, target = self._local(now, s)
        st = self._state(local)
        if local < target and not st["found"]:
            return target.isoformat(timespec="minutes")
        return (target + timedelta(days=1)).isoformat(timespec="minutes")

    # ---------------------------------------------------------------- run
    def tick(self, now: datetime | None = None) -> dict | None:
        now = now or self.now_fn()
        why = self.due(now)
        if not why:
            return None
        s = self.store.get_settings()
        local, _ = self._local(now, s)
        st = self._state(local)
        st["attempts"] += 1
        st["last"] = iso(now)
        try:
            result = self.scan_fn(trigger=f"scheduled:{why}", now=now)
            st["found"] = result["emails_new"] > 0
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - keep the loop alive, retry rules apply
            result = {"error": str(e), "emails_new": 0, "trigger": f"scheduled:{why}"}
            self.last_error = str(e)
        self.store.kv_set("sched:day", st)
        return result

    # ---------------------------------------------------------------- thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dealradar-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
            time.sleep(self.tick_seconds)
