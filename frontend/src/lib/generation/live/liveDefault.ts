import { readable } from 'svelte/store';
import { createLiveWatcher, type LiveSocket } from './liveWatch';
import type { LiveMessage } from './liveState';

type Handler = (message: LiveMessage) => void;

function lazySocket(): LiveSocket {
	let real: LiveSocket | null = null;
	let closed = false;
	const pending: Array<[string, Handler]> = [];

	void import('$lib/services/websocket').then(({ createGenerationSocket }) => {
		if (closed) return;
		const socket = createGenerationSocket() as unknown as LiveSocket;
		real = socket;
		socket.connect();
		for (const [id, handler] of pending.splice(0)) socket.subscribe(id, handler);
	});

	return {
		connect() {},
		disconnect() {
			closed = true;
			pending.length = 0;
			real?.disconnect();
		},
		subscribe(id, handler) {
			if (real) real.subscribe(id, handler);
			else pending.push([id, handler]);
		},
		unsubscribe(id, handler) {
			if (real) {
				real.unsubscribe(id, handler);
				return;
			}
			for (let i = pending.length - 1; i >= 0; i -= 1) {
				if (pending[i][0] === id && (!handler || pending[i][1] === handler)) pending.splice(i, 1);
			}
		}
	};
}

export const liveWatcher = createLiveWatcher({ createSocket: lazySocket });

export const liveClock = readable(Date.now(), (set) => {
	set(Date.now());
	const timer = setInterval(() => set(Date.now()), 1000);
	return () => clearInterval(timer);
});
