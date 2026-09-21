class NoopResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = NoopResizeObserver;
}
