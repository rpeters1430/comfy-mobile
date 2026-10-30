// Service worker: receives photos shared from other apps (Web Share Target), shows push
// notifications for finished jobs and opens them. It never caches the app or API responses.
const SHARED = 'shared-images';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'POST' || url.pathname !== '/share-target') return;
  event.respondWith((async () => {
    const form = await event.request.formData();
    const files = form.getAll('images').filter(f => f instanceof File && f.type.startsWith('image/')).slice(0, 8);
    await caches.delete(SHARED);
    const cache = await caches.open(SHARED);
    await Promise.all(files.map((file, i) => cache.put(`/shared/${i}`,
      new Response(file, { headers: { 'Content-Type': file.type, 'X-Name': encodeURIComponent(file.name || 'shared') } }))));
    return Response.redirect(`/?shared=${files.length}`, 303);
  })());
});

// Sent by the NAS when a job finishes. The tag is the job id, so this replaces the app's own notification for it.
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data?.json() || {}; } catch (e) {}
  event.waitUntil(self.registration.showNotification(data.title || 'Image Studio', {
    body: data.body || '', icon: '/icon-192.png', tag: data.tag, image: data.image, data: { url: data.url || '/' }
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.focus();
        client.postMessage({ type: 'open-job', url: target });
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
