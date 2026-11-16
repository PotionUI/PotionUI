import type { APIResponse } from '$lib/types/api';
import {
	describeEditFailure,
	type CreateEditBody,
	type EditRecord,
	type EditSource,
	type EditStateDoc,
	type EditSummary,
	type SaveEditBody
} from '$lib/services/api/edits';
import type { EditAutosaveOutbox } from './editAutosaveOutbox';
import { contentKey, decideRestore, withEditLink, type EditLink } from './editLink';

export const EDIT_AUTOSAVE_DEBOUNCE_MS = 1500;
export const EDIT_AUTOSAVE_MAX_BACKOFF_MS = 30000;

export type AutosaveStatus = 'idle' | 'saved' | 'saving' | 'offline' | 'failed' | 'conflict' | 'too-large';

export interface EditsPort {
	create(body: CreateEditBody): Promise<APIResponse<{ edit: EditRecord }>>;
	get(id: string): Promise<APIResponse<{ edit: EditRecord }>>;
	save(id: string, body: SaveEditBody): Promise<APIResponse<{ edit: EditSummary }>>;
}

export interface EditAutosaveDeps {
	tabId: string;
	edits: EditsPort;
	outbox: EditAutosaveOutbox;
	initialState: EditStateDoc;
	readLink: () => EditLink | null;
	source: () => EditSource | null;
	onLink: (link: EditLink | null) => void;
	onStatus: (status: AutosaveStatus) => void;
	onRestore: (state: EditStateDoc) => EditStateDoc | void;
	debounceMs?: number;
	maxBackoffMs?: number;
	isOnline?: () => boolean;
}

type SendResult = 'sent' | 'stop' | 'retry' | 'stale';

const inflight = new Map<string, Promise<void>>();

