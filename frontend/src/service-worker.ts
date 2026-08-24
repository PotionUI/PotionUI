/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

import { build, files, version } from '$service-worker';

const sw = self as unknown as ServiceWorkerGlobalScope;

const CACHE_NAME = `potionui-cache-${version}`;

// Assets to pre-cache (build output + static files)
const PRECACHE_ASSETS = [...build, ...files];

sw.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(CACHE_NAME)
			.then((cache) => cache.addAll(PRECACHE_ASSETS))
			.then(() => sw.skipWaiting())
	);
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
			)
			.then(() => sw.clients.claim())
	);
});

sw.addEventListener('fetch', (event) => {
	const { request } = event;
	const url = new URL(request.url);

	// Network-only for API calls and WebSocket
	if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
		return;
	}

	// Network-only for non-GET requests
	if (request.method !== 'GET') {
		return;
	}

	// Cache-first for static assets (build output)
	if (url.pathname.startsWith('/_app/') || PRECACHE_ASSETS.includes(url.pathname)) {
		event.respondWith(
			caches.match(request).then((cached) => cached || fetch(request))
		);
		return;
	}

	// Network-first for navigation requests (HTML pages)
	if (request.mode === 'navigate') {
		event.respondWith(
			fetch(request).catch(() => caches.match(request).then((cached) => cached || caches.match('/generate')))
		);
		return;
	}

	// Network-first for everything else
	event.respondWith(
		fetch(request)
			.then((response) => {
				// Optionally cache successful responses
				if (response.ok) {
					const responseClone = response.clone();
					caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
				}
				return response;
			})
			.catch(() => caches.match(request).then((cached) => cached || new Response('Offline', { status: 503 })))
	);
});
