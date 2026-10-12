"""Parse a Gentry-style "Morning Wholesale Updates" email into structured listings.

The blast is a Mailchimp email. Its plain-text part is a stack of blocks, one per
property, each starting with an address line and followed by "Key: value" lines.
This module turns that text into dicts. Listing fields come from the text part only.

The HTML twin isn't a plain duplicate, though -- it's the only part that carries the
actual photos (the text part just has a "PICTURES" link to an external album most people
never click). See `_extract_photos` for how those get pulled out and matched back to
the right listing.
"""
from __future__ import annotations

import hashlib
import re
from email import policy
from email.parser import BytesParser
from html import unescape
from urllib.parse import urlparse

# --------------------------------------------------------------------------- regexes
URL_LINE = re.compile(r"^<(https?://[^>]+)>[&,]?$")
ADDR = re.compile(
    r"^(?P<street>\d.*?),\s*(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*AZ\b\s*(?P<zip>\d{5})?\s*$"
)
ZIP_ONLY = re.compile(r"^\d{5}$")
CITY_ZIP = re.compile(r"^(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*AZ\b\s*(?P<zip>\d{5})?\s*$")
STREET_ONLY = re.compile(r"^\d[^:]*$")  # starts with a digit, no colon: '854 S San Marcos Dr, Bldg 1 Unit B'
HEADER_SECTION = re.compile(r"^(?P<name>[A-Za-z][A-Za-z ()/&'\-]{2,40}):$")  # 'Main House:' (no value)
KV = re.compile(r"^(?P<k>[A-Za-z][A-Za-z0-9 /&#().'\-]{0,40}?):\s*(?P<v>.+)$")
SECTION = re.compile(
    r"^(Main House|Main Home|Guest House|Second Home(?: \(ADU\))?|Addition|Studio Unit)$", re.I
)
PHONE = re.compile(r"(\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4})")
MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
END_MARKERS = ("wholesale questions?", "*copyright")

KEY_MAP = {
    "price": "price",
    "arv": "arv",
    "beds": "beds",
    "baths": "baths",
    "sq ft": "sqft",
    "lot size": "lot",
    "year built": "year_built",
    "property type": "type_raw",
    "hoa": "hoa",
    "emd": "emd",
    "coe": "coe",
    "access": "access",
    "condition": "condition",
    "apn": "apn",
    "apns": "apn",
    "parcel #": "apn",
    "zoning": "zoning",
    "total sq ft": "sqft_total",
}
OCCUPANCY = [
    (re.compile(r"^seller will vacate", re.I), "vacates_at_close"),
    (re.compile(r"^vacant (at|by)", re.I), "vacant_at_close"),
    (re.compile(r"^vacant$", re.I), "vacant"),
    (re.compile(r"tenant occupied|^occupied|lease in place", re.I), "occupied"),
]
DISTRESS = re.compile(r"needs?\b|fire|renovation|as-is|\breplacement\b|repair|teardown|scrape", re.I)


# --------------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    return (
        text.replace(" ", " ")
        .replace("–", "-")
        .replace("—", "-")
        .replace("×", "x")
        .replace("\r", "")
    )


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    return float(m.group(0).replace(",", "")) if m else None


def _money_list(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in MONEY.findall(s)]


def parse_lot_sqft(s: str | None) -> float | None:
    """'13,068 Sq Ft (0.30 Acres)' / '4.40 Acres / 191,664 Sq Ft' / '5 Acres' / '~9,853 Sq Ft'."""
    if not s:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*sq\.?\s*ft", s, re.I)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"([\d.]+)\s*-?\s*acres?", s, re.I)
    if m:
        return round(float(m.group(1)) * 43560)
    return None


def normalize_type(raw: str | None, has_beds: bool, has_sqft: bool, has_lot: bool) -> str:
    r = (raw or "").lower()
    if "manufactured" in r or "mobile" in r or "affixed" in r:
        return "manufactured"
    if "condo" in r or "townhome" in r:
        return "condo"
    if "multi" in r or "two homes" in r or "duplex" in r or "triplex" in r:
        return "multi"
    if "land" in r:
        return "land"
    if any(x in r for x in ("single family", "sfr", "new construction")):
        return "sfr"
    if not has_beds and not has_sqft and has_lot:
        return "land"
    return "sfr"


