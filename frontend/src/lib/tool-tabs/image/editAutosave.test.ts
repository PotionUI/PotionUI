import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import type { EditRecord } from '$lib/services/api/edits';
import { createEditAutosave, type AutosaveStatus } from './editAutosave';
import { createEditAutosaveOutbox } from './editAutosaveOutbox';
import { decideRestore, readEditLink, withEditLink, type EditLink } from './editLink';
import { emptyImageState, writeImageState } from './state';
import { resizeDraft } from './steps';

const SOURCE = { kind: 'library' as const, id: 'item-1' };
const DEBOUNCE = 1000;

function payload(steps: number, viewportZoom = 1): Record<string, unknown> {
	let stack = createStack();
	for (let i = 0; i < steps; i += 1) stack = apply(beginEdit(stack, resizeDraft({ width: 100 + i, height: 100, lock: true })));
	const viewport = viewportZoom === 1 ? emptyImageState().viewport : { fit: false, zoom: viewportZoom, panX: 0, panY: 0 };
	return writeImageState({ ...emptyImageState(), stack, viewport });
}

function record(id: string, revision: number, state: Record<string, unknown> = payload(0)): EditRecord {
	return {
		id,
		tool: 'image',
		name: null,
		source: SOURCE,
		thumbnail_upload_id: null,
		revision,
		created_at: null,
		updated_at: null,
		state
	};
}

function failure(status: number, detail: Record<string, unknown> = {}) {
	return { response: { status, data: { detail } } };
}

function memoryStorage() {
	const data = new Map<string, string>();
	return { get: (key: string) => data.get(key) ?? null, set: (key: string, value: string) => void data.set(key, value) };
}

interface Options {
	link?: EditLink | null;
	online?: boolean;
	source?: typeof SOURCE | null;
	initial?: Record<string, unknown>;
}

function setup(options: Options = {}) {
	const outbox = createEditAutosaveOutbox(memoryStorage());
	const edits = {
		create: vi.fn(async (_body: unknown) => ({ success: true, data: { edit: record('ed_new', 1) } })),
		get: vi.fn(async (_id: string) => ({ success: true, data: { edit: record('ed_1', options.link?.revision ?? 1) } })),
		save: vi.fn(async (_id: string, body: { revision: number; state: Record<string, unknown> }) => ({
			success: true,
			data: { edit: record('ed_1', body.revision + 1) }
		}))
	};
	let online = options.online ?? true;
	let stored: EditLink | null = options.link ?? null;
	const statuses: AutosaveStatus[] = [];
	const restored: Array<Record<string, unknown>> = [];
	const initial = options.initial ?? payload(0);
	const make = () =>
		createEditAutosave({
			tabId: 'tab-1',
			edits: edits as never,
			outbox,
			initialState: initial,
			readLink: () => stored,
			source: () => (options.source === undefined ? SOURCE : options.source),
			onLink: (link) => {
				stored = link;
			},
			onStatus: (status) => statuses.push(status),
			onRestore: (state) => {
				restored.push(state);
				return state;
			},
			debounceMs: DEBOUNCE,
			maxBackoffMs: 8000,
			isOnline: () => online
		});
	const engine = make();
	return {
		engine,
		make,
		edits,
		outbox,
		statuses,
		restored,
		stored: () => stored,
		setOnline(next: boolean) {
			online = next;
		},
		async boot() {
			engine.start();
			await vi.advanceTimersByTimeAsync(0);
		},
		async settle(ms = DEBOUNCE) {
			await vi.advanceTimersByTimeAsync(ms);
		}
	};
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.stubGlobal('window', new EventTarget());
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('creating the edit', () => {
	it('creates nothing for a tab that was only opened and looked at', async () => {
		const t = setup();
		await t.boot();
		t.engine.observe(payload(0, 2));
		t.engine.observe(payload(0, 3));
		await t.settle(DEBOUNCE * 3);
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.engine.status()).toBe('idle');
		expect(t.outbox.pending('tab-1')).toBeNull();
	});

	it('creates the edit on the first real change and links the tab to it', async () => {
		const t = setup();
		await t.boot();
		t.engine.observe(payload(1));
		expect(t.engine.status()).toBe('saving');
		await t.settle();
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'image', source: SOURCE, state: payload(1) });
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
		expect(t.engine.status()).toBe('saved');
		expect(t.outbox.pending('tab-1')).toBeNull();
	});

	it('does not create when the picture has no identifiable source', async () => {
		const t = setup({ source: null });
		await t.boot();
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.engine.status()).toBe('idle');
	});

	it('never sends the edit link inside the state', async () => {
		const t = setup();
		await t.boot();
		t.engine.observe(withEditLink(payload(1), { id: 'x', revision: 3 }));
		await t.settle();
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'image', source: SOURCE, state: payload(1) });
	});
});

