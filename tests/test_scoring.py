import copy
import statistics
from pathlib import Path

import pytest

from dealradar.geo import haversine_mi, locate
from dealradar.parser import parse_eml_bytes
from dealradar.scoring import analyze, buy_box_check, merged_settings

FIXTURE = Path(__file__).parent / "fixtures" / "sample.eml"


@pytest.fixture(scope="module")
def listings():
    return parse_eml_bytes(FIXTURE.read_bytes())["listings"]


@pytest.fixture(scope="module")
def scored(listings):
    return {l["street"]: l for l in analyze(listings)}


def test_haversine_known_distance():
    phoenix, tempe = (33.4484, -112.0740), (33.4255, -111.9400)
    assert 7.5 < haversine_mi(phoenix, tempe) < 8.3


def test_ppsf_values_by_hand(scored):
    assert scored["2902 W Pierce St"]["ppsf"] == pytest.approx(219900 / 1405)   # 156.51
    assert scored["1216 E Hess Ave"]["ppsf"] == pytest.approx(130000 / 1178)    # 110.36
    assert scored["3620 W Missouri Ave"]["ppsf"] == pytest.approx(235000 / 1452)  # 161.85


def test_land_is_priced_per_lot_sqft_and_never_compared(scored):
    l = scored["421 Hinton St"]
    assert l["ppsf"] is None and l["ppsf_lot"] == pytest.approx(19000 / 6534)
    assert l["n_comps"] == 0 and "Land" in l["compare_note"]


def test_no_sqft_means_no_ppsf(scored):
    l = scored["635 E Mohave Cir"]
    assert l["ppsf"] is None and "square footage" in l["compare_note"]


def test_zip_city_mismatch_uses_city(scored):
    g = scored["421 Hinton St"]["geo"]
    assert g["source"] == "city" and g["confidence"] == "low" and "does not match" in g["warning"]
    assert 32.7 < g["lat"] < 32.8  # Eloy, not Tucson (32.23)


def test_median_and_discount_are_consistent_with_comps(scored):
    l = scored["1216 E Hess Ave"]
    med = statistics.median(c["ppsf"] for c in l["comps"])
    assert l["median_ppsf"] == pytest.approx(med, abs=0.01)
    assert l["discount_pct"] == pytest.approx((med - l["ppsf"]) / med * 100, abs=0.1)
    assert all(c["dist_mi"] <= 25 for c in l["comps"])  # max_radius_mi default
    assert l["comps"] == sorted(l["comps"], key=lambda c: c["dist_mi"])
    assert l["pool_size"] == l["n_comps"] + 1


def test_comps_widen_past_the_ideal_radius_when_thin(scored):
    # Hess's ZIP has no other same-class listing at all, so none of its comps are within the
    # 0.5mi appraisal-ideal ring -- the search widens outward, closest-first, until it has 5.
    l = scored["1216 E Hess Ave"]
    assert l["n_in_radius"] == 0 and l["n_comps"] == 5
    assert all(not c["in_radius"] for c in l["comps"])
    assert l["comps"] == sorted(l["comps"], key=lambda c: c["dist_mi"])  # closest first
    assert "widened to" in l["compare_note"]

    # Stirrup's ZIP (85143) has a few same-class neighbors inside 0.5mi, so those are used first
    # and only the rest of the 5 come from farther out.
    s = scored["1506 E Stirrup Ln"]
    assert 0 < s["n_in_radius"] < s["n_comps"] == 5
    assert all(c["in_radius"] for c in s["comps"][: s["n_in_radius"]])
    assert all(not c["in_radius"] for c in s["comps"][s["n_in_radius"]:])


def test_hot_set_with_defaults(scored):
    hot = {k for k, v in scored.items() if v["hot"]}
    assert hot == {"1207 E Desert Cove Ave", "1216 E Hess Ave", "1506 E Stirrup Ln",
                   "15448 W Port Royale Ln", "19160 N Alcazar St", "2929 E Eberle Ln"}


def test_value_tier_reflects_discount_regardless_of_buy_box(scored):
    # green/yellow/red is the plain "how does this compare to the area" signal; it tracks
    # discount_pct directly and does not depend on in_buy_box the way hot/tier does.
    for l in scored.values():
        if l["discount_pct"] is None:
            assert l["value_tier"] == "unknown"
        elif l["discount_pct"] >= 15:  # default low_ppsf_pct
            assert l["value_tier"] == "green"
        elif l["discount_pct"] >= 0:
            assert l["value_tier"] == "yellow"
        else:
            assert l["value_tier"] == "red"
    # Hess is far enough below median to be green even though it's also "hot"
    assert scored["1216 E Hess Ave"]["value_tier"] == "green"


def test_fewer_comps_needed_surfaces_more(listings):
    hot = {l["street"] for l in analyze(listings, {"min_comps": 3}) if l["hot"]}
    assert "1506 E Stirrup Ln" in hot


def test_threshold_and_radius_settings(listings):
    assert not any(l["hot"] for l in analyze(listings, {"low_ppsf_pct": 50}))  # above the fixture's max discount
    tight = {l["street"]: l for l in analyze(listings, {"radius_mi": 2})}
    loose = {l["street"]: l for l in analyze(listings, {"radius_mi": 10})}
    assert tight["1216 E Hess Ave"]["n_comps"] < loose["1216 E Hess Ave"]["n_comps"]


def test_max_radius_mi_caps_how_far_the_search_widens(listings):
    narrow = analyze(listings, {"max_radius_mi": 1})
    l = next(x for x in narrow if x["street"] == "1216 E Hess Ave")
    assert all(c["dist_mi"] <= 1 for c in l["comps"])
    assert l["n_comps"] < 5  # not enough same-class listings within 1mi, and it won't look farther
    assert "Only" in l["compare_note"] and "need 5" in l["compare_note"]


def test_condos_and_houses_never_compared(scored, listings):
    ids = {l["id"]: l for l in listings}
    for l in scored.values():
        for c in l["comps"]:
            assert (ids[c["id"]]["type"] in ("condo",)) == (l["type"] == "condo")


def test_buy_box_reasons():
    l = {"type": "condo", "price": 500000, "beds": 2, "sqft": 700, "occupancy": "occupied"}
    why = buy_box_check(l, merged_settings({"buy_box": {"allow_occupied": False}})["buy_box"])
    assert len(why) == 5


def test_inputs_not_mutated(listings):
    before = copy.deepcopy(listings)
    analyze(listings)
    assert listings == before
