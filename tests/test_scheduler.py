"""Daily timing: fires at scan_time (never early), <= 5 min late, catch-up, retries, once per day."""
from datetime import datetime, timedelta, timezone

from dealradar.scheduler import Scheduler

# Phoenix is UTC-7 all year, so 11:30 local == 18:30Z
def at(h, m, s=0, day=20):
    return datetime(2026, 9, day, h + 7, m, s, tzinfo=timezone.utc)


def make(store, found=True):
    calls = []

    def scan(trigger, now):
        calls.append((trigger, now))
        return {"emails_new": 1 if found else 0, "trigger": trigger}

    return Scheduler(store, scan), calls


def test_not_before_scan_time(store):
    s, calls = make(store)
    assert s.tick(at(11, 29, 59)) is None and calls == []


def test_fires_inside_window_then_only_once(store):
    s, calls = make(store)
    assert s.tick(at(11, 30, 10))["trigger"] == "scheduled:window"
    assert s.tick(at(11, 31)) is None and s.tick(at(15, 0)) is None
    assert len(calls) == 1


def test_five_minute_edge(store):
    s, _ = make(store)
    assert s.due(at(11, 35, 0)) == "window"      # exactly +5:00 is still in the window
    assert s.due(at(11, 35, 1)) == "catch_up"    # one second later it is a catch-up


def test_catch_up_when_missed_and_can_be_disabled(store):
    s, _ = make(store)
    assert s.due(at(14, 0)) == "catch_up"
    store.set_settings({"catch_up": False})
    assert s.due(at(14, 0)) is None


def test_retries_when_no_email_then_gives_up(store):
    s, calls = make(store, found=False)
    assert s.tick(at(11, 30))["trigger"] == "scheduled:window"
    assert s.tick(at(11, 32)) is None                       # too soon
    for minute in (36, 42, 48):                             # three retries, 5+ min apart
        assert s.tick(at(11, minute))["trigger"] == "scheduled:retry"
    assert s.tick(at(12, 30)) is None                       # exhausted
    assert len(calls) == 4


def test_next_day_resets(store):
    s, calls = make(store)
    s.tick(at(11, 30))
    assert s.tick(at(11, 30, day=21)) is not None and len(calls) == 2


def test_error_does_not_kill_and_retries(store):
    def bad(trigger, now):
        raise RuntimeError("gmail down")

    s = Scheduler(store, bad)
    r = s.tick(at(11, 30))
    assert "gmail down" in r["error"] and s.last_error == "gmail down"
    assert s.tick(at(11, 36)) is not None


def test_settings_change_scan_time(store):
    s, _ = make(store)
    store.set_settings({"scan_time": "07:15"})
    assert s.due(at(7, 14)) is None and s.due(at(7, 16)) == "window"


def test_next_run_display(store):
    s, _ = make(store)
    assert s.next_run(at(9, 0)) == "2026-09-20T11:30-07:00"
    s.tick(at(11, 30))
    assert s.next_run(at(12, 0)) == "2026-09-21T11:30-07:00"
