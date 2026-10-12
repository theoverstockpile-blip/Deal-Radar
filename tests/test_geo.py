from dealradar import geo


def test_geocode_listing_uses_census_by_default(monkeypatch):
    monkeypatch.setattr(geo, "geocode_online", lambda line: (33.1, -112.1))
    called_google = []
    monkeypatch.setattr(geo, "geocode_online_google", lambda line, key: called_google.append(line) or (0, 0))
    listing = {"street": "1 Main St", "city": "Phoenix", "state": "AZ", "zip": "85007"}
    result = geo.geocode_listing(listing, online=True, provider="census")
    assert result == {"lat": 33.1, "lon": -112.1, "confidence": "high", "source": "census"}
    assert not called_google


def test_geocode_listing_uses_google_when_selected_with_key(monkeypatch):
    monkeypatch.setattr(geo, "geocode_online_google", lambda line, key: (34.2, -111.2) if key == "secret" else None)
    listing = {"street": "1 Main St", "city": "Phoenix", "state": "AZ", "zip": "85007"}
    result = geo.geocode_listing(listing, online=True, provider="google", api_key="secret")
    assert result == {"lat": 34.2, "lon": -111.2, "confidence": "high", "source": "google"}


def test_geocode_listing_falls_back_offline_without_a_google_key(monkeypatch):
    called = []
    monkeypatch.setattr(geo, "geocode_online_google", lambda line, key: called.append(1) or (9, 9))
    listing = {"street": "1 Main St", "city": "Phoenix", "state": "AZ", "zip": "85007"}
    result = geo.geocode_listing(listing, online=True, provider="google", api_key=None)
    assert not called  # never called the paid API without a key
    assert result["source"] == "zip"  # fell back to the offline ZIP centroid


def test_geocode_listing_falls_back_offline_when_google_errors(monkeypatch):
    def boom(line, key):
        raise RuntimeError("network down")
    monkeypatch.setattr(geo, "geocode_online_google", boom)
    listing = {"street": "1 Main St", "city": "Phoenix", "state": "AZ", "zip": "85007"}
    result = geo.geocode_listing(listing, online=True, provider="google", api_key="secret")
    assert result["source"] == "zip"


def test_geocode_listing_caches_results(monkeypatch):
    calls = []
    monkeypatch.setattr(geo, "geocode_online", lambda line: calls.append(1) or (1.0, 2.0))
    cache = {}
    listing = {"street": "1 Main St", "city": "Phoenix", "state": "AZ", "zip": "85007"}
    geo.geocode_listing(listing, online=True, provider="census", cache_get=cache.get, cache_set=cache.__setitem__)
    geo.geocode_listing(listing, online=True, provider="census", cache_get=cache.get, cache_set=cache.__setitem__)
    assert len(calls) == 1  # second call hit the cache, not the geocoder
