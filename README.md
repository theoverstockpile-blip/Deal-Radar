# Deal Radar

Reads wholesaler emails (built around the Gentry / Mailchimp "Morning Wholesale Updates" blast), parses every
listing, ranks by **cost per square foot** against other recent, nearby deals, and alerts on the ones that
fit the buy-box. Phone-installable web app (PWA) with a **Contact wholesaler** button on every alert.

## Try it in 30 seconds (no setup)
Open `dist/deal-radar-demo.html` in a browser. It contains the real parsed data from two real emails
(76 deals, 8 hot) and a simulated scan. Tap **Scan now**.

## Run the real app
    pip install -r requirements.txt
    python run.py                 # http://localhost:5050
Drop `.eml` or `.json` email exports into `inbox/` (default source), or use Gmail (below).

Env vars: `DEALRADAR_INBOX` (folder read when no email is connected), `DEALRADAR_DB`, `DEALRADAR_TOKEN`
(shared secret, sent as `X-Token`; set it if the server is reachable from the internet),
`DEALRADAR_DEV=1` (enables reset endpoint), `DEALRADAR_VAPID` (push key file, auto-created), `PORT`.

## Deploy (put it on the internet so Mason can use it)
This repo is ready for Railway or Render -- both auto-detect Python and use the included `Procfile`
(`gunicorn -w 1 ... run:app`; one worker on purpose, so the daily scan scheduler only runs once).

