// AM07 minimal service worker: network-first so pages are never stale;
// friendly offline notice when there is no connection at all.
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).catch(() =>
      new Response(
        "<meta name=viewport content='width=device-width'><body style='font-family:sans-serif;background:#0b1220;color:#e8edf5;display:grid;place-items:center;height:100vh'><div style='text-align:center'><h2>AM07 is offline</h2><p>No internet connection. The engine keeps running on the server.</p></div>",
        { headers: { "Content-Type": "text/html" } }
      )
    )
  );
});
