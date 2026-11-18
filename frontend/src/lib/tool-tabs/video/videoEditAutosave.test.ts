import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apply, beginEdit } from '$lib/tool-tabs/frame/editStack';
import type { EditRecord } from '$lib/services/api/edits';
import { createEditAutosave, type AutosaveStatus } from '$lib/tool-tabs/image/editAutosave';
import { createEditAutosaveOutbox } from '$lib/tool-tabs/image/editAutosaveOutbox';
import { withEditLink, type EditLink } from '$lib/tool-tabs/image/editLink';
import { videoContentKey, videoEditDoc } from './videoEdit';
import { readVideoState } from './videoState';

const SOURCE = { kind: 'library' as const, id: 'clip-1', name: 'dock.mp4' };
const DEBOUNCE = 1000;
const CLIP = { duration_seconds: 10, width: 1280, height: 720 };

function doc(end: number | null, playhead = 0) {
	const base = readVideoState(null);
	const stack = end === null ? base.stack : apply(beginEdit(base.stack, { id: 'trim', tool: 'trim', label: 'Trim', params: { start: 0, end } } as never));
	return videoEditDoc({ ...base, stack, playhead }, CLIP);
}

function record(id: string, revision: number, state = doc(null)): EditRecord {
	return { id, tool: 'video', name: null, source: SOURCE, thumbnail_upload_id: null, revision, created_at: null, updated_at: null, state };
}

function failure(status: number, detail: Record<string, unknown> = {}) {
	return { response: { status, data: { detail } } };
}

function memoryStorage() {
	const data = new Map<string, string>();
	return { get: (key: string) => data.get(key) ?? null, set: (key: string, value: string) => void data.set(key, value) };
}

function setup(link: EditLink | null = null) {
	const outbox = createEditAutosaveOutbox(memoryStorage());
	const edits = {
		create: vi.fn(async (_body: unknown) => ({ success: true, data: { edit: record('ed_new', 1) } })),
		get: vi.fn(async (_id: string) => ({ success: true, data: { edit: record('ed_1', link?.revision ?? 1) } })),
		save: vi.fn(async (_id: string, body: { revision: number; state: Record<string, unknown> }) => ({ success: true, data: { edit: record('ed_1', body.revision + 1) } }))
	};
	let stored = link;
	const statuses: AutosaveStatus[] = [];
	const restored: Array<Record<string, unknown>> = [];
	const engine = createEditAutosave({
		tabId: 'tab-v',
		tool: 'video',
		contentKey: videoContentKey,
		edits: edits as never,
		outbox,
		initialState: doc(null),
		readLink: () => stored,
		source: () => SOURCE,
		onLink: (next) => {
			stored = next;
		},
		onStatus: (status) => statuses.push(status),
		onRestore: (state) => {
			restored.push(state);
			return state;
		},
		debounceMs: DEBOUNCE,
		maxBackoffMs: 8000,
		isOnline: () => true
	});
	return {
		engine,
		edits,
		outbox,
		statuses,
		restored,
		stored: () => stored,
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

describe('autosaving a clip edit', () => {
	it('creates nothing for a clip that was only watched, scrubbed or looped', async () => {
		const t = setup();
		await t.boot();
		t.engine.observe(doc(null, 3));
		t.engine.observe(doc(null, 7));
		await t.settle(DEBOUNCE * 3);
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.engine.status()).toBe('idle');
	});

	it('creates a video edit with the clip source and the steps on the first change', async () => {
		const t = setup();
		await t.boot();
		t.engine.observe(doc(4));
		expect(t.engine.status()).toBe('saving');
		await t.settle();
		expect(t.edits.create).toHaveBeenCalledTimes(1);
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'video', source: SOURCE, state: doc(4) });
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
		expect(t.engine.status()).toBe('saved');
	});

	it('saves further changes to the same edit at the stored revision, without the link in the state', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		await t.boot();
		t.engine.observe(withEditLink(doc(4), { id: 'ed_1', revision: 1 }));
		await t.settle();
		expect(t.edits.create).not.toHaveBeenCalled();
		expect(t.edits.save).toHaveBeenCalledWith('ed_1', { revision: 1, state: doc(4) });
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 2 });
	});

	it('batches quick changes into one save', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		await t.boot();
		t.engine.observe(doc(2));
		await t.settle(300);
		t.engine.observe(doc(3));
		await t.settle(300);
		t.engine.observe(doc(4));
		await t.settle();
		expect(t.edits.save).toHaveBeenCalledTimes(1);
		expect(t.edits.save).toHaveBeenCalledWith('ed_1', { revision: 1, state: doc(4) });
	});

	it('stops on a conflict, keeps the changes and offers both versions', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		t.edits.save.mockRejectedValueOnce(failure(409, { error: 'edit_revision_conflict', revision: 4 }));
		await t.boot();
		t.engine.observe(doc(4));
		await t.settle();
		expect(t.engine.status()).toBe('conflict');
		expect(t.engine.conflictRevision()).toBe(4);
		expect(t.outbox.pending('tab-v')).not.toBeNull();
	});

	it('keeping mine after a conflict saves the clip as a new video edit', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		t.edits.save.mockRejectedValueOnce(failure(409, { error: 'edit_revision_conflict', revision: 4 }));
		await t.boot();
		t.engine.observe(doc(4));
		await t.settle();
		expect(await t.engine.keepMine()).toBe(true);
		expect(t.edits.create).toHaveBeenCalledWith({ tool: 'video', source: SOURCE, state: doc(4) });
		expect(t.stored()).toEqual({ id: 'ed_new', revision: 1 });
	});

	it('pauses as too large on 413 and keeps the changes in the browser', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		t.edits.save.mockRejectedValueOnce(failure(413, { error: 'edit_state_too_large' }));
		await t.boot();
		t.engine.observe(doc(4));
		await t.settle();
		expect(t.engine.status()).toBe('too-large');
		expect(t.outbox.pending('tab-v')).not.toBeNull();
	});

	it('restores the steps of a newer server version when the tab was linked and has nothing unsent', async () => {
		const t = setup({ id: 'ed_1', revision: 1 });
		t.edits.get.mockResolvedValueOnce({ success: true, data: { edit: record('ed_1', 3, doc(6)) } });
		await t.boot();
		expect(t.restored).toHaveLength(1);
		expect(readVideoState(t.restored[0]).stack.steps).toHaveLength(1);
		expect(t.stored()).toEqual({ id: 'ed_1', revision: 3 });
	});
});