def listing_id(street: str, zip_code: str | None, city: str) -> str:
    key = re.sub(r"[^a-z0-9]+", " ", f"{street} {city} {zip_code or ''}".lower()).strip()
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def _parse_beds(v: str) -> tuple[float | None, bool]:
    return _num(v), "den" in v.lower() or "office" in v.lower()


# --------------------------------------------------------------------------- block parsing
def _tokenize(text: str) -> list[tuple[str, str]]:
    """Text -> [('blank',''), ('url', href), ('text', line), ...]; stops at the footer."""
    out: list[tuple[str, str]] = []
    for raw in _clean(text).split("\n"):
        line = raw.strip()
        if not line:
            out.append(("blank", ""))
            continue
        if any(line.lower().startswith(m) for m in END_MARKERS):
            break
        m = URL_LINE.match(line)
        out.append(("url", m.group(1)) if m else ("text", line))
    return out


def _split_blocks(tokens: list[tuple[str, str]]):
    """Yield (header_tokens, [address_match, block_tokens...]). Handles zip-on-next-line and '199 & 185 ...'."""
    header: list[tuple[str, str]] = []
    blocks: list[dict] = []
    i, n = 0, len(tokens)
    while i < n:
        kind, val = tokens[i]
        m = ADDR.match(val) if kind == "text" else None
        if not m and kind == "text" and STREET_ONLY.match(val):
            # '854 S San Marcos Dr, Bldg 1 Unit B' <maps link>, 'Apache Junction, AZ 85120'
            j = i + 1
            while j < n and tokens[j][0] == "url":
                j += 1
            if j > i + 1 and j < n and tokens[j][0] == "text":
                cz = CITY_ZIP.match(tokens[j][1])
                if cz:
                    m = ADDR.match(f"{val}, {cz.group('city')}, AZ" + (f" {cz.group('zip')}" if cz.group("zip") else ""))
                    if m:
                        i = j  # consume the city/zip line
        if not m:
            (blocks[-1]["tokens"] if blocks else header).append(tokens[i])
            i += 1
            continue
        street, city, zip_code = m.group("street"), m.group("city").strip(), m.group("zip")
        if not zip_code:  # 'Phoenix, AZ' + maps link + ' 85008'
            j = i + 1
            while j < n and tokens[j][0] in ("url", "blank"):
                j += 1
            if j < n and ZIP_ONLY.match(tokens[j][1]):
                zip_code = tokens[j][1]
                i = j  # consume the zip line
        # '199' <maps> '185 E Morelos St, ...' -> '199 & 185 E Morelos St'
        if blocks:
            prev = blocks[-1]["tokens"]
            k = len(prev) - 1
            while k >= 0 and prev[k][0] in ("url", "blank"):
                k -= 1
            if k >= 0 and re.fullmatch(r"\d{1,5}", prev[k][1]):
                street = f"{prev[k][1]} & {street}"
                del prev[k:]
        blocks.append({"street": street, "city": city, "zip": zip_code, "tokens": []})
        i += 1
    return header, blocks


