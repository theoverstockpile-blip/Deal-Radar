'use strict';
/* Deal Radar - phone-first PWA. One file, no build step.
   Runs against the live API, or (window.DEALRADAR_DEMO set) against embedded sample data. */

const DEMO = window.DEALRADAR_DEMO || null;
const S = { status: null, deals: [], alerts: [], filter: 'box', sort: 'discount', lastAlertId: 0, online: true };

/* ------------------------------------------------------------------ helpers */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = n => (n == null ? '—' : '$' + Math.round(n).toLocaleString('en-US'));
const dollars = n => (n == null ? '—' : '$' + Math.round(n));
const num = n => (n == null ? '—' : Number(n).toLocaleString('en-US'));
const safeUrl = u => (/^https:\/\//i.test(u || '') ? u : null);
const safeTel = t => (/^tel:\+?\d{7,15}$/.test(t || '') ? t : null);
const safeSms = t => (/^sms:\+?\d{7,15}\?&body=[\w%.~-]*$/.test(t || '') ? t : null);
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };
const OCC = { vacant: 'Vacant', vacant_at_close: 'Vacant at close', occupied: 'Occupied', vacates_at_close: 'Seller vacates at close' };
const TYPE = { sfr: 'House', multi: 'Multi-family', condo: 'Condo', manufactured: 'Manufactured', land: 'Land' };
const ICON = {
  phone: '<svg viewBox="0 0 24 24"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>',
  msg: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7zM10 20a2 2 0 0 0 4 0"/></svg>',
  list: '<svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>',
  back: '<svg viewBox="0 0 24 24" style="width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round"><path d="M15 5l-7 7 7 7"/></svg>',
};
const tz = () => S.status?.settings?.timezone || 'America/Phoenix';
function when(iso) {
  if (!iso) return '';
  const d = new Date(iso), o = { timeZone: tz() };
  const day = x => x.toLocaleDateString('en-CA', o);
  const t = d.toLocaleTimeString('en-US', { ...o, hour: 'numeric', minute: '2-digit' });
  const now = new Date(), tomorrow = new Date(now.getTime() + 864e5);
  if (day(d) === day(now)) return `today ${t}`;
  if (day(d) === day(tomorrow)) return `tomorrow ${t}`;
  if (day(d) === day(new Date(now.getTime() - 864e5))) return `yesterday ${t}`;
  return d.toLocaleDateString('en-US', { ...o, weekday: 'short', month: 'short', day: 'numeric' }) + ', ' + t;
}
const discLabel = d => (d.discount_pct == null ? null : `${Math.round(Math.abs(d.discount_pct))}% ${d.discount_pct >= 0 ? 'below' : 'above'} area`);
function toast(msg, ms = 2600) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}

/* ------------------------------------------------------------------ live API */
(function grabToken() {
  try { const u = new URL(location.href), t = u.searchParams.get('token'); if (t) { store.set('dr_token', t); u.searchParams.delete('token'); history.replaceState(null, '', u.pathname + u.search + u.hash); } } catch { /* ignore */ }
})();
async function j(method, url, body) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-Token': store.get('dr_token') || '' }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).error || m; } catch { /* not json */ } throw new Error(`${r.status} ${m}`); }
  return r.json();
}
const liveApi = {
  status: () => j('GET', '/api/status'), deals: () => j('GET', '/api/deals'), alerts: (after = 0) => j('GET', '/api/alerts?after=' + after),
  scan: () => j('POST', '/api/scan'), putSettings: p => j('PUT', '/api/settings', p), contacted: id => j('POST', `/api/deals/${id}/contacted`),
  readAlerts: () => j('POST', '/api/alerts/read', {}), subscribe: s => j('POST', '/api/push/subscribe', s), reset: () => j('POST', '/api/dev/reset'),
  testEmail: () => j('POST', '/api/email/test', {}),
};

