/* Deal Radar service worker: offline shell + push alerts with a "Contact wholesaler" action. */
const CACHE = 'dealradar-v1';
const SHELL = ['/', '/styles.css', '/app.js', '/manifest.webmanifest', '/icon-192.png'];

self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/')) return; // API always live
  e.respondWith(caches.open(CACHE).then(async c => {   // shell: cached copy now, refreshed in background
    const hit = await c.match(e.request);
    const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  }));
});

self.addEventListener('push', e => {
  let d = {}; try { d = e.data ? e.data.json() : {}; } catch { d = { title: 'Deal Radar', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Deal Radar', {
    body: d.body || '', tag: d.tag || 'deal-radar', icon: '/icon-192.png', badge: '/icon-192.png',
    data: { url: d.url || '/#/alerts', deal_id: d.deal_id || null },
    actions: d.deal_id ? [{ action: 'call', title: 'Contact wholesaler' }] : [],
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const { url, deal_id } = e.notification.data || {};
  // "Contact wholesaler" opens the deal with a one-tap call sheet (phones require you to confirm the call)
  const target = e.action === 'call' && deal_id ? `/#/deal/${deal_id}?call=1` : (url || '/#/alerts');
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    for (const c of cs) if ('focus' in c) { c.navigate ? c.navigate(target) : null; return c.focus(); }
    return self.clients.openWindow(target);
  }));
});