def _parse_block(b: dict) -> dict:
    fields: dict = {}
    details: dict = {}
    notes: list[str] = []
    sections: list[dict] = []
    occupancy = None
    pictures = None
    cur_section: dict | None = None
    prev_text = None
    last_kv: tuple[dict, str] | None = None  # (dict, key) of the last plain key/value line

    for kind, val in b["tokens"]:
        if kind == "blank":
            cur_section = None
            last_kv = None
            continue
        if kind == "url":
            if prev_text and prev_text.upper() == "PICTURES":
                pictures = val
            continue
        prev_text = val
        if val.upper() == "PICTURES":
            continue
        hs = HEADER_SECTION.match(val)
        if SECTION.match(val) or hs:
            cur_section = {"name": (hs.group("name") if hs else val), "beds": None, "baths": None, "sqft": None, "notes": []}
            sections.append(cur_section)
            continue
        kv = KV.match(val)
        if kv:
            k, v = kv.group("k").strip().lower(), kv.group("v").strip()
            if cur_section is not None and k in ("beds", "baths", "sq ft"):
                cur_section[{"beds": "beds", "baths": "baths", "sq ft": "sqft"}[k]] = _num(v)
                continue
            if k in KEY_MAP:
                fields.setdefault(KEY_MAP[k], v)
                last_kv = (fields, KEY_MAP[k])
            else:
                details[kv.group("k").strip()] = v
                last_kv = (details, kv.group("k").strip())
            continue
        if last_kv and val[:1].islower():  # wrapped continuation of the previous "Key: value" line
            last_kv[0][last_kv[1]] += " " + val
            continue
        hit = next((o for rx, o in OCCUPANCY if rx.search(val)), None)
        if hit:
            occupancy = occupancy or hit
            if "at" in val.lower() or "by" in val.lower() or "vacate" in val.lower():
                notes.append(val)
            continue
        (cur_section["notes"] if cur_section else notes).append(val)

    # --- numeric fields
    prices = _money_list(fields.get("price", ""))
    arv_vals = _money_list(fields.get("arv", ""))
    beds, den = _parse_beds(fields["beds"]) if "beds" in fields else (None, False)
    baths = _num(fields.get("baths"))
    sqft = _num(fields.get("sqft"))
    sqft_parts: list[dict] = []
    flags: list[str] = []

    if sections:  # multi-structure listing: main + addition/guest/ADU
        parts = [s for s in sections if s["sqft"]]
        if parts:
            sqft_parts = [{"name": s["name"], "sqft": s["sqft"]} for s in parts]
            sqft = _num(fields.get("sqft_total")) or sum(s["sqft"] for s in parts)  # seller's total wins
            flags.append("combined_sqft")
        first = sections[0]
        beds = beds if beds is not None else first["beds"]
        baths = baths if baths is not None else first["baths"]
        for s in sections:
            notes.extend(f"{s['name']}: {x}" for x in s["notes"])

    lot_sqft = parse_lot_sqft(fields.get("lot"))
    type_norm = normalize_type(fields.get("type_raw"), beds is not None, bool(sqft), bool(lot_sqft))
    if not fields.get("type_raw") and type_norm == "sfr" and re.search(r"\b(unit|apt|#)\s*\w+", b["street"], re.I):
        type_norm = "condo"  # 'Unit 1036' with no stated type is almost always a condo/townhome
    year = int(_num(fields.get("year_built")) or 0) or None

    blob = " ".join(notes + [fields.get("condition", "")] + [f"{k} {v}" for k, v in details.items()])
    if re.search(r"not reflected in records", blob, re.I) or re.search(r"includes added", fields.get("sqft", ""), re.I):
        flags.append("sqft_uncertain")
    if "den" in fields.get("beds", "").lower() or den:
        flags.append("plus_den")
    if DISTRESS.search(blob):
        flags.append("needs_work")
    if arv_vals and "+" in fields.get("arv", ""):
        flags.append("arv_is_floor")

    street, city, zip_code = b["street"], b["city"], b["zip"]
    return {
        "id": listing_id(street, zip_code, city),
        "street": street,
        "city": city,
        "state": "AZ",
        "zip": zip_code,
        "address": f"{street}, {city}, AZ" + (f" {zip_code}" if zip_code else ""),
        "apn": fields.get("apn"),
        "price": prices[0] if prices else None,
        "arv": arv_vals[0] if arv_vals else None,
        "arv_high": arv_vals[1] if len(arv_vals) > 1 else None,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "sqft_parts": sqft_parts,
        "lot_sqft": lot_sqft,
        "year_built": year,
        "type": type_norm,
        "type_raw": fields.get("type_raw"),
        "occupancy": occupancy,
        "access": fields.get("access"),
        "emd": (_money_list(fields.get("emd", "")) or [None])[0],
        "coe": fields.get("coe"),
        "hoa": fields.get("hoa"),
        "condition": fields.get("condition"),
        "zoning": fields.get("zoning"),
        "details": details,
        "notes": notes,
        "flags": flags,
        "pictures_url": pictures,
        "maps_url": "https://www.google.com/maps/search/"
        + re.sub(r"\s+", "+", f"{street}, {city}, AZ {zip_code or ''}".strip()),
    }


def _find_contact(text: str) -> dict | None:
    """Look for 'Text/Call/Reach out to <Name> <phone>' in a chunk of text. None if there isn't one."""
    m = re.search(r"(?i:text|call|reach out to)\s+([A-Z][a-z]+)\s+" + PHONE.pattern, text)
    ph = PHONE.search(text)
    if not ph:
        return None
    digits = re.sub(r"\D", "", ph.group(1))
    return {"name": m.group(1) if m else None, "phone": f"+1{digits}", "display": f"{digits[0:3]}-{digits[3:6]}-{digits[6:]}"}


