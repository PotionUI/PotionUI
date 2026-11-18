import { readable } from 'svelte/store';
import { createGenerationSocket } from '$lib/services/websocket';
import { createLiveWatcher } from './liveWatch';

export const liveWatcher = createLiveWatcher({ createSocket: () => createGenerationSocket() });

export const liveClock = readable(Date.now(), (set) => {
	set(Date.now());
	const timer = setInterval(() => set(Date.now()), 1000);
	return () => clearInterval(timer);
});
