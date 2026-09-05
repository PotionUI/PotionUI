/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

import { files, version } from '$service-worker';
import { isPrecacheEligibleStaticPath } from './lib/service-worker/shell';
import { cacheFirst, navigateOffline, precacheShell } from './lib/service-worker/runtime';

const sw = self as unknown as ServiceWorkerGlobalScope;

const CACHE_NAME = `potionui-cache-${version}`;
const SHELL_URL = '/';

sw.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(CACHE_NAME)
			.then((cache) => precacheShell(cache, files, SHELL_URL, fetch))
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

	// Cache-first, filling the cache on first request: content-hashed build
	// output and the small set of static assets the shell policy also covers.
	if (url.pathname.startsWith('/_app/immutable/') || isPrecacheEligibleStaticPath(url.pathname)) {
		event.respondWith(caches.open(CACHE_NAME).then((cache) => cacheFirst(request, cache, fetch)));
		return;
	}

	// Network-first for navigation requests (HTML pages); offline falls back
	// to the cached SPA shell, which client-side routing then renders from.
	if (request.mode === 'navigate') {
		event.respondWith(
			fetch(request).catch(() => caches.open(CACHE_NAME).then((cache) => navigateOffline(cache, SHELL_URL)))
		);
		return;
	}

	// Network-first for everything else
	event.respondWith(
		fetch(request)
			.then((response) => {
				if (response.ok) {
					const responseClone = response.clone();
					caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
				}
				return response;
			})
			.catch(() => caches.match(request).then((cached) => cached || new Response('Offline', { status: 503 })))
	);
});