# --------------------------------------------------------------------------- photos (from the HTML part)
PICTURES_ANCHOR = re.compile(r'<a\s+[^>]*href="[^"]*"[^>]*>\s*PICTURES\s*</a>', re.I)
IMG_TAG = re.compile(r'<img\s+[^>]*\bsrc="([^"]+)"', re.I)
# Mailchimp serves this wholesaler's actual listing photos from mcusercontent.com; the only other
# image host in these emails is gallery.mailchimp.com, used for the header/footer banner art.
DECOR_HOST = "gallery.mailchimp.com"
PHOTO_HOST = "mcusercontent.com"


def _extract_photos(html: str) -> list[list[str]]:
    """Group the email's real property photos by listing, in document order.

    Every listing that has photos ends its block with a "PICTURES" link to the full album (most
    people wholesaling by email never click it). This walks the HTML and, for each such link,
    collects the <img> tags that appeared since the previous one -- those are that listing's own
    photos. Decorative banner images (gallery.mailchimp.com) are filtered out; only the real photo
    CDN (mcusercontent.com) counts. Listings with no PICTURES link of their own get an empty list
    here and simply show none.
    """
    if not html:
        return []
    groups: list[list[str]] = []
    pos = 0
    for m in PICTURES_ANCHOR.finditer(html):
        chunk = html[pos:m.start()]
        urls = []
        for img_m in IMG_TAG.finditer(chunk):
            url = unescape(img_m.group(1))
            host = urlparse(url).netloc
            if PHOTO_HOST in host and DECOR_HOST not in host:
                urls.append(url)
        groups.append(urls)
        pos = m.end()
    return groups


# --------------------------------------------------------------------------- public API
def parse_text(text: str, html: str | None = None) -> dict:
    """Parse the plain-text body. Returns {'contact': {...}|None, 'listings': [...]}.

    Most wholesaler blasts have exactly one contact for the whole email (a banner like "Text Spencer
    480-283-4425"), so each listing normally just inherits that one. But if an email ever mixes
    listings from more than one source -- a co-posted deal with its own "Call Jordan 602-..." right
    next to that listing -- we don't want every listing in the email to wrongly get the first
    contact found. So each listing's own block is checked FIRST for a contact near it; only when a
    listing has none of its own does it fall back to the one email-wide contact.

    `html` is optional -- the HTML twin of the email, used only to pull real photos (see
    `_extract_photos`). Without it (an older JSON export, say) listings just get no photos.
    """
    tokens = _tokenize(text)
    header, blocks = _split_blocks(tokens)
    head_text = " ".join(v for k, v in header if k == "text")
    full = _clean(text)

    contact = _find_contact(full)  # email-wide fallback; also the field other code reads as "the" contact
    listings = []
    for b in blocks:
        l = _parse_block(b)
        block_text = " ".join(v for k, v in b["tokens"] if k == "text")
        l["contact"] = _find_contact(block_text) or contact
        l["photos"] = []
        listings.append(l)

    # The PICTURES links in the HTML appear in the same order as, and one-for-one with, the
    # listings that have a pictures_url from the text part -- so zip them back together
    # positionally. If a future email format doesn't line up (a different count), this just
    # stops matching early rather than guessing; listings past that point keep photos=[].
    if html:
        with_pics = [l for l in listings if l.get("pictures_url")]
        for l, urls in zip(with_pics, _extract_photos(html)):
            l["photos"] = urls

    return {"contact": contact, "header": head_text, "listings": listings}


def parse_eml_bytes(raw: bytes) -> dict:
    """Parse a raw .eml. Adds message metadata."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    body = msg.get_body(preferencelist=("plain",))
    if body is None:
        raise ValueError("email has no text/plain part")
    html_part = msg.get_body(preferencelist=("html",))
    out = parse_text(body.get_content(), html_part.get_content() if html_part is not None else None)
    out.update(
        message_id=(msg["Message-ID"] or "").strip("<> "),
        subject=str(msg["Subject"] or ""),
        sender=str(msg["From"] or ""),
        date=str(msg["Date"] or ""),
    )
    return out
