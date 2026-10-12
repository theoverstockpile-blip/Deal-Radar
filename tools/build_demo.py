"""Build dist/deal-radar-demo.html: the real interface running on embedded results from the real pipeline.

Runs the actual parser + scoring on the two real emails, embeds the output, and inlines CSS/JS so the file works
offline with no server (used for the shareable click-through prototype).
"""
import json, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dealradar.scanner import run_scan
from dealradar.server import deal_view
from dealradar.scoring import analyze
from dealradar.sources import FolderSource
from dealradar.store import Store

NOW = datetime(2026, 9, 14, 18, 30, tzinfo=timezone.utc)  # the morning both emails arrived, 11:30 Arizona time


def build_data():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "in").mkdir()
    shutil.copy(ROOT / "tests/fixtures/sample.eml", tmp / "in/sample.eml")
    shutil.copy(ROOT / "tests/fixtures/gmail_0914_early.json", tmp / "in/early.json")
    store = Store(str(tmp / "d.db"))
    run_scan(store, FolderSource(tmp / "in"), now=NOW)
    s = store.get_settings()
    # Bake the candidate pool out to a generous 50mi (beyond the real 25mi max_radius_mi default)
    # so the demo's sliders have real data to reveal even when dragged past the shipped default --
    # the frontend's reclassify() redoes the same closest-first / in-radius-first selection
    # client-side as the radius, min_comps, max_radius_mi etc. sliders move.
    scored = analyze(store.deals_in_window(30, NOW), {**s, "max_radius_mi": 50})
    deals = []
    for d in scored:
        dv = deal_view(d)
        dv["comps"] = d["all_comps"]
        dv["first_seen"] = None
        deals.append(dv)
    return {"settings": s, "deals": deals, "next_scan": "2026-09-15T11:30-07:00"}


def standalone(data) -> str:
    css = (ROOT / "static/styles.css").read_text()
    js = (ROOT / "static/app.js").read_text()
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Deal Radar prototype</title><meta name="theme-color" content="#17694a"><style>{css}</style></head>
<body><div id="app"><header class="top"><h1 id="title">Deal Radar</h1><span class="sub" id="sub"></span></header>
<main id="view" aria-live="polite"></main><div class="demo" id="demo-note" hidden>Interactive prototype with sample data from a real wholesale email.</div></div>
<div class="tabs" id="tabs"></div>
<script>window.DEALRADAR_DEMO={blob};</script><script>{js}</script></body></html>"""


if __name__ == "__main__":
    data = build_data()
    out = ROOT / "dist/deal-radar-demo.html"
    out.write_text(standalone(data))
    (ROOT / "dist/demo-data.json").write_text(json.dumps(data))
    print(f"{len(data['deals'])} deals, {sum(d['hot'] for d in data['deals'])} hot -> {out} ({out.stat().st_size/1024:.0f} KB)")