describe('debounce and the queue', () => {
	it('coalesces a burst of changes into one request carrying the latest state', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 4 } });
		await t.boot();
		t.engine.observe(payload(1));
		await t.settle(DEBOUNCE / 2);
		t.engine.observe(payload(2));
		await t.settle(DEBOUNCE / 2);
		t.engine.observe(payload(3));
		await t.settle(DEBOUNCE - 1);
		expect(t.edits.save).not.toHaveBeenCalled();
		await t.settle(1);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.edits.save).toHaveBeenCalledWith('ed_1', { revision: 4, state: payload(3) });
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 5 });
	});

	it('sends each following change against the revision the last save returned', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		t.engine.observe(payload(1));
		await t.settle();
		t.engine.observe(payload(2));
		await t.settle();
		expect(t.edits.save.mock.calls.map((call) => call[1].revision)).toEqual([1, 2]);
	});

	it('keeps only the latest state while offline and flushes it on reconnect', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 }, online: false });
		await t.boot();
		t.engine.observe(payload(1));
		t.engine.observe(payload(2));
		await t.settle();
		t.engine.observe(payload(3));
		await t.settle(DEBOUNCE * 2);
		expect(t.edits.save).not.toHaveBeenCalled();
		expect(t.engine.status()).toBe('offline');
		expect(t.outbox.pendingTabIds()).toEqual(['tab-1']);
		expect(t.outbox.pending('tab-1')?.state).toEqual(payload(3));
		t.setOnline(true);
		window.dispatchEvent(new Event('online'));
		await t.settle(0);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.edits.save).toHaveBeenCalledWith('ed_1', { revision: 1, state: payload(3) });
		expect(t.engine.status()).toBe('saved');
		expect(t.outbox.pending('tab-1')).toBeNull();
	});

	it('queues a first change made offline as a create and sends it on reconnect', async () => {
		const t = setup({ online: false });
		await t.boot();
		t.engine.observe(payload(2));
		await t.settle();
		expect(t.edits.create).not.toHaveBeenCalled();
		t.setOnline(true);
		window.dispatchEvent(new Event('online'));
		await t.settle(0);
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
	});

	it('retries with backoff while the server fails and then sends only the latest state', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		t.edits.save.mockRejectedValueOnce(failure(503)).mockRejectedValueOnce({ message: 'Network Error' });
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.engine.status()).toBe('offline');
		t.engine.observe(payload(2));
		await t.settle(1000);
		expect(t.edits.save).toHaveBeenCalledTimes(2);
		await t.settle(2000);
		expect(t.edits.save).toHaveBeenCalledTimes(3);
		expect(t.edits.save.mock.calls[2][1].state).toEqual(payload(2));
		expect(t.engine.status()).toBe('saved');
		expect(t.outbox.pending('tab-1')).toBeNull();
	});

	it('flushes pending work when the tab is closed', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		t.engine.observe(payload(1));
		await t.engine.close();
		expect(t.edits.save).toHaveBeenCalledTimes(1);
	});
});

function deferred<T>() {
	let resolve: (value: T) => void = () => undefined;
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
}

describe('changes made while a request is in flight', () => {
	it('sends a change made during a save as a second save on the revision the first returned', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		const gate = deferred<{ success: boolean; data: { edit: EditRecord } }>();
		t.edits.save.mockImplementationOnce(() => gate.promise);
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		t.engine.observe(payload(2));
		gate.resolve({ success: true, data: { edit: record('ed_1', 2) } });
		await t.settle(0);
		expect(t.edits.save).toHaveBeenCalledTimes(2);
		expect(t.edits.save.mock.calls[1]).toEqual(['ed_1', { revision: 2, state: payload(2) }]);
		await t.settle(DEBOUNCE * 2);
		expect(t.edits.save).toHaveBeenCalledTimes(2);
		expect(t.outbox.pending('tab-1')).toBeNull();
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 3 });
		expect(t.engine.status()).toBe('saved');
	});

	it('never creates two edits when a change is made during the create', async () => {
		const t = setup();
		await t.boot();
		const gate = deferred<{ success: boolean; data: { edit: EditRecord } }>();
		t.edits.create.mockImplementationOnce(() => gate.promise);
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		t.engine.observe(payload(2));
		gate.resolve({ success: true, data: { edit: record('ed_new', 1) } });
		await t.settle(0);
		await t.settle(DEBOUNCE * 2);
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.edits.save).toHaveBeenCalledWith('ed_new', { revision: 1, state: payload(2) });
		expect(t.outbox.pending('tab-1')).toBeNull();
	});
});