/* ------------------------------------------------------------------ demo API (embedded data; mirrors backend rules) */
const median = a => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
function boxMisses(d, b) {
  const w = [];
  if (!b.types.includes(d.type)) w.push(`type is ${d.type}`);
  if (d.price && d.price > b.max_price) w.push(`price above ${money(b.max_price)}`);
  if (d.type !== 'land') {
    if (d.beds != null && d.beds < b.min_beds) w.push(`only ${d.beds} beds`);
    if (d.sqft != null && d.sqft < b.min_sqft) w.push(`only ${num(d.sqft)} sq ft`);
  }
  if (!b.allow_occupied && d.occupancy === 'occupied') w.push('occupied');
  return w;
}
function reclassify(d, s) {
  const r = { ...d, median_ppsf: null, discount_pct: null, rank: null, pool_size: null, low_ppsf: false, n_in_radius: 0 };
  // Mirrors dealradar/scoring.py's analyze(): prefer the appraisal-ideal radius_mi ring; only
  // widen past it, closest-first (capped at max_radius_mi), if that ring doesn't have min_comps.
  const candidates = (d.comps || []).filter(c => c.dist_mi <= s.max_radius_mi)
    .map(c => ({ ...c, in_radius: c.dist_mi <= s.radius_mi }))
    .sort((a, b) => a.dist_mi - b.dist_mi);
  const nInRadius = candidates.filter(c => c.in_radius).length;
  r.comps = nInRadius >= s.min_comps ? candidates.filter(c => c.in_radius) : candidates.slice(0, s.min_comps);
  r.n_comps = r.comps.length; r.n_in_radius = r.comps.filter(c => c.in_radius).length;
  if (d.comparable) {
    if (r.n_comps >= s.min_comps) {
      const med = median(r.comps.map(c => c.ppsf));
      r.median_ppsf = Math.round(med * 100) / 100; r.discount_pct = Math.round((med - d.ppsf) / med * 1000) / 10;
      r.rank = 1 + r.comps.filter(c => c.ppsf < d.ppsf).length; r.pool_size = r.n_comps + 1; r.low_ppsf = r.discount_pct >= s.low_ppsf_pct;
      r.compare_note = r.n_in_radius === r.n_comps ? null
        : `Only ${r.n_in_radius} within ${s.radius_mi}mi; widened to ${r.comps[r.comps.length - 1].dist_mi}mi to reach ${s.min_comps} comps`;
    } else r.compare_note = `Only ${r.n_comps} nearby deal(s) to compare (need ${s.min_comps})`;
  }
  r.box_misses = boxMisses(d, s.buy_box); r.in_buy_box = !r.box_misses.length;
  r.hot = r.in_buy_box && r.low_ppsf; r.tier = r.hot ? 'hot' : (r.low_ppsf ? 'cheap_outside_box' : 'other');
  r.value_tier = r.discount_pct == null ? 'unknown' : r.discount_pct >= s.low_ppsf_pct ? 'green' : r.discount_pct >= 0 ? 'yellow' : 'red';
  return r;
}
function demoApi() {
  const D = { scanTime: null, settings: JSON.parse(JSON.stringify(DEMO.settings)), scanned: false, alerts: [], contacted: {}, nextId: 1, lastScan: null };
  const deals = () => (D.scanned ? DEMO.deals.map(d => ({ ...reclassify(d, D.settings), first_seen: D.scanTime, contacted_at: D.contacted[d.id] || null })) : []);
  const text = d => {
    const farthest = d.comps.length ? d.comps[d.comps.length - 1].dist_mi : null;
    const compsDesc = d.n_in_radius === d.n_comps ? `${d.n_comps} deals within ${farthest} mi`
      : `${d.n_comps} deals, ${d.n_in_radius} within the ideal radius, out to ${farthest} mi`;
    return [`Low cost per sq ft: ${d.street}`, `${dollars(d.ppsf)}/sq ft vs ${dollars(d.median_ppsf)} area median (${Math.round(d.discount_pct)}% below, ${compsDesc}). Asking ${money(d.price)}.`];
  };
  return {
    status: async () => {
      const ds = deals();
      return { now: new Date().toISOString(), settings: D.settings, last_scan: D.lastScan, next_scan: DEMO.next_scan, unread: D.alerts.filter(a => !a.read_at).length,
        counts: { deals: ds.length, hot: ds.filter(d => d.hot).length }, push_key: null, push_subscriptions: 0, dev: true, demo: true };
    },
    deals: async () => deals(),
    alerts: async (after = 0) => D.alerts.filter(a => a.id > after).sort((a, b) => b.id - a.id),
    scan: async () => {
      if (D.scanned) return { emails_new: 0, deals_new: 0, hot: deals().filter(d => d.hot).length, message: 'No new wholesale email found' };
      D.scanned = true; D.scanTime = new Date().toISOString();
      const ds = deals(), hot = ds.filter(d => d.hot).sort((a, b) => b.discount_pct - a.discount_pct);
      hot.forEach(d => { const [title, body] = text(d); D.alerts.push({ id: D.nextId++, deal_id: d.id, kind: 'hot', title, body, created_at: new Date().toISOString(), read_at: null }); });
      const best = ds.filter(d => d.discount_pct != null).sort((a, b) => b.discount_pct - a.discount_pct).slice(0, 3);
      D.alerts.push({ id: D.nextId++, deal_id: null, kind: 'digest', title: 'Daily wholesale digest', created_at: new Date().toISOString(), read_at: null,
        body: `${ds.length} new deal(s) today, ${hot.length} hot. Best vs area: ` + best.map(d => `${d.street} (${Math.round(d.discount_pct)}% below, ${dollars(d.ppsf)}/sq ft)`).join('; ') });
      D.lastScan = { started_at: new Date().toISOString(), message: `1 new email(s), ${ds.length} new deal(s), ${hot.length} hot`, hot: hot.length };
      return { emails_new: 1, deals_new: ds.length, hot: hot.length, message: D.lastScan.message };
    },
    putSettings: async p => { for (const [k, v] of Object.entries(p)) { if (v && typeof v === 'object' && !Array.isArray(v)) Object.assign(D.settings[k], v); else D.settings[k] = v; } return D.settings; },
    contacted: async id => { D.contacted[id] = new Date().toISOString(); return { ok: true }; },
    readAlerts: async () => { D.alerts.forEach(a => { a.read_at = a.read_at || new Date().toISOString(); }); return { ok: true }; },
    subscribe: async () => ({ ok: true }),
    reset: async () => { D.scanned = false; D.scanTime = null; D.alerts = []; D.contacted = {}; D.lastScan = null; return { ok: true }; },
  };
}
const API = DEMO ? demoApi() : liveApi;

/* ------------------------------------------------------------------ data refresh */
async function refresh() {
  try {
    const [status, deals, alerts] = await Promise.all([API.status(), API.deals(), API.alerts(0)]);
    S.status = status; S.deals = deals; S.alerts = alerts; S.online = true;
    S.lastAlertId = Math.max(S.lastAlertId, ...alerts.map(a => a.id), 0);
  } catch (e) { S.online = false; S.error = String(e.message || e); }
  updateBadge();
}
function updateBadge() { const b = $('#badge'); if (!b) return; const n = S.status?.unread || 0; b.textContent = n > 99 ? '99+' : n; b.hidden = !n; }

