"""End-to-end scan: email in -> deals stored -> alerts + push out."""
from datetime import datetime, timezone

from dealradar.push import PushNotifier
from dealradar.scanner import run_scan
from dealradar.sources import FolderSource

NOW = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)  # 11:30 in Phoenix


def test_first_scan_creates_deals_hot_alerts_and_digest(store, inbox):
    # min_comps=5, widening closest-first past the 0.5mi appraisal-ideal ring when it's thin,
    # so even this fixture's spread-out addresses end up with real comps and real hot deals.
    r = run_scan(store, FolderSource(inbox), now=NOW)
    assert (r["emails_new"], r["deals_new"], r["hot"]) == (1, 50, 6)
    kinds = sorted(a["kind"] for a in store.list_alerts())
    assert kinds == ["digest", "hot", "hot", "hot", "hot", "hot", "hot"]
    titles = " | ".join(a["title"] for a in store.list_alerts())
    assert "1216 E Hess Ave" in titles and "Eberle" in titles
    hess = next(a for a in store.list_alerts() if "Hess" in a["title"])
    assert "$110/sq ft" in hess["body"] and "46% below" in hess["body"]
    assert store.last_scan()["hot"] == 6


def test_second_scan_is_idempotent(store, inbox):
    run_scan(store, FolderSource(inbox), now=NOW)
    n = len(store.list_alerts())
    r = run_scan(store, FolderSource(inbox), now=NOW)
    assert (r["emails_new"], r["deals_new"], r["alerts_created"]) == (0, 0, 0)
    assert len(store.list_alerts()) == n
    assert r["message"] == "No new wholesale email found"


def test_price_drop_realerts_once(store, inbox):
    run_scan(store, FolderSource(inbox), now=NOW)
    raw = (inbox / "sample.eml").read_bytes()
    import re
    edited = re.sub(rb"Price: \$130,000(\r?\n)ARV: \$300,000(\r?\n)Scrape", rb"Price: $119,000\1ARV: $300,000\2Scrape", raw)
    assert edited != raw  # the fixture uses CRLF line endings
    edited = edited.replace(b"Message-ID: <CABiDMzrbmwP1MS2LC_StcKvoXUtp-D-25oRhFTfOM0KCMhxhOQ@mail.gmail.com>",
                            b"Message-ID: <second-email@example.com>")
    (inbox / "day2.eml").write_bytes(edited)
    r = run_scan(store, FolderSource(inbox), now=NOW)
    assert r["emails_new"] == 1 and r["price_changes"] == 1 and r["deals_new"] == 0
    drops = [a for a in store.list_alerts() if a["title"].startswith("Price drop")]
    assert len(drops) == 1 and "was $130,000" in drops[0]["body"]
    run_scan(store, FolderSource(inbox), now=NOW)
    assert len([a for a in store.list_alerts() if a["title"].startswith("Price drop")]) == 1


def test_push_sent_for_hot_deals_not_digest(store, inbox, tmp_path):
    sent = []
    n = PushNotifier(store, vapid_path=tmp_path / "v.pem", sender=lambda **kw: sent.append(kw))
    store.add_push_sub({"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}})
    r = run_scan(store, FolderSource(inbox), notifier=n, now=NOW)
    # 6 hot deals > MAX_INDIVIDUAL_PUSHES (5), so this is one grouped push, not the digest.
    assert r["pushed"] == 1 and len(sent) == 1
    import json
    p = json.loads(sent[0]["data"])
    assert p["url"] == "/#/alerts" and "hot deals" in p["title"]
    assert len(n.public_key) > 80  # VAPID public key for the browser


def test_push_sent_individually_for_a_few_hot_deals(store, inbox, tmp_path):
    store.set_settings({"low_ppsf_pct": 35})  # narrows it to 2 hot deals, under MAX_INDIVIDUAL_PUSHES
    sent = []
    n = PushNotifier(store, vapid_path=tmp_path / "v.pem", sender=lambda **kw: sent.append(kw))
    store.add_push_sub({"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}})
    r = run_scan(store, FolderSource(inbox), notifier=n, now=NOW)
    assert r["hot"] == 2 and r["pushed"] == 2 and len(sent) == 2
    import json
    p = json.loads(sent[0]["data"])
    assert p["url"].startswith("/#/deal/") and "sq ft" in p["body"]


def test_digest_push_when_nothing_hot(store, inbox, tmp_path):
    store.set_settings({"low_ppsf_pct": 60})
    sent = []
    n = PushNotifier(store, vapid_path=tmp_path / "v.pem", sender=lambda **kw: sent.append(kw))
    store.add_push_sub({"endpoint": "https://push.example/abc", "keys": {}})
    r = run_scan(store, FolderSource(inbox), notifier=n, now=NOW)
    assert r["hot"] == 0 and len(sent) == 1 and "digest" in sent[0]["data"].lower()


def test_gone_subscription_is_removed(store, inbox, tmp_path):
    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def boom(**kw):
        raise Gone()

    n = PushNotifier(store, vapid_path=tmp_path / "v.pem", sender=boom)
    store.add_push_sub({"endpoint": "https://push.example/dead", "keys": {}})
    run_scan(store, FolderSource(inbox), notifier=n, now=NOW)
    assert store.push_subs() == []


def test_bad_email_file_does_not_break_scan(store, inbox):
    (inbox / "junk.eml").write_bytes(b"not an email at all \xff\xfe")
    (inbox / "broken.json").write_text("{nope")
    r = run_scan(store, FolderSource(inbox), now=NOW)
    assert r["deals_new"] == 50


def test_two_real_emails_overlap_and_price_cuts(store, inbox):
    """Real inbox: an early blast and a later re-send of the same listings with reduced prices."""
    import shutil
    from pathlib import Path
    shutil.copy(Path(__file__).parent / "fixtures" / "gmail_0914_early.json", inbox / "a_early.json")  # sorts first
    r = run_scan(store, FolderSource(inbox), now=NOW)
    assert r["emails_new"] == 2
    # 50 + 36 listings, minus the ones that appear in both emails
    all_ids = {d["id"] for d in store.deals_in_window(30, NOW)}
    assert len(all_ids) < 86 and len(all_ids) > 60
    drops = {a["title"].removeprefix("Price drop: ") for a in store.list_alerts() if a["title"].startswith("Price drop")}
    # the later email is the reduced-price version, so nothing should read as a price *rise*
    d = {x["street"]: x for x in store.deals_in_window(30, NOW)}
    assert d["2902 W Pierce St"]["price"] == 219900 and d["2902 W Pierce St"]["price_prev"] == 229900
    assert d["10436 N 37th Ave"]["price"] == 279900 and d["10436 N 37th Ave"]["price_prev"] == 285000
    assert d["1533 W 5th St"]["price"] == 339000 and d["1533 W 5th St"]["price_prev"] == 349000


def test_scan_order_is_by_email_date_not_filename(store, inbox):
    import shutil
    from pathlib import Path
    # name the EARLY email so it sorts LAST alphabetically; result must not change
    shutil.copy(Path(__file__).parent / "fixtures" / "gmail_0914_early.json", inbox / "z_early.json")
    run_scan(store, FolderSource(inbox), now=NOW)
    d = {x["street"]: x for x in store.deals_in_window(30, NOW)}
    assert d["2902 W Pierce St"]["price"] == 219900  # the later (reduced) price wins