export function createEditAutosave(deps: EditAutosaveDeps) {
	const { tabId, edits, outbox } = deps;
	const debounceMs = deps.debounceMs ?? EDIT_AUTOSAVE_DEBOUNCE_MS;
	const maxBackoffMs = deps.maxBackoffMs ?? EDIT_AUTOSAVE_MAX_BACKOFF_MS;
	const isOnline = deps.isOnline ?? (() => typeof navigator === 'undefined' || navigator.onLine !== false);

	let link: EditLink | null = deps.readLink();
	let latest: EditStateDoc = deps.initialState;
	let lastKey = contentKey(deps.initialState);
	let changes = 0;
	let conflict: { revision: number | null } | null = null;
	let paused: 'too-large' | 'failed' | null = null;
	let status: AutosaveStatus = 'idle';
	let started = false;
	let booted = false;
	let booting = false;
	let flushing = false;
	let flushAgain = false;
	let session = 0;
	let retryAttempt = 0;
	let flushTimer: ReturnType<typeof setTimeout> | null = null;
	let retryTimer: ReturnType<typeof setTimeout> | null = null;

	function computeStatus(): AutosaveStatus {
		if (conflict) return 'conflict';
		if (paused) return paused;
		if (outbox.pending(tabId)) return !isOnline() || retryAttempt > 0 ? 'offline' : 'saving';
		return link ? 'saved' : 'idle';
	}

	function emit(): void {
		const next = computeStatus();
		if (next === status) return;
		status = next;
		deps.onStatus(next);
	}

	function setLink(next: EditLink | null): void {
		link = next;
		deps.onLink(next);
	}

	function schedule(delay = debounceMs): void {
		if (!started || !booted) return;
		if (flushTimer) clearTimeout(flushTimer);
		if (retryTimer) clearTimeout(retryTimer);
		retryTimer = null;
		flushTimer = setTimeout(() => {
			flushTimer = null;
			void flush();
		}, delay);
	}

	function scheduleRetry(): void {
		if (!started) return;
		if (retryTimer) clearTimeout(retryTimer);
		const delay = Math.min(1000 * 2 ** retryAttempt, maxBackoffMs);
		retryAttempt += 1;
		retryTimer = setTimeout(() => {
			retryTimer = null;
			if (booted) void flush();
			else void boot();
		}, delay);
	}

	function adopt(server: EditRecord): void {
		const applied = deps.onRestore(server.state);
		const state = applied ?? server.state;
		latest = state;
		lastKey = contentKey(state);
		setLink({ id: server.id, revision: server.revision });
	}

	function failureResult(error: unknown): SendResult {
		const failure = describeEditFailure(error);
		if (failure.kind === 'conflict') {
			conflict = { revision: failure.revision };
			return 'stop';
		}
		if (failure.kind === 'not_found' && link) {
			outbox.drop(tabId);
			setLink(null);
			return 'stop';
		}
		if (failure.kind === 'too_large') {
			paused = 'too-large';
			return 'stop';
		}
		if (failure.kind === 'network' || failure.kind === 'server') return 'retry';
		paused = 'failed';
		return 'stop';
	}

	async function send(): Promise<SendResult> {
		const mine = session;
		const entry = outbox.pending(tabId);
		if (!entry) return 'stop';
		const state = withEditLink(entry.state, null);
		if (!link) {
			const source = entry.source ?? deps.source();
			if (!source) {
				outbox.drop(tabId);
				return 'stop';
			}
			try {
				const response = await edits.create({ tool: 'image', source, state });
				if (mine !== session) return 'stale';
				const edit = response.data?.edit;
				if (!edit) {
					paused = 'failed';
					return 'stop';
				}
				setLink({ id: edit.id, revision: edit.revision });
				outbox.ack(tabId, entry);
				return 'sent';
			} catch (error) {
				if (mine !== session) return 'stale';
				return failureResult(error);
			}
		}
		const target = link;
		try {
			const response = await edits.save(target.id, { revision: target.revision, state });
			if (mine !== session) return 'stale';
			const edit = response.data?.edit;
			if (!edit) {
				paused = 'failed';
				return 'stop';
			}
			setLink({ id: target.id, revision: edit.revision });
			outbox.ack(tabId, entry);
			return 'sent';
		} catch (error) {
			if (mine !== session) return 'stale';
			return failureResult(error);
		}
	}

	async function flush(): Promise<void> {
		if (!started || !booted) return;
		if (flushing) {
			flushAgain = true;
			return;
		}
		if (conflict || paused || !outbox.pending(tabId)) {
			emit();
			return;
		}
		if (!isOnline()) {
			emit();
			return;
		}
		const mine = session;
		flushing = true;
		let retry = false;
		try {
			for (;;) {
				const result = await send();
				if (result === 'stale') return;
				if (result === 'retry') {
					retry = true;
					break;
				}
				if (result === 'stop') break;
				if (!outbox.pending(tabId)) break;
			}
		} finally {
			if (mine === session) flushing = false;
		}
		if (retry) {
			scheduleRetry();
			emit();
			return;
		}
		retryAttempt = 0;
		emit();
		if (flushAgain) {
			flushAgain = false;
			void flush();
		}
	}

	async function boot(): Promise<void> {
		if (!started || booted || booting) return;
		const mine = session;
		booting = true;
		try {
			const previous = inflight.get(tabId);
			if (previous) {
				await previous;
				if (mine !== session) return;
				link = deps.readLink();
			}
			if (!link) {
				booted = true;
				emit();
				if (outbox.pending(tabId)) schedule(0);
				return;
			}
			if (!isOnline()) {
				emit();
				return;
			}
			let server: EditRecord | undefined;
			try {
				server = (await edits.get(link.id)).data?.edit;
			} catch (error) {
				if (mine !== session) return;
				const failure = describeEditFailure(error);
				if (failure.kind === 'not_found') server = undefined;
				else {
					scheduleRetry();
					emit();
					return;
				}
			}
			if (mine !== session) return;
			const action = decideRestore(link.revision, server ? server.revision : null, !!outbox.pending(tabId));
			if (action === 'unlink') {
				outbox.drop(tabId);
				setLink(null);
			} else if (server && action === 'adopt-server') {
				adopt(server);
			} else if (server && action === 'conflict') {
				conflict = { revision: server.revision };
			} else if (server && action === 'push-local') {
				setLink({ id: link.id, revision: server.revision });
				if (!outbox.pending(tabId)) outbox.put(tabId, { source: deps.source(), state: latest });
			}
			booted = true;
			retryAttempt = 0;
			emit();
			if (outbox.pending(tabId) && !conflict) schedule(0);
		} finally {
			if (mine === session) booting = false;
		}
	}

	function onOnline(): void {
		retryAttempt = 0;
		if (retryTimer) clearTimeout(retryTimer);
		retryTimer = null;
		if (booted) void flush();
		else void boot();
	}

	function onOffline(): void {
		emit();
	}

	function start(): void {
		if (started) return;
		started = true;
		session += 1;
		if (typeof window !== 'undefined') {
			window.addEventListener('online', onOnline);
			window.addEventListener('offline', onOffline);
		}
		void boot();
	}

	function stop(): void {
		if (!started) return;
		started = false;
		session += 1;
		flushing = false;
		flushAgain = false;
		booting = false;
		booted = false;
		if (typeof window !== 'undefined') {
			window.removeEventListener('online', onOnline);
			window.removeEventListener('offline', onOffline);
		}
		if (flushTimer) clearTimeout(flushTimer);
		if (retryTimer) clearTimeout(retryTimer);
		flushTimer = null;
		retryTimer = null;
	}

	async function close(): Promise<void> {
		if (!started) return;
		if (flushTimer) clearTimeout(flushTimer);
		flushTimer = null;
		const settle = (async () => {
			try {
				if (booted && !conflict && outbox.pending(tabId) && isOnline()) await flush();
			} finally {
				stop();
			}
		})();
		inflight.set(tabId, settle);
		await settle;
		if (inflight.get(tabId) === settle) inflight.delete(tabId);
	}

	function observe(payload: EditStateDoc): void {
		latest = payload;
		const key = contentKey(payload);
		if (key === lastKey) return;
		lastKey = key;
		changes += 1;
		if (!started) return;
		const source = deps.source();
		if (!link && !source && !outbox.pending(tabId)) return;
		outbox.put(tabId, { source: source ?? outbox.pending(tabId)?.source ?? null, state: payload });
		if (conflict) return;
		paused = null;
		emit();
		schedule();
	}

	async function loadNewer(): Promise<boolean> {
		if (!conflict || !link) return false;
		const id = link.id;
		try {
			const server = (await edits.get(id)).data?.edit;
			if (!server) return false;
			adopt(server);
		} catch (error) {
			if (describeEditFailure(error).kind !== 'not_found') return false;
			setLink(null);
		}
		conflict = null;
		paused = null;
		outbox.drop(tabId);
		retryAttempt = 0;
		emit();
		return true;
	}

	async function keepMine(): Promise<boolean> {
		if (!conflict) return false;
		const source = deps.source() ?? outbox.pending(tabId)?.source ?? null;
		if (!source) return false;
		const mark = changes;
		const state = withEditLink(latest, null);
		try {
			const edit = (await edits.create({ tool: 'image', source, state })).data?.edit;
			if (!edit) return false;
			conflict = null;
			paused = null;
			setLink({ id: edit.id, revision: edit.revision });
		} catch {
			return false;
		}
		if (changes !== mark) outbox.put(tabId, { source, state: latest });
		else outbox.drop(tabId);
		retryAttempt = 0;
		emit();
		if (outbox.pending(tabId)) schedule();
		return true;
	}

	async function flushNow(): Promise<void> {
		if (flushTimer) clearTimeout(flushTimer);
		flushTimer = null;
		await flush();
	}

	return {
		start,
		stop,
		close,
		observe,
		flushNow,
		loadNewer,
		keepMine,
		status: () => status,
		link: () => link,
		conflictRevision: () => conflict?.revision ?? null
	};
}

export type EditAutosave = ReturnType<typeof createEditAutosave>;