/* ------------------------------------------------------------------ components */
const callLink = (d, cls = 'btn primary block', label = 'Contact wholesaler') => {
  const tel = safeTel(d.tel);
  return tel ? `<a class="${cls}" href="${tel}" data-act="contact" data-id="${esc(d.id)}" aria-label="${esc(label)}: call ${esc(d.contact_name || 'the wholesaler')} about ${esc(d.street)}">${ICON.phone}<span>${esc(label)}</span></a>`
    : '<button class="btn block" disabled>No wholesaler phone in this email</button>';
};
const facts = d => [money(d.price), d.beds != null ? `${d.beds} bd` : null, d.baths != null ? `${d.baths} ba` : null, d.sqft ? `${num(d.sqft)} sq ft` : (d.lot_sqft ? `${num(d.lot_sqft)} sq ft lot` : null)].filter(Boolean).join(' · ');
const cut = d => (d.price_prev && d.price && d.price_prev > d.price ? `<span class="pill warn" title="Was ${money(d.price_prev)}">Price cut ${money(d.price_prev - d.price)}</span>` : '');
function pill(d) {
  if (d.discount_pct == null) return '';
  // green = well below area median, yellow = near it, red = above it (value_tier, independent of the buy box)
  const cls = d.value_tier === 'yellow' ? ' warn' : d.value_tier === 'red' ? ' bad' : '';
  return `<span class="pill${cls}">${esc(discLabel(d))}</span>`;
}
const quickLinks = (d, cls = 'btn small') => [
  safeUrl(d.pictures_url) && `<a class="${cls}" href="${safeUrl(d.pictures_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${ICON.photo || ''}Pictures</a>`,
  safeUrl(d.maps_url) && `<a class="${cls}" href="${safeUrl(d.maps_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${ICON.pin || ''}Map</a>`,
].filter(Boolean).join(' ');
function photoStrip(d) {
  // Real photos pulled straight out of the wholesaler's email (not a Google Drive link --
  // those aren't reliably public, so we show the pictures the email itself embeds).
  const photos = (d.photos || []).map(safeUrl).filter(Boolean);
  if (!photos.length) return '';
  return `<div class="photostrip" role="list" aria-label="Photos of ${esc(d.street)}">${photos.map(u =>
    `<a class="phototile" href="${u}" target="_blank" rel="noopener" role="listitem" onclick="event.stopPropagation()">` +
    `<img src="${u}" alt="Photo of ${esc(d.street)}" loading="lazy"></a>`).join('')}</div>`;
}
const quickCall = d => {
  const tel = safeTel(d.tel);
  return tel ? `<a class="btn small primary iconly" href="${tel}" data-act="contact" data-id="${esc(d.id)}" aria-label="Call ${esc(d.contact_name || 'the wholesaler')} about ${esc(d.street)}">${ICON.phone}<span>Call</span></a>` : '';
};
function railItem(d, activeId) {
  const active = d.id === activeId;
  return `<div class="railitem${active ? ' active' : ''}"${active ? ' aria-current="true"' : ''}>
    <a class="rlink" href="#/deal/${esc(d.id)}">
      <div class="rtop">${pill(d)}${cut(d)}</div>
      <div class="raddr">${esc(d.street)}</div>
      <div class="rppsf num">${dollars(d.ppsf)}<small>/sf</small><span class="rvs">vs ${dollars(d.median_ppsf)}</span></div>
    </a>
    ${quickCall(d)}
  </div>`;
}
function dealRail(activeId) {
  const ranked = S.deals.filter(d => d.hot).sort((a, b) => (b.discount_pct ?? -999) - (a.discount_pct ?? -999));
  if (ranked.length < 2) return '';
  return `<nav class="rail" aria-label="Other deals, best to worst — tap to open, or tap the phone icon to call">${ranked.map(d => railItem(d, activeId)).join('')}</nav>`;
}
const compsLine = d => {
  if (d.median_ppsf == null) return '';
  const farthest = d.comps.length ? d.comps[d.comps.length - 1].dist_mi : null;
  return d.n_in_radius === d.n_comps ? `${d.n_comps} comps within ${farthest} mi`
    : `${d.n_comps} comps, ${d.n_in_radius} within ${esc(S.status.settings.radius_mi)}mi · rest out to ${farthest} mi`;
};
function dealCard(d) {
  const isNew = d.first_seen && Date.now() - new Date(d.first_seen) < 36e5 * 36;
  const links = quickLinks(d);
  return `<article class="card deal">
    <div class="head"><span>${d.hot ? '<span class="pill">Hot deal</span>' : ''} ${cut(d)} ${isNew ? '<span class="pill plain">New</span>' : ''} ${d.contacted_at ? '<span class="pill plain">Contacted</span>' : ''}</span>${pill(d)}</div>
    <a href="#/deal/${esc(d.id)}" class="addr" style="display:block">${esc(d.street)}</a><div class="city">${esc(d.city)}, AZ ${esc(d.zip || '')}</div>
    <div class="ppsf num"><b>${dollars(d.ppsf)}</b><span>/sq ft</span><div class="vs">area median ${dollars(d.median_ppsf)}<br>${compsLine(d)}</div></div>
    <div class="facts">${esc(facts(d))}</div>
    ${links ? `<div class="facts" style="display:flex;gap:8px">${links}</div>` : ''}
    ${photoStrip(d)}
    <div class="actions">${callLink(d, 'btn primary')}<a class="btn details" href="#/deal/${esc(d.id)}">Details</a></div>
  </article>`;
}
function row(d) {
  const right = d.ppsf != null ? `<b class="num">${dollars(d.ppsf)}</b><small>/sf</small>${pill(d)}`
    : d.ppsf_lot != null ? `<b class="num">$${d.ppsf_lot.toFixed(2)}</b><small>/lot sf</small>` : '<small>no sq ft</small>';
  return `<a class="row" href="#/deal/${esc(d.id)}"><div><div class="addr">${esc(d.street)}</div><div class="sub">${esc(d.city)} · ${esc(facts(d))}</div>${cut(d) ? `<div style="margin-top:4px">${cut(d)}</div>` : ''}</div><div class="rn">${right}</div></a>`;
}
function strip(d) {
  if (d.median_ppsf == null || !d.comps.length) return '';
  const vals = d.comps.map(c => c.ppsf).concat([d.ppsf, d.median_ppsf]), lo = Math.min(...vals), hi = Math.max(...vals), W = 300, P = 14;
  const x = v => P + (W - 2 * P) * (hi === lo ? .5 : (v - lo) / (hi - lo));
  const clamp = v => Math.min(W - 26, Math.max(26, v));
  return `<svg class="strip" viewBox="0 0 ${W} 58" role="img" aria-label="Where this deal's cost per square foot sits among ${d.comps.length} nearby deals">
    <line class="axis" x1="${P}" x2="${W - P}" y1="28" y2="28"/>
    ${d.comps.map(c => `<circle class="comp" cx="${x(c.ppsf).toFixed(1)}" cy="28" r="3.5"/>`).join('')}
    <line class="med" x1="${x(d.median_ppsf).toFixed(1)}" x2="${x(d.median_ppsf).toFixed(1)}" y1="14" y2="42"/>
    <text x="${clamp(x(d.median_ppsf)).toFixed(1)}" y="9" text-anchor="middle">area median ${dollars(d.median_ppsf)}</text>
    <circle class="me" cx="${x(d.ppsf).toFixed(1)}" cy="28" r="7"/>
    <text x="${clamp(x(d.ppsf)).toFixed(1)}" y="54" text-anchor="middle">this deal ${dollars(d.ppsf)}</text>
  </svg>`;
}

