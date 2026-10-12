from datetime import datetime, timezone

import pytest

from dealradar.server import create_app
from dealradar.sources import FolderSource

NOW = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, inbox, monkeypatch):
    monkeypatch.setenv("DEALRADAR_VAPID", str(tmp_path / "v.pem"))
    app = create_app(str(tmp_path / "a.db"), source=FolderSource(inbox), start_scheduler=False,
                     now_fn=lambda: NOW, dev=True)
    return app.test_client()


def test_scan_then_deals(client):
    r = client.post("/api/scan").get_json()
    # min_comps=5, widening closest-first past the 0.5mi appraisal-ideal ring when it's thin.
    assert r["deals_new"] == 50 and r["hot"] == 6
    deals = client.get("/api/deals").get_json()
    assert len(deals) == 50
    hess = next(d for d in deals if d["street"] == "1216 E Hess Ave")
    assert hess["hot"] and round(hess["ppsf"]) == 110 and round(hess["median_ppsf"]) == 202
    assert hess["tel"] == "tel:+14802834425" and hess["sms"].startswith("sms:+14802834425")
    assert hess["contact_name"] == "Spencer" and hess["n_comps"] == 5 and len(hess["comps"]) == 5
    assert hess["n_in_radius"] == 0  # none of Hess's ZIP-mates are same-class; all 5 comps came from widening out


def test_status_and_unknown_deal(client):
    client.post("/api/scan")
    st = client.get("/api/status").get_json()
    assert st["counts"] == {"deals": 50, "hot": 6} and st["unread"] == 7 and st["next_scan"].endswith("-07:00")
    assert st["push_key"] and st["last_scan"]["hot"] == 6
    assert client.get("/api/deals/nope").status_code == 404


def test_settings_validation_and_effect(client):
    client.post("/api/scan")
    assert client.put("/api/settings", json={"scan_time": "25:99"}).status_code == 400
    assert client.put("/api/settings", json={"radius_mi": 0}).status_code == 400
    assert client.put("/api/settings", json={"min_comps": 3}).status_code == 200
    assert client.get("/api/status").get_json()["counts"]["hot"] >= 6  # at least as many as min_comps=5 found
    client.put("/api/settings", json={"buy_box": {"max_price": 200000}})
    hot = [d["street"] for d in client.get("/api/deals").get_json() if d["hot"]]
    assert "1216 E Hess Ave" in hot and all(d["price"] <= 200000 for d in client.get("/api/deals").get_json() if d["hot"])


def test_geocode_provider_settings(client):
    assert client.put("/api/settings", json={"geocode_provider": "bing"}).status_code == 400
    assert client.put("/api/settings", json={"geocode_api_key": 123}).status_code == 400
    assert client.put("/api/settings", json={"geocode_setup_progress": ["account", 7]}).status_code == 400
    r = client.put("/api/settings", json={"geocode_provider": "google", "geocode_api_key": "AIzaTest",
                                           "geocode_setup_progress": ["account", "project"]})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()
    assert s["geocode_provider"] == "google" and s["geocode_api_key"] == "AIzaTest"
    assert s["geocode_setup_progress"] == ["account", "project"]


def test_email_settings_validation(client):
    assert client.put("/api/settings", json={"email_address": 123}).status_code == 400
    assert client.put("/api/settings", json={"imap_setup_progress": ["verify", 7]}).status_code == 400
    r = client.put("/api/settings", json={"email_address": "mason@gmail.com", "email_app_password": "abcd efgh",
                                           "imap_setup_progress": ["verify", "apppw"]})
    assert r.status_code == 200
    s = client.get("/api/settings").get_json()
    assert s["email_address"] == "mason@gmail.com" and s["email_app_password"] == "abcd efgh"
    assert s["imap_setup_progress"] == ["verify", "apppw"]


def test_email_test_endpoint_without_credentials(client):
    r = client.post("/api/email/test").get_json()
    assert r["ok"] is False and "password" in r["error"]


def test_email_test_endpoint_reports_bad_login(client, monkeypatch):
    client.put("/api/settings", json={"email_address": "mason@gmail.com", "email_app_password": "wrong"})
    import imaplib

    class FailsLogin:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FailsLogin)
    r = client.post("/api/email/test").get_json()
    assert r["ok"] is False and "mason@gmail.com" in r["error"] and "wrong" not in r["error"]


def test_contact_override(client):
    client.post("/api/scan")
    client.put("/api/settings", json={"contact_phone_override": "+16025550123"})
    d = next(d for d in client.get("/api/deals").get_json() if d["street"] == "1216 E Hess Ave")
    assert d["tel"] == "tel:+16025550123"


def test_contacted_and_alert_read(client):
    client.post("/api/scan")
    d = next(d for d in client.get("/api/deals").get_json() if d["hot"])
    assert client.post(f"/api/deals/{d['id']}/contacted").status_code == 200
    assert client.get(f"/api/deals/{d['id']}").get_json()["contacted_at"]
    assert client.post("/api/deals/zzz/contacted").status_code == 404
    a = client.get("/api/alerts").get_json()
    assert len(a) == 7  # the digest + 6 hot alerts
    client.post("/api/alerts/read", json={"id": a[0]["id"]})
    assert client.get("/api/status").get_json()["unread"] == 6
    client.post("/api/alerts/read", json={})
    assert client.get("/api/status").get_json()["unread"] == 0
    assert client.get("/api/alerts?after=" + str(a[0]["id"])).get_json() == []


def test_push_subscribe(client):
    assert client.post("/api/push/subscribe", json={"nope": 1}).status_code == 400
    assert client.post("/api/push/subscribe", json={"endpoint": "https://p/x", "keys": {}}).status_code == 200
    assert client.get("/api/status").get_json()["push_subscriptions"] == 1
    client.post("/api/push/unsubscribe", json={"endpoint": "https://p/x"})
    assert client.get("/api/status").get_json()["push_subscriptions"] == 0


def test_token_protects_api(tmp_path, inbox, monkeypatch):
    monkeypatch.setenv("DEALRADAR_VAPID", str(tmp_path / "v.pem"))
    monkeypatch.setenv("DEALRADAR_TOKEN", "s3cret")
    c = create_app(str(tmp_path / "b.db"), source=FolderSource(inbox), start_scheduler=False).test_client()
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Token": "s3cret"}).status_code == 200


def test_dev_reset(client):
    client.post("/api/scan")
    client.post("/api/dev/reset")
    assert client.get("/api/status").get_json()["counts"]["deals"] == 0
    assert client.post("/api/scan").get_json()["deals_new"] == 50  # rescans cleanly