describe('closing a tab while a save is running', () => {
	it('lets the next engine for the same tab read the link only after that save finished', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		const gate = deferred<{ success: boolean; data: { edit: EditRecord } }>();
		t.edits.save.mockImplementationOnce(() => gate.promise);
		t.engine.observe(payload(1));
		const closing = t.engine.close();
		await t.settle(0);
		expect(t.edits.save).toHaveBeenCalledTimes(1);

		t.edits.get.mockClear();
		t.edits.get.mockResolvedValue({ success: true, data: { edit: record('ed_1', 2) } });
		const next = t.make();
		next.start();
		await t.settle(0);
		expect(t.edits.get).not.toHaveBeenCalled();

		gate.resolve({ success: true, data: { edit: record('ed_1', 2) } });
		await closing;
		await t.settle(0);
		expect(t.edits.get).toHaveBeenCalledTimes(1);
		expect(next.link()).toEqual({ id: 'ed_1', revision: 2 });
		expect(next.status()).toBe('saved');
		expect(t.restored).toEqual([]);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		next.stop();
	});
});

describe('a revision conflict', () => {
	async function conflicted() {
		const t = setup({ link: { id: 'ed_1', revision: 2 } });
		await t.boot();
		t.edits.save.mockRejectedValueOnce(failure(409, { error: 'edit_revision_conflict', revision: 5 }));
		t.engine.observe(payload(1));
		await t.settle();
		return t;
	}

	it('stops autosaving that edit and reports the conflict', async () => {
		const t = await conflicted();
		expect(t.engine.status()).toBe('conflict');
		expect(t.engine.conflictRevision()).toBe(5);
		t.engine.observe(payload(2));
		await t.settle(DEBOUNCE * 5);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.outbox.pending('tab-1')?.state).toEqual(payload(2));
	});

	it('loads the newer version and carries on from its revision', async () => {
		const t = await conflicted();
		const remote = payload(4);
		t.edits.get.mockResolvedValueOnce({ success: true, data: { edit: record('ed_1', 5, remote) } });
		expect(await t.engine.loadNewer()).toBe(true);
		expect(t.restored).toEqual([remote]);
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 5 });
		expect(t.engine.status()).toBe('saved');
		expect(t.outbox.pending('tab-1')).toBeNull();
		t.engine.observe(remote);
		await t.settle(DEBOUNCE * 2);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		t.engine.observe(payload(5));
		await t.settle();
		expect(t.edits.save).toHaveBeenLastCalledWith('ed_1', { revision: 5, state: payload(5) });
	});

	it('keeps mine as a new edit and continues on the copy', async () => {
		const t = await conflicted();
		t.engine.observe(payload(2));
		expect(await t.engine.keepMine()).toBe(true);
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'image', source: SOURCE, state: payload(2) });
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
		expect(t.engine.status()).toBe('saved');
		expect(t.outbox.pending('tab-1')).toBeNull();
		t.engine.observe(payload(3));
		await t.settle();
		expect(t.edits.save).toHaveBeenLastCalledWith('ed_new', { revision: 1, state: payload(3) });
	});

	it('stays in conflict when the copy cannot be created', async () => {
		const t = await conflicted();
		t.edits.create.mockRejectedValueOnce(failure(503));
		expect(await t.engine.keepMine()).toBe(false);
		expect(t.engine.status()).toBe('conflict');
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 2 });
	});

	it('does nothing to resolve when there is no conflict', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		expect(await t.engine.loadNewer()).toBe(false);
		expect(await t.engine.keepMine()).toBe(false);
	});
});

describe('deleted and oversized edits', () => {
	it('keeps working locally after the edit was deleted and creates a fresh one on the next change', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 2 } });
		await t.boot();
		t.edits.save.mockRejectedValueOnce(failure(404, { error: 'edit_not_found' }));
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.stored()).toBeNull();
		expect(t.engine.status()).toBe('idle');
		expect(t.edits.create).not.toHaveBeenCalled();
		t.engine.observe(payload(2));
		await t.settle();
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
	});

	it('pauses with a notice when the state is too large and tries again after the next change', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		t.edits.save.mockRejectedValueOnce(failure(413, { error: 'edit_state_too_large' }));
		t.engine.observe(payload(1));
		await t.settle();
		expect(t.engine.status()).toBe('too-large');
		await t.settle(DEBOUNCE * 10);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.outbox.pending('tab-1')?.state).toEqual(payload(1));
		t.engine.observe(payload(2));
		await t.settle();
		expect(t.edits.save).toHaveBeenCalledTimes(2);
		expect(t.engine.status()).toBe('saved');
	});

	it('pauses on a refused request without retrying in a loop', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 1 } });
		await t.boot();
		t.edits.save.mockRejectedValue(failure(422, { error: 'invalid' }));
		t.engine.observe(payload(1));
		await t.settle(DEBOUNCE * 10);
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.engine.status()).toBe('failed');
	});
});