/* ------------------------------------------------------------------ views */
function viewAlerts() {
  const st = S.status, ls = st.last_scan, hot = S.deals.filter(d => d.hot).sort((a, b) => b.discount_pct - a.discount_pct);
  const cheap = S.deals.filter(d => d.tier === 'cheap_outside_box').sort((a, b) => b.discount_pct - a.discount_pct);
  const digest = S.alerts.find(a => a.kind === 'digest');
  let h = `<div class="card status"><div><b>${ls ? 'Last scan ' + esc(when(ls.started_at)) : 'No scan yet'}</b><span>${ls ? esc(ls.message) : 'Runs daily at ' + esc(fmtTime(st.settings.scan_time))}${' · next ' + esc(when(st.next_scan))}</span></div><button class="btn small" data-act="scan">Scan now</button></div>`;
  if (!S.deals.length) {
    h += `<div class="empty"><b>No deals yet</b>Tap <em>Scan now</em> to read the wholesale email right away, or wait for the ${esc(fmtTime(st.settings.scan_time))} scan.</div>`;
    return h;
  }
  if (digest) h += `<h2>Today’s digest</h2><div class="card pad small">${esc(digest.body)}</div>`;
  h += `<h2>Hot deals${hot.length ? ` · ${hot.length}` : ''}</h2>`;
  h += hot.length ? hot.map(dealCard).join('') : `<div class="card pad small muted">Nothing in your buy box is far enough below the area median right now. Lower the “low cost” threshold in Settings to see more.</div>`;
  if (cheap.length) h += `<h2>Cheap, but outside your buy box</h2><div class="card list">${cheap.slice(0, 6).map(row).join('')}</div><p class="hint">${esc(cheap[0].street)}: ${esc(cheap[0].box_misses.join(', '))}</p>`;
  return h;
}
function viewDeals() {
  let ds = S.deals.slice();
  const f = S.filter;
  ds = ds.filter(d => (f === 'box' ? d.in_buy_box : f === 'land' ? d.type === 'land' : true));
  const key = { discount: d => -(d.discount_pct ?? -999), ppsf: d => d.ppsf ?? 1e9, price: d => d.price ?? 1e12 }[S.sort];
  ds.sort((a, b) => key(a) - key(b));
  const chip = (k, l) => `<button class="chip" data-act="filter" data-v="${k}" aria-pressed="${f === k}">${l}</button>`;
  return `<div class="chips">${chip('box', 'In my buy box')}${chip('all', 'All deals')}${chip('land', 'Land')}</div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span class="small muted">${ds.length} deal${ds.length === 1 ? '' : 's'}</span>
    <select class="sort" data-act="sort" aria-label="Sort deals"><option value="discount"${S.sort === 'discount' ? ' selected' : ''}>Biggest discount</option><option value="ppsf"${S.sort === 'ppsf' ? ' selected' : ''}>Lowest $/sq ft</option><option value="price"${S.sort === 'price' ? ' selected' : ''}>Lowest price</option></select></div>
    ${ds.length ? `<div class="card list">${ds.map(row).join('')}</div>` : '<div class="empty"><b>Nothing here</b>Run a scan, or try “All deals”.</div>'}`;
}
function viewDeal(id, q) {
  const d = S.deals.find(x => x.id === id);
  if (!d) return '<a class="back" href="#/deals">' + ICON.back + 'Back</a><div class="empty"><b>Deal not found</b>It may have aged out of the comparison window.</div>';
  const s = S.status.settings, why = [];
  if (d.median_ppsf != null) why.push(`<li>${dollars(d.ppsf)}/sq ft is <b>${Math.round(Math.abs(d.discount_pct))}% ${d.discount_pct >= 0 ? 'below' : 'above'}</b> the ${dollars(d.median_ppsf)} median of ${compsLine(d)}.</li>`);
  else if (d.compare_note) why.push(`<li class="no">${esc(d.compare_note)}.</li>`);
  if (d.rank) why.push(`<li>Cheapest #${d.rank} of ${d.pool_size} nearby.</li>`);
  why.push(d.in_buy_box ? '<li>Fits your buy box.</li>' : `<li class="no">Outside your buy box: ${esc(d.box_misses.join(', '))}.</li>`);
  const warn = [];
  if ((d.flags || []).includes('sqft_uncertain')) warn.push('The email says the square footage may not match public records, so double-check this cost per sq ft.');
  if ((d.flags || []).includes('combined_sqft')) warn.push('Cost per sq ft uses combined living area: ' + d.sqft_parts.map(p => `${p.name} ${num(p.sqft)}`).join(' + ') + ' sq ft.');
  if ((d.flags || []).includes('arv_is_floor')) warn.push('The ARV in the email is a minimum (“+”).');
  if (d.geo_warning) warn.push(d.geo_warning + '.');
  if (d.price_prev && d.price_prev > d.price) warn.push(`Price dropped from ${money(d.price_prev)}.`);
  const kv = [['Asking price', money(d.price)], ['ARV', d.arv ? money(d.arv) + (d.arv_high ? '–' + money(d.arv_high) : '') + (d.price ? ` (+${money(d.arv - d.price)})` : '') : null],
    ['Beds / baths', d.beds != null ? `${d.beds} / ${d.baths ?? '—'}` : null], ['Living area', d.sqft ? num(d.sqft) + ' sq ft' : null], ['Lot', d.lot_sqft ? num(d.lot_sqft) + ' sq ft' : null],
    ['Built', d.year_built], ['Type', TYPE[d.type] + (d.type_raw && d.type_raw.toLowerCase() !== TYPE[d.type].toLowerCase() ? ` (${d.type_raw})` : '')], ['Occupancy', OCC[d.occupancy]],
    ['Access', d.access], ['Close of escrow', d.coe], ['Earnest money', d.emd ? money(d.emd) : null], ['HOA', d.hoa], ['Zoning', d.zoning], ['APN', d.apn],
    ...Object.entries(d.details || {}).slice(0, 10)].filter(([, v]) => v != null && v !== '');
  const notes = [d.condition, ...(d.notes || [])].filter(Boolean);
  const lat = d.ppsf != null ? `<div class="ppsf num"><b>${dollars(d.ppsf)}</b><span>/sq ft</span><div class="vs">${pill(d)}</div></div>`
    : `<div class="ppsf num"><b>${d.ppsf_lot != null ? '$' + d.ppsf_lot.toFixed(2) : '—'}</b><span>${d.ppsf_lot != null ? '/sq ft of lot' : 'no sq ft in email'}</span></div>`;
  const links = quickLinks(d);
  const sms = safeSms(d.sms);
  let h = `<a class="back" href="#/deals">${ICON.back}Back</a>
    <div class="dsplit">${dealRail(d.id)}<div class="dmain">
    <div class="dhead"><span>${d.hot ? '<span class="pill">Hot deal</span> ' : ''}${cut(d)} ${d.contacted_at ? '<span class="pill plain">Contacted</span>' : ''}</span><h1>${esc(d.street)}</h1><div class="city">${esc(d.city)}, AZ ${esc(d.zip || '')}</div></div>
    <div class="card hero">${lat}${strip(d)}<ul class="why">${why.join('')}</ul></div>
    ${warn.map(w => `<div class="notice">${esc(w)}</div>`).join('')}
    <h2>Details</h2><dl class="card grid" style="margin:0">${kv.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
    ${notes.length ? `<h2>Notes from the email</h2><ul class="card notes">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    ${links ? `<div style="margin-top:12px;display:flex;gap:8px">${links}</div>` : ''}
    ${photoStrip(d)}`;
  if (d.comps.length) h += `<h2>Nearby deals compared (${d.comps.length})</h2><div class="card list">${d.comps.map(c => `<a class="row" href="#/deal/${esc(c.id)}"><div><div class="addr">${esc(c.address.split(',')[0])}</div><div class="sub">${c.in_radius ? `<span class="pill plain">Within ${esc(s.radius_mi)}mi</span> ` : ''}${c.dist_mi} mi · ${money(c.price)} · ${num(c.sqft)} sq ft</div></div><div class="rn"><b class="num">${dollars(c.ppsf)}</b><small>/sf</small></div></a>`).join('')}</div>`;
  h += `<p class="hint">Distances use ${s.geocode_online ? 'street-level geocoding' : 'ZIP-code centers'}, so they are ${s.geocode_online ? 'accurate to the street' : 'approximate'}. Appraisers generally prefer comps within ${esc(s.radius_mi)}mi; farther ones are only used when the closer ring doesn’t have enough.</p>`;
  h += `</div></div>`;
  h += `<div class="bar"><div class="in">${callLink(d, 'btn primary', 'Contact wholesaler')}${sms ? `<a class="btn sms" href="${sms}" data-act="contact" data-id="${esc(d.id)}" aria-label="Text the wholesaler">${ICON.msg}Text</a>` : ''}</div></div>`;
  if (q && q.get('call') === '1') h += confirmSheet(d);
  return h;
}
function confirmSheet(d) {
  return `<div class="scrim" data-act="dismiss"><div class="sheet" role="dialog" aria-label="Call the wholesaler"><h3>Call ${esc(d.contact_name || 'the wholesaler')}?</h3><div class="muted small">About ${esc(d.address)}</div>
    <div class="num">${esc((d.contact_phone || '').replace(/^\+1(\d{3})(\d{3})(\d{4})$/, '$1-$2-$3'))}</div>
    <div class="actions">${callLink(d, 'btn primary', 'Call now')}<button class="btn" data-act="dismiss">Not now</button></div></div></div>`;
}
const fmtTime = t => { const [h, m] = t.split(':').map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`; };
const GEOCODE_SETUP_STEPS = [
  { id: 'account', label: 'Create a Google Cloud account', url: 'https://console.cloud.google.com/', help: 'Free to create; billing only kicks in if you go past the free monthly credit below.' },
  { id: 'project', label: 'Create a project', url: 'https://console.cloud.google.com/projectcreate', help: 'Any name works, e.g. “Deal Radar”.' },
  { id: 'enable', label: 'Enable the Geocoding API', url: 'https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com', help: 'Click Enable on that page for the project you just made.' },
  { id: 'billing', label: 'Turn on billing for the project', url: 'https://console.cloud.google.com/billing', help: 'Google’s recurring free credit comfortably covers normal use for one investor.' },
  { id: 'key', label: 'Create an API key', url: 'https://console.cloud.google.com/apis/credentials', help: 'Click “Create credentials” → “API key”. Restricting it to the Geocoding API only is recommended.' },
  { id: 'paste', label: 'Paste the key in below', help: 'Last step — no link, just fill in the field under this checklist.' },
];
function geocodeSetupFlow(s) {
  const done = s.geocode_setup_progress || [];
  const steps = GEOCODE_SETUP_STEPS.map((st, i) => `<div class="field checkstep">
      <label for="gs-${st.id}" style="display:flex;align-items:flex-start;gap:12px;flex:1;cursor:pointer">
        <input id="gs-${st.id}" type="checkbox" data-act="geocode-step" data-step="${st.id}" ${done.includes(st.id) ? 'checked' : ''} style="margin-top:2px">
        <span><span class="lab">${i + 1}. ${esc(st.label)}</span>${st.help ? `<span class="help">${esc(st.help)}</span>` : ''}</span>
      </label>
      ${st.url ? `<a class="btn small" href="${st.url}" target="_blank" rel="noopener">Open</a>` : ''}
    </div>`).join('');
  return `<div class="card set" style="margin-top:10px">
      <div style="padding:12px 16px 2px" class="small muted">${done.length}/${GEOCODE_SETUP_STEPS.length} steps checked off</div>
      ${steps}
      <div class="field"><div><label for="f-geocode_api_key">Google API key</label><span class="help">Paste the key from step 5. Stored on this server only.</span></div>
        <input id="f-geocode_api_key" type="text" data-path="geocode_api_key" placeholder="AIza…" value="${esc(s.geocode_api_key)}" style="width:220px"></div>
    </div>`;
}
const IMAP_SETUP_STEPS = [
  { id: 'verify', label: 'Turn on 2-Step Verification', url: 'https://myaccount.google.com/signinoptions/two-step-verification', help: 'Google (and most providers) require this before they’ll issue an app password. Skip if it’s already on.' },
  { id: 'apppw', label: 'Create an app password', url: 'https://myaccount.google.com/apppasswords', help: 'Name it "Deal Radar". It gives you a 16-character code — that’s what goes below, never the real account password.' },
  { id: 'enter', label: 'Enter the email + app password below', help: 'Last step — no link, just fill in the two fields under this checklist.' },
];
function imapSetupFlow(s) {
  const done = s.imap_setup_progress || [];
  const steps = IMAP_SETUP_STEPS.map((st, i) => `<div class="field checkstep">
      <label for="is-${st.id}" style="display:flex;align-items:flex-start;gap:12px;flex:1;cursor:pointer">
        <input id="is-${st.id}" type="checkbox" data-act="imap-step" data-step="${st.id}" ${done.includes(st.id) ? 'checked' : ''} style="margin-top:2px">
        <span><span class="lab">${i + 1}. ${esc(st.label)}</span>${st.help ? `<span class="help">${esc(st.help)}</span>` : ''}</span>
      </label>
      ${st.url ? `<a class="btn small" href="${st.url}" target="_blank" rel="noopener">Open</a>` : ''}
    </div>`).join('');
  return `<div class="card set" style="margin-top:10px">
      <div style="padding:12px 16px 2px" class="small muted">${done.length}/${IMAP_SETUP_STEPS.length} steps checked off · works with Gmail, Outlook, Yahoo, iCloud and most others</div>
      ${steps}
      <div class="field"><div><label for="f-email_address">Email address</label><span class="help">The inbox to scan for the wholesaler blast.</span></div>
        <input id="f-email_address" type="email" data-path="email_address" placeholder="you@gmail.com" value="${esc(s.email_address)}" style="width:220px"></div>
      <div class="field"><div><label for="f-email_app_password">App password</label><span class="help">The 16-character code from step 2. Stored on this server only, never shown again in full.</span></div>
        <input id="f-email_app_password" type="password" data-path="email_app_password" placeholder="•••• •••• •••• ••••" value="${esc(s.email_app_password)}" style="width:220px"></div>
      ${s.email_address && !IMAP_HOSTS_KNOWN(s.email_address) ? `<div class="field"><div><label for="f-imap_host">IMAP server</label><span class="help">Not a Gmail/Outlook/Yahoo/iCloud/AOL address — search "&lt;your provider&gt; IMAP app password" for the server name.</span></div>
        <input id="f-imap_host" type="text" data-path="imap_host" placeholder="imap.example.com" value="${esc(s.imap_host)}" style="width:220px"></div>` : ''}
      <div class="field"><div><span class="lab">Test it</span><span class="help">Tries signing in right now — doesn’t wait for the next scan.</span></div>
        <button class="btn small" data-act="test-email">Test connection</button></div>
    </div>`;
}
const IMAP_HOSTS_KNOWN = email => /@(gmail|googlemail|outlook|hotmail|live|office365|yahoo|ymail|icloud|me|mac|aol)\.com$/i.test((email || '').trim());
function viewSettings() {
  const s = S.status.settings, b = s.buy_box;
  const num_ = (path, val, help, step = 1) => `<div class="field"><div><label for="f-${path}">${help[0]}</label>${help[1] ? `<span class="help">${help[1]}</span>` : ''}</div><input id="f-${path}" type="number" inputmode="decimal" step="${step}" data-path="${path}" value="${esc(val)}"></div>`;
  const chk = (path, val, label, help) => `<div class="field"><div><label for="f-${path}">${label}</label>${help ? `<span class="help">${help}</span>` : ''}</div><input id="f-${path}" type="checkbox" data-path="${path}" ${val ? 'checked' : ''}></div>`;
  const typ = ['sfr', 'multi', 'condo', 'manufactured'].map(t => `<button class="chip" data-act="type" data-v="${t}" aria-pressed="${b.types.includes(t)}">${TYPE[t]}</button>`).join('');
  const perm = ('Notification' in window) ? Notification.permission : 'unsupported';
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.navigator.standalone;
  return `<h2>Daily scan</h2><div class="card set">
      <div class="field"><div><label for="f-scan_time">Scan time</label><span class="help">Arizona time. Starts on time, up to ${esc(s.scan_window_min)} min late.</span></div><input id="f-scan_time" type="time" data-path="scan_time" value="${esc(s.scan_time)}"></div>
      ${num_('scan_window_min', s.scan_window_min, ['Tolerance (minutes)', 'How late a scan may start'])}
      ${chk('catch_up', s.catch_up, 'Catch up if missed', 'Scan once when back online if the window passed')}</div>
    <h2>Connect your email</h2><div class="card set">
      ${DEMO ? '<p class="hint" style="padding:12px 16px 2px">This sample runs on fixed demo data, so there’s nothing to connect here — it’s wired up for real use once the app is deployed. On the real app, you’d enter Mason’s email and an app password here and it would scan his inbox every morning, same as the "Scan now" button does with these two sample emails.</p>' : `<p class="hint" style="padding:12px 16px 2px">${s.email_address ? 'Connected — the daily scan reads this inbox automatically.' : 'Without this, the daily scan has nothing to read. Connect the inbox the wholesaler blast lands in.'}</p>${imapSetupFlow(s)}`}
    </div>
    <h2>Notifications</h2><div class="card set"><div class="field"><div><span class="lab">Phone alerts</span><span class="help">${DEMO ? 'Demo: shows a sample alert banner.' : perm === 'granted' ? 'On for this device.' : perm === 'denied' ? 'Blocked in browser settings.' : 'Off.'}${ios ? ' On iPhone, first tap Share → Add to Home Screen.' : ''}</span></div>
      <span style="display:flex;gap:8px"><button class="btn small" data-act="enable-notify">${perm === 'granted' && !DEMO ? 'Re-enable' : 'Turn on'}</button><button class="btn small" data-act="test-notify">Test</button></span></div></div>
    <h2>What counts as a deal</h2><div class="card set">
      ${num_('low_ppsf_pct', s.low_ppsf_pct, ['Low cost per sq ft', '% below the area median'])}
      ${num_('radius_mi', s.radius_mi, ['Ideal comp radius (miles)', 'Appraisers usually want comps this close; farther ones are used only if there aren’t enough nearby'], 0.1)}
      ${num_('min_comps', s.min_comps, ['Minimum comps', 'Searches closest-to-furthest until it has at least this many'])}
      ${num_('max_radius_mi', s.max_radius_mi, ['Max search radius (miles)', 'Never pull a comp from farther than this, even to reach the minimum'], 0.5)}</div>
    <h2>Location accuracy</h2><div class="card set">
      ${DEMO ? '' : chk('geocode_online', s.geocode_online, 'Precise location lookup', 'Turns ZIP-code-only distances into real street-level distances, so "0.5 miles" means what it says.')}
      <div class="field"><div><label for="f-geocode_provider">Geocoding service</label><span class="help">Free covers almost every address. Add the paid one only if you want a backup for addresses it misses.</span></div>
        <select id="f-geocode_provider" data-path="geocode_provider" style="min-height:40px;border:1px solid var(--line);background:var(--bg);border-radius:10px;padding:8px 10px">
          <option value="census"${s.geocode_provider !== 'google' ? ' selected' : ''}>Free — US Census</option>
          <option value="google"${s.geocode_provider === 'google' ? ' selected' : ''}>Paid — Google Maps</option>
        </select></div></div>
      ${s.geocode_provider === 'google' ? geocodeSetupFlow(s) : ''}
      <p class="hint">${DEMO ? 'This sample runs on fixed demo data, so the toggle above doesn’t change what you see here — it’s wired up for real use once the app is deployed.' : 'Distances use the US Census geocoder for free, street-level accuracy once this server has internet access.'} Switch to Google Maps above only if you want a paid backup for the rare address Census can’t find.</p>
    <h2>Buy box</h2><div class="card set"><div style="padding:12px 16px 4px" class="small muted">Property types</div><div class="types">${typ}</div>
      ${num_('buy_box.max_price', b.max_price, ['Max price'], 5000)}${num_('buy_box.min_beds', b.min_beds, ['Min bedrooms'])}${num_('buy_box.min_sqft', b.min_sqft, ['Min sq ft'], 50)}
      ${chk('buy_box.allow_occupied', b.allow_occupied, 'Allow occupied')}${chk('alert_only_buy_box', s.alert_only_buy_box, 'Only alert on buy-box matches')}</div>
    <h2>Wholesaler contact</h2><div class="card set"><div class="field"><div><label for="f-contact_phone_override">Override number</label><span class="help">Leave blank to use the number in the email</span></div><input id="f-contact_phone_override" type="tel" data-path="contact_phone_override" placeholder="+1…" value="${esc(s.contact_phone_override)}"></div></div>
    ${S.status.dev ? `<h2>Testing</h2><div class="card set"><div class="field"><div><span class="lab">Start over</span><span class="help">Forget scanned emails, deals and alerts</span></div><button class="btn small" data-act="reset">Reset</button></div></div>` : ''}
    <p class="hint">Starter buy box. Adjust these to match Mason’s real criteria.</p>`;
}

