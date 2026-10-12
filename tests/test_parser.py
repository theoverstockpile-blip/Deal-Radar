"""Parser tests, pinned to values read by hand from tests/fixtures/sample.eml."""
from pathlib import Path

import pytest

from dealradar.parser import parse_eml_bytes, parse_lot_sqft, parse_text

FIXTURE = Path(__file__).parent / "fixtures" / "sample.eml"


@pytest.fixture(scope="module")
def parsed():
    return parse_eml_bytes(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def by_street(parsed):
    return {l["street"]: l for l in parsed["listings"]}


def test_count_and_contact(parsed):
    assert len(parsed["listings"]) == 50  # the HTML twin also has 50 address lines
    assert parsed["contact"] == {"name": "Spencer", "phone": "+14802834425", "display": "480-283-4425"}
    assert all(l["contact"]["phone"] == "+14802834425" for l in parsed["listings"])
    assert parsed["message_id"] and "Wholesale" in parsed["subject"]


def test_listing_with_its_own_contact_overrides_the_email_wide_one():
    text = """Wholesale Opportunity

Text Spencer 480-283-4425

123 W Alpha St, Phoenix, AZ 85007
Price: $150,000
Beds: 3
Baths: 2
Sq Ft: 1200

456 E Beta Ave, Phoenix, AZ 85008
Price: $160,000
Beds: 3
Baths: 2
Sq Ft: 1300
Co-posted listing -- call Jordan 602-555-0101 for this one

789 N Gamma Dr, Phoenix, AZ 85009
Price: $170,000
Beds: 3
Baths: 2
Sq Ft: 1400
"""
    parsed = parse_text(text)
    by_street = {l["street"]: l for l in parsed["listings"]}
    # The co-posted listing gets Jordan's number, not Spencer's (the email-wide default) --
    # it's the "text/call <name> <phone>" line sitting right in that listing's own block.
    assert by_street["456 E Beta Ave"]["contact"]["name"] == "Jordan"
    assert by_street["456 E Beta Ave"]["contact"]["phone"] == "+16025550101"
    # Listings with no contact of their own still fall back to the one email-wide number.
    assert by_street["123 W Alpha St"]["contact"]["phone"] == "+14802834425"
    assert by_street["789 N Gamma Dr"]["contact"]["phone"] == "+14802834425"


def test_ids_unique(parsed):
    ids = [l["id"] for l in parsed["listings"]]
    assert len(set(ids)) == len(ids)


def test_photos_from_html_twin(parsed, by_street):
    # Real photos, pulled from the email's HTML part and matched back to each listing's own
    # "PICTURES" link -- not the Mailchimp header/footer banner art, and not a Google Drive link.
    from collections import Counter
    counts = Counter(len(l["photos"]) for l in parsed["listings"])
    assert counts == {0: 8, 1: 35, 2: 6, 3: 1}  # 42 of 50 listings have photos, 50 photo URLs total
    l = by_street["114 S McNab Pkwy"]
    assert len(l["photos"]) == 1
    assert l["photos"][0].startswith("https://mcusercontent.com/")
    # Listings with no PICTURES link in the HTML just get none -- never a guess.
    no_pics = [s for s, l in by_street.items() if not l["pictures_url"]]
    assert all(by_street[s]["photos"] == [] for s in no_pics)


def test_photos_absent_without_html():
    # A source with no HTML twin (older-style JSON export) parses fine, just with no photos.
    parsed = parse_text("123 W Alpha St, Phoenix, AZ 85007\nPrice: $150,000\n")
    assert parsed["listings"][0]["photos"] == []


def test_basic_house(by_street):
    l = by_street["114 S McNab Pkwy"]
    assert (l["price"], l["beds"], l["baths"], l["sqft"], l["lot_sqft"], l["year_built"]) == (
        74900, 4, 1, 1080, 8277, 1954)
    assert l["zip"] == "85631" and l["occupancy"] == "vacant"
    assert l["coe"] == "09/28/2026" and l["access"] == "Contact for Access"
    assert l["details"]["Roof"] == "Needs Replacement" and "needs_work" in l["flags"]
    assert l["pictures_url"].startswith("https://us.list-manage.com/")


def test_zip_on_following_line(by_street):
    l = by_street["3441 E Taylor St"]
    assert l["zip"] == "85008" and l["arv"] == 465000 and l["sqft"] == 2150
    assert l["occupancy"] == "occupied"


def test_two_address_listing_and_sections(by_street):
    l = by_street["199 & 185 E Morelos St"]
    assert l["price"] == 368000 and l["type"] == "multi"
    assert l["sqft"] == 1536 + 252 and "combined_sqft" in l["flags"]
    assert l["apn"] == "303-17-135 & 303-17-134"
    assert "needs_work" not in l["flags"]  # 'Replaced with Used Unit' is not a defect


def test_two_homes_combined(by_street):
    l = by_street["22044 N 88th Ave"]
    assert l["sqft"] == 1860 + 1500 and l["type"] == "multi" and l["beds"] == 4


def test_land_has_no_sqft_and_is_land(by_street):
    for s in ("421 Hinton St", "7836 E Sugarloaf St", "1218 S 13th Ave", "10826 S 25th Dr"):
        assert by_street[s]["type"] == "land" and not by_street[s]["sqft"]


def test_arv_variants(by_street):
    r = by_street["14763 E Circle M Ranch Pl"]
    assert (r["arv"], r["arv_high"]) == (820000, 1350000)
    p = by_street["1109 N 27th Dr"]
    assert p["arv"] == 330000 and "arv_is_floor" in p["flags"]


def test_types(by_street):
    assert by_street["635 E Mohave Cir"]["type"] == "manufactured"
    assert by_street["7038 W Karen Lee Ln"]["type"] == "manufactured"
    assert by_street["1701 W Tuckey Ln, Unit 210"]["type"] == "condo"
    assert by_street["10757 N 74th St Unit 1036"]["type"] == "condo"
    assert by_street["2929 E Eberle Ln"]["type"] == "sfr"  # 'Zoning: Multi-Family' is zoning, not type


def test_uncertain_sqft_flagged(by_street):
    assert "sqft_uncertain" in by_street["6437 E Calle Bellatrix"]["flags"]
    assert "sqft_uncertain" in by_street["2423 W Mineral Rd"]["flags"]


def test_den_and_wrapped_notes(by_street):
    assert by_street["1540 S Carpenter Ln"]["beds"] == 2 and "plus_den" in by_street["1540 S Carpenter Ln"]["flags"]
    note = by_street["10839 E Sonrisa Ave"]["details"]["Notes"]
    assert "walled-off room in the living area" in note  # wrapped line re-joined


def test_occupancy_at_close(by_street):
    assert by_street["1216 E Hess Ave"]["occupancy"] == "occupied"
    assert any("Vacate" in n for n in by_street["1216 E Hess Ave"]["notes"])


@pytest.mark.parametrize("raw,expected", [
    ("13,068 Sq Ft (0.30 Acres)", 13068), ("4.40 Acres / 191,664 Sq Ft", 191664),
    ("5 Acres", 217800), ("~9,853 Sq Ft", 9853), ("0.15 Acres", 6534), ("3 Acres (129,594 Sq Ft)", 129594),
    ("1.04 Acres", 45302), (None, None),
])
def test_lot_sqft(raw, expected):
    assert parse_lot_sqft(raw) == expected


# ---- second real email (pulled through the Gmail connector): formats the first sample did not have
EARLY = Path(__file__).parent / "fixtures" / "gmail_0914_early.json"


@pytest.fixture(scope="module")
def early():
    import json
    from dealradar.parser import parse_text
    r = parse_text(json.loads(EARLY.read_text())["text"])
    return r, {l["street"]: l for l in r["listings"]}


def test_early_count_matches_price_lines(early):
    import json, re
    text = json.loads(EARLY.read_text())["text"]
    assert len(early[0]["listings"]) == len(re.findall(r"(?m)^Price:", text)) == 36


def test_address_split_across_lines_by_a_link(early):
    l = early[1]["854 S San Marcos Dr, Bldg 1 Unit B"]
    assert (l["city"], l["zip"], l["price"], l["sqft"]) == ("Apache Junction", "85120", 189900, 1438)
    # ...and it did not leak into the neighbouring listing
    assert early[1]["115 W Ohio St"]["price"] == 180000 and early[1]["115 W Ohio St"]["sqft"] == 1669


def test_colon_sections_use_sellers_total(early):
    l = early[1]["4102 E Palm Ln"]
    assert l["sqft"] == 2673 and l["beds"] == 4 and "combined_sqft" in l["flags"]
    assert [p["sqft"] for p in l["sqft_parts"]] == [1346, 631, 696]


def test_affixed_and_mobile_are_manufactured(early):
    assert early[1]["10123 S 31st Dr"]["type"] == "manufactured"
    assert early[1]["31015 N 229th Ave"]["type"] == "manufactured"


def test_vacant_lot_variants_are_land(early):
    for s in ("208 E Centre Ave", "7826 S 220th Ln", "310 S Peak Rd"):
        assert early[1][s]["type"] == "land"