1. Push this repo to GitHub (already done if you're reading this from there).
2. On [railway.app](https://railway.app) (or [render.com](https://render.com)): sign in with GitHub,
   **New Project -> Deploy from GitHub repo**, pick this repo.
3. Add a **persistent volume** (Railway: Settings -> Volumes; Render: a paid disk) mounted at `/data`,
   so deals and push subscriptions survive a redeploy -- SQLite/the push key live on local disk
   otherwise and reset every time the app redeploys.
4. Set env vars: `DEALRADAR_DB=/data/dealradar.db`, `DEALRADAR_VAPID=/data/vapid_private.pem`,
   `DEALRADAR_TOKEN=<a random string you make up>` (keeps the app private -- see below). The email
   connection itself is set up in Settings after deploying, not as an env var -- see "Email setup" below.
5. Deploy. You'll get a `https://...up.railway.app` (or `.onrender.com`) URL -- that's the real app.
6. Open `https://<your-url>/?token=<the DEALRADAR_TOKEN you set>` once on Mason's phone. It saves the
   token locally and strips it from the address bar. Then **Share -> Add to Home Screen** (iPhone) or
   the install prompt (Android) turns it into an app icon.

## What it does
- **Daily scan**: server-side scheduler, default 11:30 AM America/Phoenix (the blast lands ~11:10-11:18).
  Never early, at most 5 minutes late (`scan_window_min`). If the server was off it catches up. If no new
  email is found it retries 3 times at 5-minute gaps (up to 2 h).
- **Scoring**: price / sq ft. Comps are appraiser-style: same class (house / condo / manufactured), from
  deals no older than `lookback_days` (150, ~5 months), preferring ones within `radius_mi` (0.5mi -- the
  appraisal-ideal ring). When that ring has fewer than `min_comps` (5), the search widens outward,
  closest-first, up to `max_radius_mi` (25mi) until it has enough. Every comp carries its distance and
  whether it fell inside the ideal ring, so the UI can show both groups. "Low" = at least `low_ppsf_pct`
  (15%) below the median of whichever comps were used.
  - **Geocoding**: offline by default (`dealradar/geo.py`'s ZIP-centroid table, ~1-3mi accurate), which
    means `radius_mi` mostly collapses to "same ZIP." Turn on **Precise location lookup**
    (`geocode_online` setting) once the server has internet access to use the free US Census geocoder
    instead -- street-level, cached per address in `kv` so each listing is only geocoded once. This
    sandbox has no outbound access to that API, so it's untested end-to-end here; it needs verifying
    once deployed somewhere with real internet access.
    - Settings > **Location accuracy** also lets you switch the `geocode_provider` to **Google Maps**
      as a paid backup for the rare address Census can't find. The checklist there walks through
      creating a Google Cloud account, enabling the Geocoding API, turning on billing, and creating an
      API key, with a direct link for each step; progress is saved (`geocode_setup_progress`) and the
      key is stored in `geocode_api_key`. Falls back to Census, then offline, if the key is missing or
      the paid call fails for any reason -- a scan is never blocked on it.
- **Photos**: a horizontal scroll strip of the listing's real photos appears under the Pictures/Map links,
  on both the deal card and the detail page. These come straight out of the wholesaler email itself (its
  HTML part embeds them; the Google Drive album is just where the email links out for the full set), so
  no separate photo service or API key is needed. Not every listing has its own photos in the email --
  those just show no strip. 42 of 50 listings in the sample email have them (1-3 photos each).
- **Hot** = inside the buy-box AND low $/sq ft -> push notification with **Contact wholesaler**. Everything
  else goes into the daily digest. Price cuts on re-sent listings are detected and re-alert.
- **Contact wholesaler** opens the phone dialer (`tel:`) after a confirm sheet; there is also a Text button.
  It calls the wholesaler who sent the deal sheet (e.g. "Text Spencer 480-283-4425"), not the homeowner --
  that's correct for how wholesale deals work, since the wholesaler controls access and fields buyer
  inquiries. The parser checks each listing's own block for a contact first and only falls back to the one
  email-wide number when a listing has none of its own, so a future email that mixes listings from more
  than one source won't misattribute a contact.
- Buy-box (Settings): types, max price, min beds, min sq ft, occupied OK. **The defaults are placeholders
  until Mason's real criteria are entered.**

## Email setup
Settings > **Connect your email**: no Google Cloud project or OAuth consent screen needed. Works with
Gmail, Outlook/Office365, Yahoo, iCloud, AOL, or anything else that supports IMAP + app passwords.
1. Turn on 2-Step Verification on the account (most accounts already have this).
2. Create an **app password** for it (Gmail: myaccount.google.com/apppasswords) -- a 16-character code,
   separate from the real password.
3. Enter the email address and that app password in Settings, then tap **Test connection**.
4. That's it -- the next scheduled scan (and every one after) reads that inbox automatically. No
   redeploy needed; saving new credentials in Settings takes effect on the very next scan.

Recommended either way: have Mason auto-forward the blast to a dedicated inbox (a Gmail filter that
forwards it) rather than connecting his main inbox, so this app only ever sees that one kind of email.

Verified end-to-end against a real Gmail inbox: a live test pulled two real forwarded wholesaler
blasts straight out of a real Gmail account over this exact scan pipeline and found the same 76
deals / 8 hot as the sample fixtures below -- see `tests/test_sources.py` for the mocked-IMAP
version of that same flow (no real network in the test suite itself).

## Push notifications
Needs HTTPS (or localhost) and `pywebpush`. VAPID keys are generated on first run. On iPhone the app must
first be added to the Home Screen.

## Tests
    pytest        # 89 tests: parser, scoring, scan flow, scheduler, API, geocoding, photos, email (mocked IMAP)
    python tools/build_demo.py   # rebuilds dist/deal-radar-demo.html

## Known limitations
- A page cannot silently place a call; the OS always asks the user to confirm.
- Comps come only from the deals in the emails, so very thin pools (fewer than `min_comps` total,
  even out to `max_radius_mi`) still show "Only N nearby deal(s) to compare".
- Distances use ZIP-code centres (about 1-3 mi accurate) unless `geocode_online` is turned on and the
  server has internet access -- untested end-to-end here, see the Scoring section above.
- Not verified here (outside the mocked test suite): real push delivery to a phone. The email
  connection itself has been verified against a real Gmail inbox (see "Email setup" above).
  SMS/email alerts are not built yet.
- Photos need the email's HTML part. Real `.eml` files (and the real Gmail API source) always have one, so
  this isn't a concern there -- but a `.json` email export with no `html` field (an older-style export)
  will parse fine, it'll just show no photos for those listings.
