import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { createLiveWatcher, type LiveSocket } from './liveWatch';
import type { LiveMessage } from './liveState';

function fakeSocket() {
	const handlers = new Map<string, Set<(m: LiveMessage) => void>>();
	const log = { connects: 0, disconnects: 0, subscribed: [] as string[] };
	const socket: LiveSocket = {
		connect: () => void (log.connects += 1),
		disconnect: () => void (log.disconnects += 1),
		subscribe(id, handler) {
			log.subscribed.push(id);
			if (!handlers.has(id)) handlers.set(id, new Set());
			handlers.get(id)!.add(handler);
		},
		unsubscribe(id, handler) {
			if (handler) handlers.get(id)?.delete(handler);
			else handlers.delete(id);
		}
	};
	const emit = (id: string, message: LiveMessage) => handlers.get(id)?.forEach((h) => h(message));
	return { socket, log, handlers, emit };
}

function setup(maxTiles = 2) {
	const fake = fakeSocket();
	const watcher = createLiveWatcher({ createSocket: () => fake.socket, maxTiles });
	return { fake, watcher };
}

describe('liveWatcher', () => {
	it('subscribes on watch and unsubscribes and disconnects on release', () => {
		const { fake, watcher } = setup();
		const release = watcher.watch('a', 'detail')!;
		expect(fake.log.subscribed).toEqual(['a']);
		expect(fake.log.connects).toBe(1);
		release();
		expect(fake.handlers.has('a')).toBe(false);
		expect(fake.log.disconnects).toBe(1);
		expect(get(watcher.states).a).toBeUndefined();
	});

	it('bounds tile watches and frees a slot on release', () => {
		const { watcher } = setup(2);
		const a = watcher.watch('a', 'tile');
		watcher.watch('b', 'tile');
		expect(watcher.watch('c', 'tile')).toBeNull();
		expect(watcher.tileCount()).toBe(2);
		a!();
		expect(watcher.watch('c', 'tile')).not.toBeNull();
	});

	it('lets the detail watch past the tile bound and shares one subscription', () => {
		const { fake, watcher } = setup(1);
		watcher.watch('a', 'tile');
		expect(watcher.watch('b', 'detail')).not.toBeNull();
		watcher.watch('a', 'detail');
		expect(fake.log.subscribed.filter((id) => id === 'a')).toHaveLength(1);
	});

	it('applies messages to the shared state and drops the subscription when finished', () => {
		const { fake, watcher } = setup();
		const seen: string[] = [];
		watcher.onTerminal((state) => seen.push(state.status));
		watcher.watch('a', 'tile');
		fake.emit('a', { type: 'workbench_update', image: 'data:image/png;base64,AA', replayed: true });
		expect(get(watcher.states).a.preview).toContain('AA');
		fake.emit('a', { type: 'generation_complete' });
		expect(seen).toEqual(['completed']);
		expect(fake.handlers.has('a')).toBe(false);
		expect(watcher.tileCount()).toBe(0);
		expect(fake.log.disconnects).toBe(1);
	});

	it('does not subscribe again to a generation that has already finished', () => {
		const { fake, watcher } = setup();
		const first = watcher.watch('a', 'detail')!;
		fake.emit('a', { type: 'generation_error', message: 'x' });
		watcher.watch('a', 'tile');
		expect(fake.log.subscribed).toEqual(['a']);
		first();
	});
});
