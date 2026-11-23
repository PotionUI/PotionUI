export function isPageHidden(): boolean {
	return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

export function whenPageVisible(run: () => void): () => void {
	if (!isPageHidden()) {
		run();
		return () => {};
	}
	const resume = () => {
		if (isPageHidden()) return;
		document.removeEventListener('visibilitychange', resume);
		run();
	};
	document.addEventListener('visibilitychange', resume);
	return () => document.removeEventListener('visibilitychange', resume);
}

export function pollWhileVisible(tick: () => void | Promise<void>, ms: number): () => void {
	let inFlight = false;
	const fire = () => {
		if (inFlight || isPageHidden()) return;
		inFlight = true;
		Promise.resolve()
			.then(tick)
			.catch(() => {})
			.finally(() => {
				inFlight = false;
			});
	};
	const timer = setInterval(fire, ms);
	const resume = fire;
	document.addEventListener('visibilitychange', resume);
	return () => {
		clearInterval(timer);
		document.removeEventListener('visibilitychange', resume);
	};
}