/* ------------------------------------------------------------------ router / render */
function render() {
  const app = $('#view'); if (!app) return;
  const [path, query] = (location.hash || '#/alerts').split('?'), q = new URLSearchParams(query || '');
  document.body.classList.toggle('detail', path.startsWith('#/deal/'));
  document.querySelectorAll('.tabs a').forEach(a => { if (a.getAttribute('href') === (path.startsWith('#/deal/') ? '#/deals' : path)) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  $('#title').textContent = path === '#/deals' ? 'All deals' : path === '#/settings' ? 'Settings' : path.startsWith('#/deal/') ? '' : 'Deal Radar';
  if (!S.status) { app.innerHTML = `<div class="empty"><b>${S.online ? 'Loading…' : 'Can’t reach the server'}</b>${S.online ? '' : esc(S.error || '') + '<br><button class="btn small" data-act="retry" style="margin-top:12px">Try again</button>'}</div>`; return; }
  app.innerHTML = path === '#/deals' ? viewDeals() : path === '#/settings' ? viewSettings() : path.startsWith('#/deal/') ? viewDeal(path.slice(7), q) : viewAlerts();
  if (path === '#/alerts' || path === '') markRead();
  window.scrollTo(0, path.startsWith('#/deal/') ? 0 : window.scrollY);
}
let readTimer;
function markRead() { clearTimeout(readTimer); if (!S.status?.unread) return; readTimer = setTimeout(async () => { try { await API.readAlerts(); S.status.unread = 0; updateBadge(); } catch { /* retry next visit */ } }, 1500); }

/* ------------------------------------------------------------------ notifications */
function urlB64(b64) { const p = '='.repeat((4 - b64.length % 4) % 4), r = atob((b64 + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); }
async function enableNotifications() {
  if (DEMO) { showPush({ title: 'Low cost per sq ft: 1216 E Hess Ave', body: 'This is how a hot-deal alert appears. In the live app it arrives as a real phone notification.', deal_id: (S.deals.find(d => d.hot) || {}).id }); return; }
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return toast('This browser does not support notifications');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') return toast('Notifications were not allowed');
  try {
    const reg = await navigator.serviceWorker.ready;
    if (S.status.push_key && 'PushManager' in window) {
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64(S.status.push_key) });
      await API.subscribe(sub.toJSON()); toast('Alerts are on, even when the app is closed');
    } else toast('Alerts are on while the app is open');
  } catch (e) { toast('Could not finish setup: ' + (e.message || e)); }
  render();
}
async function notify(a) {
  const url = a.deal_id ? `#/deal/${a.deal_id}` : '#/alerts';
  if (!DEMO && 'Notification' in window && Notification.permission === 'granted' && 'serviceWorker' in navigator) {
    try { const reg = await navigator.serviceWorker.ready; return reg.showNotification(a.title, { body: a.body, tag: 'alert-' + a.id, icon: '/icon-192.png', data: { url: '/' + url, deal_id: a.deal_id }, actions: a.deal_id ? [{ action: 'call', title: 'Contact wholesaler' }] : [] }); } catch { /* fall through to banner */ }
  }
  showPush(a);
}
function showPush(a) {
  document.querySelectorAll('.push').forEach(x => x.remove());
  const d = a.deal_id && S.deals.find(x => x.id === a.deal_id), tel = d && safeTel(d.tel);
  const el = document.createElement('div'); el.className = 'push'; el.setAttribute('role', 'alert');
  el.innerHTML = `<div class="who"><i></i>DEAL RADAR · now</div><b>${esc(a.title)}</b><p>${esc(a.body)}</p><div class="actions">${tel ? `<a class="btn primary small" href="${tel}" data-act="contact" data-id="${esc(d.id)}">${ICON.phone}<span>Contact wholesaler</span></a>` : ''}${d ? `<a class="btn small" href="#/deal/${esc(d.id)}" data-act="dismiss-push">View</a>` : ''}<button class="btn small" data-act="dismiss-push">Dismiss</button></div>`;
  document.body.appendChild(el); setTimeout(() => el.remove(), 15000);
}
async function poll() {
  if (document.hidden || DEMO) return;
  try {
    const fresh = (await API.alerts(S.lastAlertId)).sort((a, b) => a.id - b.id);
    if (!fresh.length) return;
    S.lastAlertId = fresh[fresh.length - 1].id;
    await refresh(); render();
    fresh.filter(a => a.kind === 'hot').slice(0, 3).forEach(notify);
    if (!fresh.some(a => a.kind === 'hot')) toast(fresh[fresh.length - 1].title);
  } catch { /* offline: try again next tick */ }
}

/* ------------------------------------------------------------------ events */
document.addEventListener('click', async e => {
  const t = e.target.closest('[data-act]'); if (!t) return;
  const act = t.dataset.act;
  if (act === 'contact') { API.contacted(t.dataset.id).then(() => { const d = S.deals.find(x => x.id === t.dataset.id); if (d) d.contacted_at = new Date().toISOString(); }).catch(() => {}); return; } // let the tel:/sms: link proceed
  if (act === 'dismiss') { if (e.target === t || t.tagName === 'BUTTON') { const [p] = location.hash.split('?'); history.replaceState(null, '', p); render(); } return; }
  if (act === 'sort' || act === 'geocode-step' || act === 'imap-step') return; // handled on 'change'; preventDefault here would block the dropdown/checkbox
  if (act === 'dismiss-push') { document.querySelectorAll('.push').forEach(x => x.remove()); return; }
  e.preventDefault();
  if (act === 'scan') {
    t.disabled = true; t.textContent = 'Scanning…';
    try { const r = await API.scan(); await refresh(); render(); toast(r.emails_new ? `Found ${r.deals_new} deals, ${r.hot} hot` : r.message);
      if (DEMO && r.emails_new) { const top = S.alerts.filter(a => a.kind === 'hot').sort((a, b) => b.id - a.id)[0]; if (top) setTimeout(() => showPush(top), 600); } }
    catch (err) { toast('Scan failed: ' + err.message); render(); }
  } else if (act === 'filter') { S.filter = t.dataset.v; render(); }
  else if (act === 'type') {
    const v = t.dataset.v, cur = S.status.settings.buy_box.types, next = cur.includes(v) ? cur.filter(x => x !== v) : [...cur, v];
    if (!next.length) return toast('Keep at least one property type');
    await save({ buy_box: { types: next } }); render();
  } else if (act === 'enable-notify') enableNotifications();
  else if (act === 'test-notify') { const d = S.deals.find(x => x.hot) || S.deals[0]; notify({ id: 'test', deal_id: d && d.id, title: d ? 'Test alert: ' + d.street : 'Test alert', body: d ? `${dollars(d.ppsf)}/sq ft. Tap Contact wholesaler to call.` : 'Notifications are working.' }); }
  else if (act === 'reset') { await API.reset(); await refresh(); render(); toast('Cleared. Scan now to start again.'); }
  else if (act === 'retry') { await refresh(); render(); }
  else if (act === 'test-email') {
    t.disabled = true; const was = t.textContent; t.textContent = 'Testing…';
    try { const r = await API.testEmail(); toast(r.ok ? 'Connected! Signed in fine.' : 'Could not connect: ' + r.error); }
    catch (err) { toast('Could not connect: ' + err.message); }
    t.disabled = false; t.textContent = was;
  }
});
document.addEventListener('change', async e => {
  const t = e.target;
  if (t.dataset.act === 'sort') { S.sort = t.value; return render(); }
  if (t.dataset.act === 'geocode-step') {
    const id = t.dataset.step, cur = S.status.settings.geocode_setup_progress || [];
    const next = t.checked ? [...new Set([...cur, id])] : cur.filter(x => x !== id);
    await save({ geocode_setup_progress: next });
    return render();
  }
  if (t.dataset.act === 'imap-step') {
    const id = t.dataset.step, cur = S.status.settings.imap_setup_progress || [];
    const next = t.checked ? [...new Set([...cur, id])] : cur.filter(x => x !== id);
    await save({ imap_setup_progress: next });
    return render();
  }
  const path = t.dataset.path; if (!path) return;
  let v = t.type === 'checkbox' ? t.checked : t.type === 'number' ? Number(t.value) : t.value;
  if (t.type === 'number' && (t.value === '' || Number.isNaN(v))) return;
  const patch = {}; let o = patch; const parts = path.split('.'); parts.slice(0, -1).forEach(k => { o[k] = {}; o = o[k]; }); o[parts.at(-1)] = v;
  await save(patch);
  if (path === 'geocode_provider') render();
});
async function save(patch) {
  try { await API.putSettings(patch); const [st, ds] = await Promise.all([API.status(), API.deals()]); S.status = st; S.deals = ds; toast('Saved'); }
  catch (err) { toast('Not saved: ' + err.message); }
}

/* ------------------------------------------------------------------ boot */
(async function boot() {
  $('#tabs').innerHTML = `<nav aria-label="Main"><a href="#/alerts">${ICON.bell}Alerts<b class="badge" id="badge" hidden></b></a><a href="#/deals">${ICON.list}All deals</a><a href="#/settings">${ICON.gear}Settings</a></nav>`;
  if (DEMO) $('#demo-note').hidden = false;
  addEventListener('hashchange', render);
  render(); await refresh(); render();
  setInterval(poll, 60000); document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  if ('serviceWorker' in navigator && !DEMO && location.protocol.startsWith('http')) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