describe('restoring after a reload', () => {
	it('decides from the local base, the server revision and unsent changes', () => {
		const table: Array<[number, number | null, boolean, string]> = [
			[3, null, false, 'unlink'],
			[3, null, true, 'unlink'],
			[3, 3, false, 'keep'],
			[3, 3, true, 'push-local'],
			[3, 5, false, 'adopt-server'],
			[3, 5, true, 'conflict'],
			[3, 2, false, 'push-local'],
			[3, 2, true, 'push-local']
		];
		for (const [base, server, unsent, expected] of table) {
			expect(decideRestore(base, server, unsent)).toBe(expected);
		}
	});

	it('keeps the local copy when the server has not moved and nothing is unsent', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		t.edits.get.mockResolvedValue({ success: true, data: { edit: record('ed_1', 3) } });
		await t.boot();
		expect(t.restored).toEqual([]);
		expect(t.edits.save).not.toHaveBeenCalled();
		expect(t.engine.status()).toBe('saved');
	});

	it('pushes unsent local changes on top of an unchanged server revision', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		t.outbox.put('tab-1', { source: SOURCE, state: payload(2) });
		t.edits.get.mockResolvedValue({ success: true, data: { edit: record('ed_1', 3) } });
		await t.boot();
		await t.settle(0);
		expect(t.edits.save).toHaveBeenCalledWith('ed_1', { revision: 3, state: payload(2) });
		expect(t.restored).toEqual([]);
	});

	it('adopts a newer server copy when nothing is unsent', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		const remote = payload(3);
		t.edits.get.mockResolvedValue({ success: true, data: { edit: record('ed_1', 6, remote) } });
		await t.boot();
		expect(t.restored).toEqual([remote]);
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 6 });
		t.engine.observe(remote);
		await t.settle(DEBOUNCE * 2);
		expect(t.edits.save).not.toHaveBeenCalled();
	});

	it('treats a newer server copy plus unsent local changes as a conflict and sends nothing', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		t.outbox.put('tab-1', { source: SOURCE, state: payload(2) });
		t.edits.get.mockResolvedValue({ success: true, data: { edit: record('ed_1', 6, payload(3)) } });
		await t.boot();
		await t.settle(DEBOUNCE * 3);
		expect(t.engine.status()).toBe('conflict');
		expect(t.engine.conflictRevision()).toBe(6);
		expect(t.edits.save).not.toHaveBeenCalled();
		expect(t.restored).toEqual([]);
		expect(t.outbox.pending('tab-1')?.state).toEqual(payload(2));
	});

	it('unlinks when the server no longer has the edit and keeps the local work', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		t.outbox.put('tab-1', { source: SOURCE, state: payload(2) });
		t.edits.get.mockRejectedValue(failure(404, { error: 'edit_not_found' }));
		await t.boot();
		await t.settle(DEBOUNCE * 2);
		expect(t.stored()).toBeNull();
		expect(t.restored).toEqual([]);
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.outbox.pending('tab-1')).toBeNull();
	});

	it('creates an edit that was queued before the reload', async () => {
		const t = setup();
		t.outbox.put('tab-1', { source: SOURCE, state: payload(2) });
		await t.boot();
		await t.settle(0);
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'image', source: SOURCE, state: payload(2) });
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
	});

	it('retries the check when the server cannot be reached', async () => {
		const t = setup({ link: { id: 'ed_1', revision: 3 } });
		t.edits.get.mockRejectedValueOnce(failure(503)).mockResolvedValue({ success: true, data: { edit: record('ed_1', 3) } });
		await t.boot();
		expect(t.edits.get).toHaveBeenCalledTimes(1);
		await t.settle(1000);
		expect(t.edits.get).toHaveBeenCalledTimes(2);
		expect(t.engine.status()).toBe('saved');
	});
});

describe('the persisted link', () => {
	it('reads only a well formed link and tolerates tabs that never had one', () => {
		expect(readEditLink({ v: 1, edit: { id: 'ed_1', revision: 4 } })).toEqual({ id: 'ed_1', revision: 4 });
		expect(readEditLink({ v: 1 })).toBeNull();
		expect(readEditLink(null)).toBeNull();
		expect(readEditLink({ edit: { id: '', revision: 1 } })).toBeNull();
		expect(readEditLink({ edit: { id: 'ed_1', revision: 0 } })).toBeNull();
	});

	it('adds and removes the link without touching the rest of the state', () => {
		const base = payload(1);
		const linked = withEditLink(base, { id: 'ed_1', revision: 2 });
		expect(linked.edit).toEqual({ id: 'ed_1', revision: 2 });
		expect(withEditLink(linked, null)).toEqual(base);
	});
});
