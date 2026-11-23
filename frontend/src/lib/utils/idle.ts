export function whenIdle(task: () => void, timeoutMs = 2000): () => void {
	if (typeof window === 'undefined') {
		task();
		return () => {};
	}
	if (typeof window.requestIdleCallback === 'function') {
		const handle = window.requestIdleCallback(task, { timeout: timeoutMs });
		return () => window.cancelIdleCallback(handle);
	}
	const handle = window.setTimeout(task, Math.min(timeoutMs, 200));
	return () => window.clearTimeout(handle);
}
