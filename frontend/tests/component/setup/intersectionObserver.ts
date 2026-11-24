class NoopIntersectionObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
	takeRecords() {
		return [];
	}
}

if (typeof globalThis.IntersectionObserver === 'undefined') {
	(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = NoopIntersectionObserver;
}
