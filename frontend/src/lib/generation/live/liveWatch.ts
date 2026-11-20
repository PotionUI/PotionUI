import { readable, type Readable } from 'svelte/store';
import {
	applyLiveMessage,
	initialLive,
	isTerminalLive,
	type LiveGeneration,
	type LiveMessage,
	type LiveStatus
} from './liveState';

export const MAX_TILE_WATCHES = 8;

export type LiveWatchKind = 'tile' | 'detail';

export interface LiveSocket {
	connect(): void;
	disconnect(): void;
	subscribe(generationId: string, handler: (message: LiveMessage) => void): void;
	unsubscribe(generationId: string, handler?: (message: LiveMessage) => void): void;
}

export interface LiveWatcherDeps {
	createSocket: () => LiveSocket;
	maxTiles?: number;
}

interface Entry {
	handler: (message: LiveMessage) => void;
	tiles: number;
	details: number;
	subscribed: boolean;
}

export type LiveSnapshot = Readonly<Record<string, LiveGeneration>>;

export interface LiveWatcher {
	states: Readable<LiveSnapshot>;
	watch(id: string, kind: LiveWatchKind, status?: LiveStatus): (() => void) | null;
	activeCount(): number;
	tileCount(): number;
	onTerminal(listener: (state: LiveGeneration) => void): () => void;
}

export function createLiveWatcher(deps: LiveWatcherDeps): LiveWatcher {
	const maxTiles = deps.maxTiles ?? MAX_TILE_WATCHES;
	const entries = new Map<string, Entry>();
	const terminalListeners = new Set<(state: LiveGeneration) => void>();
	let snapshot: Record<string, LiveGeneration> = {};
	let publish: ((value: LiveSnapshot) => void) | null = null;
	let socket: LiveSocket | null = null;

	const states: Readable<LiveSnapshot> = readable<LiveSnapshot>(snapshot, (set) => {
		publish = set;
		set(snapshot);
		return () => {
			publish = null;
		};
	});

	function commit(next: Record<string, LiveGeneration>) {
		snapshot = next;
		publish?.(snapshot);
	}

	function liveEntries(): number {
		let total = 0;
		for (const [id, entry] of entries) {
			if (entry.subscribed && snapshot[id] && !isTerminalLive(snapshot[id].status)) total += 1;
		}
		return total;
	}

	function tileCount(): number {
		let total = 0;
		for (const [id, entry] of entries) {
			if (entry.tiles > 0 && snapshot[id] && !isTerminalLive(snapshot[id].status)) total += 1;
		}
		return total;
	}

	function closeSocketWhenIdle() {
		if (socket && !Array.from(entries.values()).some((entry) => entry.subscribed)) {
			socket.disconnect();
			socket = null;
		}
	}

	function dropSubscription(id: string, entry: Entry) {
		if (!entry.subscribed) return;
		entry.subscribed = false;
		socket?.unsubscribe(id, entry.handler);
		socket?.unsubscribe(id);
		closeSocketWhenIdle();
	}

	function receive(id: string, message: LiveMessage) {
		const entry = entries.get(id);
		const current = snapshot[id];
		if (!entry || !current) return;
		const next = applyLiveMessage(current, message);
		if (next === current) return;
		commit({ ...snapshot, [id]: next });
		if (isTerminalLive(next.status) && !isTerminalLive(current.status)) {
			dropSubscription(id, entry);
			terminalListeners.forEach((listener) => listener(next));
		}
	}

	function watch(id: string, kind: LiveWatchKind, status: LiveStatus = 'running'): (() => void) | null {
		if (!id) return null;
		let entry = entries.get(id);
		if (!entry) {
			if (kind === 'tile' && tileCount() >= maxTiles) return null;
			entry = {
				handler: (message) => receive(id, message),
				tiles: 0,
				details: 0,
				subscribed: false
			};
			entries.set(id, entry);
			commit({ ...snapshot, [id]: initialLive(id, status) });
		} else if (kind === 'tile' && entry.tiles === 0 && entry.details === 0 && tileCount() >= maxTiles) {
			return null;
		}
		if (kind === 'tile') entry.tiles += 1;
		else entry.details += 1;

		const state = snapshot[id];
		if (!entry.subscribed && state && !isTerminalLive(state.status)) {
			entry.subscribed = true;
			if (!socket) {
				socket = deps.createSocket();
				socket.connect();
			}
			socket.subscribe(id, entry.handler);
		}

		let released = false;
		return () => {
			if (released) return;
			released = true;
			const held = entries.get(id);
			if (!held) return;
			if (kind === 'tile') held.tiles -= 1;
			else held.details -= 1;
			if (held.tiles > 0 || held.details > 0) return;
			dropSubscription(id, held);
			entries.delete(id);
			const { [id]: _removed, ...rest } = snapshot;
			commit(rest);
		};
	}

	return {
		states,
		watch,
		activeCount: liveEntries,
		tileCount,
		onTerminal(listener) {
			terminalListeners.add(listener);
			return () => terminalListeners.delete(listener);
		}
	};
}
