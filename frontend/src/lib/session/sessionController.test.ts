import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { Tab } from '$lib/types/tabs';
import type { Session } from '$lib/types/api';
import {
	createSessionController,
	type SessionController,
	type SessionControllerState,
	type SessionControllerTimers
} from './sessionController';

const PRESET_ID = 'preset-1';
const OTHER_PRESET_ID = 'preset-2';
const MODE = 'image';
const TAB_ID = 'tab-1';
const SESSION_A = 'session-a';
const SESSION_B = 'session-b';

function makeSession(id: string, overrides: Partial<Session> = {}): Session {
	return {
		id,
		preset_id: PRESET_ID,
		name: `Session ${id}`,
		data: { [MODE]: { promptSegments: [{ id: 's', content: 'saved' }] } },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		...overrides
	} as Session;
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	// An unobserved rejection between `reject()` and the controller's own catch
	// would fail the run under vitest's unhandled-rejection guard.
	promise.catch(() => {});
	return { promise, resolve, reject };
}

/** Drains the microtask queue the controller derives its state on. */
async function settle() {
	for (let i = 0; i < 20; i++) await Promise.resolve();
}

function createFakeTabs(seed: Partial<Tab>) {
	let tabs: Tab[] = [{ id: TAB_ID, ...seed } as Tab];
	const subscribers = new Set<(value: { tabs: Tab[] }) => void>();
	/** Only the controller's own writes; a test's `edit` is not recorded. */
	const writes: Array<Partial<Tab>> = [];

	function emit(patch: Partial<Tab>) {
		tabs = tabs.map((tab) => (tab.id === TAB_ID ? { ...tab, ...patch } : tab));
		for (const subscriber of subscribers) subscriber({ tabs });
	}

	return {
		writes,
		get tab() {
			return tabs[0];
		},
		/** A change made outside the controller (the live draft being edited). */
		edit(patch: Partial<Tab>) {
			emit(patch);
		},
		subscribe(run: (value: { tabs: Tab[] }) => void) {
			subscribers.add(run);
			run({ tabs });
			return () => subscribers.delete(run);
		},
		updateTab(_tabId: string, patch: Partial<Tab>) {
			writes.push(patch);
			emit(patch);
		}
	};
}

function createFakeTimers() {
	let time = 0;
	let nextId = 1;
	const entries = new Map<
		number,
		{ handler: () => void; due: number; interval: number | null }
	>();

	const timers: SessionControllerTimers & { advance(ms: number): void; pending(): number } = {
		setInterval(handler, ms) {
			const id = nextId++;
			entries.set(id, { handler, due: time + ms, interval: ms });
			return id;
		},
		clearInterval(id) {
			entries.delete(id);
		},
		setTimeout(handler, ms) {
			const id = nextId++;
			entries.set(id, { handler, due: time + ms, interval: null });
			return id;
		},
		clearTimeout(id) {
			entries.delete(id);
		},
		advance(ms) {
			const target = time + ms;
			for (;;) {
				const next = [...entries.entries()]
					.filter(([, entry]) => entry.due <= target)
					.sort((a, b) => a[1].due - b[1].due)[0];
				if (!next) break;
				const [id, entry] = next;
				time = entry.due;
				if (entry.interval === null) entries.delete(id);
				else entry.due = time + entry.interval;
				entry.handler();
			}
			time = target;
		},
		pending() {
			return entries.size;
		}
	};

	return timers;
}

function createFakeApi() {
	return {
		getSessionsForPreset: vi.fn(async (_presetId: string) => ({
			success: true,
			data: [] as Session[]
		})),
		getSessionById: vi.fn(async (id: string) => ({ success: true, data: makeSession(id) })),
		saveSession: vi.fn(async () => ({ success: true, data: makeSession('new') })),
		updateSession: vi.fn(async (id: string) => ({ success: true, data: makeSession(id) })),
		deleteSession: vi.fn(async () => ({ success: true, data: { message: 'ok' } })),
		getSessionVersions: vi.fn(async () => ({ success: true, data: [] })),
		getSessionVersion: vi.fn(async () => ({
			success: true,
			data: { version: 1, created_at: '2026-01-01T00:00:00Z', data: {} }
		}))
	};
}

function createHarness(options: { tab?: Partial<Tab>; storage?: Record<string, string> } = {}) {
	const api = createFakeApi();
	const tabs = createFakeTabs(options.tab ?? {});
	const timers = createFakeTimers();
	const store = new Map(Object.entries(options.storage ?? {}));
	const toasts = { info: vi.fn(), warning: vi.fn(), error: vi.fn() };
	const logger = { warn: vi.fn(), error: vi.fn() };

	const controller = createSessionController({
		api: api as never,
		tabs,
		storage: {
			get: (key) => store.get(key) ?? null,
			set: (key, value) => void store.set(key, value)
		},
		toasts,
		logger,
		timers
	});

	return { api, tabs, timers, toasts, logger, controller, storage: store };
}

function context(overrides: Record<string, unknown> = {}) {
	return {
		tabId: TAB_ID,
		presetId: PRESET_ID,
		currentMode: MODE,
		presetVersion: undefined,
		modeVariants: [],
		...overrides
	} as never;
}

function baselineWrites(writes: Array<Partial<Tab>>) {
	return writes.filter((write) => 'savedSessionSignature' in write);
}

let harness: ReturnType<typeof createHarness>;
let controller: SessionController;

function draft(content: string) {
	return { promptSegments: [{ id: 's', content }] } as Partial<Tab>;
}

/** Boots a controller already bound to a tab whose live draft differs from the
 *  session's saved baseline — i.e. dirty, with a real session selected. */
async function bootWithDirtySession(options: { storage?: Record<string, string> } = {}) {
	harness = createHarness({
		tab: {
			selectedPreset: PRESET_ID,
			selectedMode: MODE,
			selectedSessionId: SESSION_A,
			savedSessionSignature: JSON.stringify({ [MODE]: { prompt: 'the saved baseline' } }),
			promptSegments: [{ id: 's', content: 'live edit' }]
		} as Partial<Tab>,
		storage: options.storage
	});
	harness.api.getSessionsForPreset.mockResolvedValue({
		success: true,
		data: [makeSession(SESSION_A), makeSession(SESSION_B)]
	});
	controller = harness.controller;
	controller.setContext(context());
	controller.start();
	await settle();
	return harness;
}

/** Boots a controller bound to a tab with no session picked yet, so the reads
 *  under test are the only thing touching the selection. */
async function bootWithoutSession() {
	harness = createHarness({
		tab: { selectedPreset: PRESET_ID, selectedMode: MODE } as Partial<Tab>
	});
	harness.api.getSessionsForPreset.mockResolvedValue({
		success: true,
		data: [makeSession(SESSION_A), makeSession(SESSION_B)]
	});
	controller = harness.controller;
	controller.setContext(context());
	controller.start();
	await settle();
	return harness;
}

function version(versionNumber: number) {
	return {
		version_number: versionNumber,
		created_at: '2026-01-01T00:00:00Z',
		summary: `Save ${versionNumber}`
	};
}

beforeEach(() => {
	vi.clearAllMocks();
});

describe('createSessionController', () => {
	it('binds the tab selection and reports the live draft as unsaved', async () => {
		await bootWithDirtySession();

		const state = get(controller.state);
		expect(state.currentSession?.id).toBe(SESSION_A);
		expect(state.hasUnsavedChanges).toBe(true);
		// Adopting a tab never refetches the session it is already linked to.
		expect(harness.api.getSessionById).not.toHaveBeenCalled();
	});

	it('drops a save that lands after the mode was switched under it', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.updateSession.mockReturnValue(pending.promise as never);

		void controller.quickSave();
		await settle();

		controller.setContext(context({ currentMode: 'video' }));
		await settle();

		const baselinesBefore = baselineWrites(harness.tabs.writes).length;
		pending.resolve({ success: true, data: makeSession(SESSION_A, { name: 'Renamed by save' }) });
		await settle();

		expect(baselineWrites(harness.tabs.writes).length).toBe(baselinesBefore);
		// The saved session's own list entry is still refreshed.
		expect(get(controller.state).sessions.find((s) => s.id === SESSION_A)?.name).toBe(
			'Renamed by save'
		);
	});

	it('drops a save that lands after the preset was switched under it', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.updateSession.mockReturnValue(pending.promise as never);

		void controller.quickSave();
		await settle();

		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		const baselinesBefore = baselineWrites(harness.tabs.writes).length;
		pending.resolve({ success: true, data: makeSession(SESSION_A) });
		await settle();

		expect(baselineWrites(harness.tabs.writes).length).toBe(baselinesBefore);
	});

	it('drops a save that lands after the user selected another session', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.updateSession.mockReturnValue(pending.promise as never);

		void controller.quickSave();
		await settle();

		await controller.select(SESSION_B);
		await settle();
		const baselineForB = harness.tabs.tab.savedSessionSignature;

		pending.resolve({ success: true, data: makeSession(SESSION_A) });
		await settle();

		expect(get(controller.state).currentSession?.id).toBe(SESSION_B);
		expect(harness.tabs.tab.savedSessionSignature).toBe(baselineForB);
	});

	it('refuses to save a session that belongs to another preset and unlinks it', async () => {
		await bootWithDirtySession();
		// The live preset moved on while this session stayed selected.
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		await controller.quickSave();
		await settle();

		expect(harness.api.updateSession).not.toHaveBeenCalled();
		const state = get(controller.state);
		expect(state.error).toBe('Cannot save: Session belongs to a different preset');
		expect(state.currentSession).toBeNull();
		expect(state.selectedSessionId).toBe('');
	});

	it('warns once when the loaded session was saved under an older preset version', async () => {
		await bootWithDirtySession();
		harness.api.getSessionById.mockResolvedValue({
			success: true,
			data: makeSession(SESSION_B, {
				data: { [MODE]: { presetVersion: '1.0.0' } }
			})
		});

		controller.setContext(context({ presetVersion: '2.0.0' }));
		await controller.select(SESSION_B);
		await settle();

		expect(harness.toasts.warning).toHaveBeenCalledTimes(1);
		expect(harness.toasts.warning.mock.calls[0][0]).toContain('1.0.0');
		expect(harness.toasts.warning.mock.calls[0][0]).toContain('2.0.0');
	});

	it('does not warn when the loaded session matches the live preset version', async () => {
		await bootWithDirtySession();
		harness.api.getSessionById.mockResolvedValue({
			success: true,
			data: makeSession(SESSION_B, {
				data: { [MODE]: { presetVersion: '2.0.0' } }
			})
		});

		controller.setContext(context({ presetVersion: '2.0.0' }));
		await controller.select(SESSION_B);
		await settle();

		expect(harness.toasts.warning).not.toHaveBeenCalled();
	});

	it('autosaves on the stored cadence and stops on destroy', async () => {
		await bootWithDirtySession({
			storage: { autoSaveEnabled: 'true', autoSaveInterval: '5000' }
		});

		harness.timers.advance(4999);
		expect(harness.api.updateSession).not.toHaveBeenCalled();

		harness.timers.advance(1);
		await settle();
		expect(harness.api.updateSession).toHaveBeenCalledTimes(1);

		// Still dirty (the draft moved again), so the next tick saves again.
		harness.tabs.edit({ promptSegments: [{ id: 's', content: 'edited again' }] } as Partial<Tab>);
		await settle();
		harness.timers.advance(5000);
		await settle();
		expect(harness.api.updateSession).toHaveBeenCalledTimes(2);

		controller.destroy();
		harness.timers.advance(50000);
		expect(harness.api.updateSession).toHaveBeenCalledTimes(2);
		expect(harness.timers.pending()).toBe(0);
	});

	it('does not autosave when the stored preference is off', async () => {
		await bootWithDirtySession({ storage: { autoSaveEnabled: 'false' } });

		harness.timers.advance(60000);
		await settle();

		expect(harness.api.updateSession).not.toHaveBeenCalled();
	});

	it('publishes nothing and writes no baseline once destroyed mid-save', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.updateSession.mockReturnValue(pending.promise as never);

		void controller.quickSave();
		await settle();

		const emissions: SessionControllerState[] = [];
		const unsubscribe = controller.state.subscribe((state) => emissions.push(state));
		emissions.length = 0;

		controller.destroy();
		const baselinesBefore = baselineWrites(harness.tabs.writes).length;

		pending.resolve({ success: true, data: makeSession(SESSION_A) });
		await settle();

		expect(emissions).toHaveLength(0);
		expect(baselineWrites(harness.tabs.writes).length).toBe(baselinesBefore);
		unsubscribe();
	});

	it('clears the list-load retry timer on destroy', async () => {
		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		harness.api.getSessionsForPreset.mockRejectedValue(new Error('backend down'));
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();

		expect(harness.api.getSessionsForPreset).toHaveBeenCalledTimes(1);
		expect(harness.timers.pending()).toBe(1);

		controller.destroy();
		harness.timers.advance(60000);
		await settle();

		expect(harness.api.getSessionsForPreset).toHaveBeenCalledTimes(1);
		expect(harness.timers.pending()).toBe(0);
	});

	it('retries the list load after a backend outage and binds the linked session', async () => {
		harness = createHarness({
			tab: {
				selectedPreset: PRESET_ID,
				selectedMode: MODE,
				selectedSessionId: SESSION_A,
				savedSessionSignature: null
			} as Partial<Tab>
		});
		harness.api.getSessionsForPreset
			.mockRejectedValueOnce(new Error('backend down'))
			.mockResolvedValue({ success: true, data: [makeSession(SESSION_A)] });
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();
		expect(get(controller.state).currentSession).toBeNull();

		harness.timers.advance(2000);
		await settle();

		expect(get(controller.state).currentSession?.id).toBe(SESSION_A);
		// A null baseline is the deliberate "restored, not yet saved" state.
		expect(get(controller.state).hasUnsavedChanges).toBe(true);
	});

	// Two saves of the SAME selected session share a selection generation, so
	// ordering between them needs its own counter: without one, whichever
	// resolves LAST wins and an older snapshot silently becomes the baseline.
	it('ignores an older save of the same session that lands after a newer one', async () => {
		await bootWithDirtySession();
		const older = deferred<{ success: boolean; data: Session }>();
		const newer = deferred<{ success: boolean; data: Session }>();

		harness.tabs.edit(draft('draft X'));
		harness.api.updateSession.mockReturnValueOnce(older.promise as never);
		void controller.quickSave();
		await settle();

		harness.tabs.edit(draft('draft Y'));
		harness.api.updateSession.mockReturnValueOnce(newer.promise as never);
		void controller.quickSave();
		await settle();

		newer.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 2' }) });
		await settle();

		older.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 1' }) });
		await settle();

		const state = get(controller.state);
		expect(state.currentSession?.name).toBe('version 2');
		expect(harness.tabs.tab.savedSessionSignature).toContain('draft Y');
		expect(harness.tabs.tab.savedSessionSignature).not.toContain('draft X');
		expect(state.hasUnsavedChanges).toBe(false);
	});

	it('reports no error for an older save of the same session that fails after a newer one', async () => {
		await bootWithDirtySession();
		const older = deferred<{ success: boolean; data: Session }>();
		const newer = deferred<{ success: boolean; data: Session }>();

		harness.tabs.edit(draft('draft X'));
		harness.api.updateSession.mockReturnValueOnce(older.promise as never);
		void controller.quickSave();
		await settle();

		harness.tabs.edit(draft('draft Y'));
		harness.api.updateSession.mockReturnValueOnce(newer.promise as never);
		void controller.quickSave();
		await settle();

		newer.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 2' }) });
		await settle();

		older.reject(new Error('network down'));
		await settle();

		const state = get(controller.state);
		expect(state.error).toBeNull();
		expect(state.currentSession?.name).toBe('version 2');
		expect(harness.tabs.tab.savedSessionSignature).toContain('draft Y');
		expect(state.isQuickSaving).toBe(false);
	});

	// The older save is superseded the moment the newer one is ISSUED, not when
	// one of them lands: a stale success that arrives while the newer save is
	// still in flight would otherwise publish a stale snapshot as the baseline,
	// and be left standing there if the newer save then fails.
	it('ignores an older save that lands while a newer save of the same session is in flight', async () => {
		await bootWithDirtySession();
		const bootBaseline = harness.tabs.tab.savedSessionSignature;
		const older = deferred<{ success: boolean; data: Session }>();
		const newer = deferred<{ success: boolean; data: Session }>();

		harness.tabs.edit(draft('draft X'));
		harness.api.updateSession.mockReturnValueOnce(older.promise as never);
		void controller.quickSave();
		await settle();

		harness.tabs.edit(draft('draft Y'));
		harness.api.updateSession.mockReturnValueOnce(newer.promise as never);
		void controller.quickSave();
		await settle();

		older.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 1' }) });
		await settle();

		expect(harness.tabs.tab.savedSessionSignature).toBe(bootBaseline);
		expect(get(controller.state).currentSession?.name).not.toBe('version 1');

		newer.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 2' }) });
		await settle();

		expect(get(controller.state).currentSession?.name).toBe('version 2');
		expect(harness.tabs.tab.savedSessionSignature).toContain('draft Y');
	});

	it('reports no error for an older save that fails while a newer one is in flight', async () => {
		await bootWithDirtySession();
		const older = deferred<{ success: boolean; data: Session }>();
		const newer = deferred<{ success: boolean; data: Session }>();

		harness.tabs.edit(draft('draft X'));
		harness.api.updateSession.mockReturnValueOnce(older.promise as never);
		void controller.quickSave();
		await settle();

		harness.tabs.edit(draft('draft Y'));
		harness.api.updateSession.mockReturnValueOnce(newer.promise as never);
		void controller.quickSave();
		await settle();

		older.reject(new Error('network down'));
		await settle();
		expect(get(controller.state).error).toBeNull();

		newer.resolve({ success: true, data: makeSession(SESSION_A, { name: 'version 2' }) });
		await settle();

		// The newest save succeeded, so nothing is left claiming it failed.
		expect(get(controller.state).error).toBeNull();
		expect(harness.tabs.tab.savedSessionSignature).toContain('draft Y');
	});

	// Across intents the guard is the seq of whatever last wrote the active
	// state: a restore deliberately leaves the tab dirty, and a save issued
	// before it must not quietly mark that restored draft as saved.
	it('ignores a save that lands after a restore replaced the active state', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.updateSession.mockReturnValue(pending.promise as never);
		harness.api.getSessionVersion.mockResolvedValue({
			success: true,
			data: { version: 2, created_at: '2026-01-01T00:00:00Z', data: { [MODE]: { prompt: 'old' } } }
		} as never);

		void controller.quickSave();
		await settle();

		await controller.restoreVersion(SESSION_A, 2);
		await settle();

		pending.resolve({ success: true, data: makeSession(SESSION_A) });
		await settle();

		expect(harness.tabs.tab.savedSessionSignature).toBeNull();
		expect(get(controller.state).hasUnsavedChanges).toBe(true);
	});

	// A save-as that completes after a preset switch belongs to the preset it
	// was started under: its record must not appear in the new preset's list,
	// and it must not answer for a dialog the user has since opened.
	it('keeps a save-as that completes under another preset out of that preset list', async () => {
		const presetASession = makeSession(SESSION_A);
		const presetBSession = makeSession(SESSION_B, { preset_id: OTHER_PRESET_ID });
		const created = makeSession('session-created', { name: 'Created under A' });
		const server: Record<string, Session[]> = {
			[PRESET_ID]: [presetASession],
			[OTHER_PRESET_ID]: [presetBSession]
		};

		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		harness.api.getSessionsForPreset.mockImplementation(async (presetId: string) => ({
			success: true,
			data: server[presetId]
		}));
		controller = harness.controller;
		controller.setContext(context());
		controller.start();
		await settle();
		expect(get(controller.state).sessions).toEqual([presetASession]);

		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.saveSession.mockReturnValue(pending.promise as never);
		const saving = controller.saveAs('Created under A', 'save-as');
		await settle();

		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();
		expect(get(controller.state).sessions).toEqual([presetBSession]);

		// The server accepted it — under preset A.
		server[PRESET_ID] = [created, presetASession];
		pending.resolve({ success: true, data: created });
		await settle();

		// Preset B's list is untouched, and the caller is told not to close the
		// dialog now on screen.
		expect(get(controller.state).sessions).toEqual([presetBSession]);
		expect(get(controller.state).selectedSessionId).toBe('');
		expect(await saving).toBe(false);

		// Switching back re-reads the list, which is where the record shows up.
		controller.setContext(context());
		await settle();
		expect(get(controller.state).sessions).toEqual([created, presetASession]);
	});

	it('closes the dialog and inserts the record for a save-as under the live context', async () => {
		await bootWithDirtySession();
		const created = makeSession('session-created', { name: 'Fresh' });
		harness.api.saveSession.mockResolvedValue({ success: true, data: created });

		expect(await controller.saveAs('Fresh', 'save-as')).toBe(true);
		await settle();

		const state = get(controller.state);
		expect(state.sessions[0]).toEqual(created);
		expect(state.selectedSessionId).toBe('session-created');
		expect(state.hasUnsavedChanges).toBe(false);
	});

	it('leaves the baseline pointing at nothing after restoring a version', async () => {
		await bootWithDirtySession();
		harness.api.getSessionVersion.mockResolvedValue({
			success: true,
			data: { version: 2, created_at: '2026-01-01T00:00:00Z', data: { [MODE]: { prompt: 'old' } } }
		} as never);

		await controller.restoreVersion(SESSION_A, 2);
		await settle();

		const state = get(controller.state);
		expect(state.hasUnsavedChanges).toBe(true);
		expect(harness.tabs.tab.savedSessionSignature).toBeNull();
		expect(state.historySessionId).toBeNull();
		expect(harness.toasts.info).toHaveBeenCalledTimes(1);
	});

	// A completion that also answers a dialog carries two separate rights: to
	// close the dialog the view is showing, and to put down the busy flag it
	// raised. The first belongs to the operation the view is still waiting on;
	// the second belongs to whoever raised it, or the control stays busy.
	it('closes the confirmation and drops the record for a delete under the live context', async () => {
		await bootWithDirtySession();

		expect(await controller.deleteSession()).toBe(true);
		await settle();

		const state = get(controller.state);
		expect(state.sessions.map((entry) => entry.id)).toEqual([SESSION_B]);
		expect(state.selectedSessionId).toBe('');
		expect(state.isSessionLoading).toBe(false);
	});

	it('does not close a delete confirmation opened after the context switched', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: { message: string } }>();
		const second = deferred<{ success: boolean; data: { message: string } }>();
		let call = 0;
		harness.api.deleteSession.mockImplementation(
			(() => (call++ === 0 ? first.promise : second.promise)) as never
		);

		const firstDelete = controller.deleteSession();
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();
		const secondDelete = controller.deleteSession();
		await settle();

		first.resolve({ success: true, data: { message: 'ok' } });
		await settle();
		expect(await firstDelete).toBe(false);

		second.resolve({ success: true, data: { message: 'ok' } });
		await settle();
		expect(await secondDelete).toBe(true);
	});

	it('leaves a newer delete busy when an older one completes under it', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: { message: string } }>();
		const second = deferred<{ success: boolean; data: { message: string } }>();
		let call = 0;
		harness.api.deleteSession.mockImplementation(
			(() => (call++ === 0 ? first.promise : second.promise)) as never
		);

		void controller.deleteSession();
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();
		void controller.deleteSession();
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		first.resolve({ success: true, data: { message: 'ok' } });
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		second.resolve({ success: true, data: { message: 'ok' } });
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(false);
	});

	it('raises no delete toast for a failure under a context the user has left', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: { message: string } }>();
		harness.api.deleteSession.mockReturnValue(pending.promise as never);

		void controller.deleteSession();
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		pending.reject(new Error('server said no'));
		await settle();

		expect(harness.toasts.error).not.toHaveBeenCalled();
	});

	it('raises no delete toast for a failure that lands after destroy', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: { message: string } }>();
		harness.api.deleteSession.mockReturnValue(pending.promise as never);

		const deleting = controller.deleteSession();
		await settle();

		controller.destroy();
		pending.reject(new Error('server said no'));
		await settle();

		expect(harness.toasts.error).not.toHaveBeenCalled();
		expect(await deleting).toBe(false);
	});

	it('does not close a save dialog a newer save-as has taken over', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: Session }>();
		const second = deferred<{ success: boolean; data: Session }>();
		let call = 0;
		harness.api.saveSession.mockImplementation(
			(() => (call++ === 0 ? first.promise : second.promise)) as never
		);

		const firstSave = controller.saveAs('First name', 'save-as');
		await settle();
		const secondSave = controller.saveAs('Second name', 'save-as');
		await settle();

		first.resolve({ success: true, data: makeSession('created-first', { name: 'First name' }) });
		await settle();
		expect(await firstSave).toBe(false);
		// The record the server did accept still belongs in the list.
		expect(get(controller.state).sessions.some((entry) => entry.id === 'created-first')).toBe(true);

		second.resolve({ success: true, data: makeSession('created-second', { name: 'Second name' }) });
		await settle();
		expect(await secondSave).toBe(true);
	});

	it('leaves a newer save-as saving when an older one completes under it', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: Session }>();
		const second = deferred<{ success: boolean; data: Session }>();
		let call = 0;
		harness.api.saveSession.mockImplementation(
			(() => (call++ === 0 ? first.promise : second.promise)) as never
		);

		void controller.saveAs('First name', 'save-as');
		await settle();
		void controller.saveAs('Second name', 'save-as');
		await settle();
		expect(get(controller.state).isSaving).toBe(true);

		first.resolve({ success: true, data: makeSession('created-first', { name: 'First name' }) });
		await settle();
		expect(get(controller.state).isSaving).toBe(true);

		second.resolve({ success: true, data: makeSession('created-second', { name: 'Second name' }) });
		await settle();
		expect(get(controller.state).isSaving).toBe(false);
	});

	it('does not answer for a save dialog when the save-as lands after destroy', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.saveSession.mockReturnValue(pending.promise as never);

		const saving = controller.saveAs('Fresh', 'save-as');
		await settle();

		controller.destroy();
		pending.resolve({ success: true, data: makeSession('created', { name: 'Fresh' }) });
		await settle();

		expect(await saving).toBe(false);
	});

	it('raises no restore toast for a failure under a context the user has left', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: unknown }>();
		harness.api.getSessionVersion.mockReturnValue(pending.promise as never);

		void controller.restoreVersion(SESSION_A, 2);
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		pending.reject(new Error('gone'));
		await settle();

		expect(harness.toasts.error).not.toHaveBeenCalled();
	});

	it('leaves a newer restore in progress when an older one completes under it', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: unknown }>();
		const second = deferred<{ success: boolean; data: unknown }>();
		let call = 0;
		harness.api.getSessionVersion.mockImplementation(
			(() => (call++ === 0 ? first.promise : second.promise)) as never
		);

		void controller.restoreVersion(SESSION_A, 2);
		await settle();
		void controller.restoreVersion(SESSION_A, 3);
		await settle();
		expect(get(controller.state).isRestoringVersion).toBe(true);

		first.resolve({
			success: true,
			data: { version_number: 2, created_at: '2026-01-01T00:00:00Z', summary: 'Save 2', data: {} }
		});
		await settle();
		expect(get(controller.state).isRestoringVersion).toBe(true);

		second.resolve({
			success: true,
			data: { version_number: 3, created_at: '2026-01-01T00:00:00Z', summary: 'Save 3', data: {} }
		});
		await settle();
		expect(get(controller.state).isRestoringVersion).toBe(false);
	});

	// Reads replace the selection, the rows and the loading flags without ever
	// touching the server, so an out-of-order or obsolete completion is just as
	// destructive as a stale save landing on the active state.
	it('ignores a session load that lands after the user picked another session', async () => {
		await bootWithoutSession();
		const first = deferred<{ success: boolean; data: Session }>();
		const second = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockImplementation(
			((id: string) => (id === SESSION_A ? first.promise : second.promise)) as never
		);

		void controller.select(SESSION_A);
		await settle();
		void controller.select(SESSION_B);
		await settle();

		second.resolve({ success: true, data: makeSession(SESSION_B) });
		await settle();
		expect(get(controller.state).currentSession?.id).toBe(SESSION_B);

		first.resolve({ success: true, data: makeSession(SESSION_A) });
		await settle();

		const state = get(controller.state);
		expect(state.selectedSessionId).toBe(SESSION_B);
		expect(state.currentSession?.id).toBe(SESSION_B);
		expect(harness.tabs.tab.selectedSessionId).toBe(SESSION_B);
	});

	it('reports no error for a session load that fails after another session was picked', async () => {
		await bootWithoutSession();
		const first = deferred<{ success: boolean; data: Session }>();
		const second = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockImplementation(
			((id: string) => (id === SESSION_A ? first.promise : second.promise)) as never
		);

		void controller.select(SESSION_A);
		await settle();
		void controller.select(SESSION_B);
		await settle();

		second.resolve({ success: true, data: makeSession(SESSION_B) });
		await settle();

		first.reject(new Error('network down'));
		await settle();
		// Any later edit republishes; a stale field written now would surface then.
		harness.tabs.edit(draft('typing on'));
		await settle();

		const state = get(controller.state);
		expect(state.error).toBeNull();
		expect(state.currentSession?.id).toBe(SESSION_B);
		expect(state.isSessionLoading).toBe(false);
	});

	// The programmatic sync's "this session belongs to another preset" verdict
	// is only true of the preset the read was issued under: applied afterwards
	// it unlinks a session the user never touched.
	it('drops a programmatic session sync that lands after the preset was switched', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(pending.promise as never);

		harness.tabs.edit({ selectedSessionId: SESSION_B } as Partial<Tab>);
		await settle();
		expect(harness.api.getSessionById).toHaveBeenCalledWith(SESSION_B);

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		const writesBefore = harness.tabs.writes.length;
		pending.resolve({ success: true, data: makeSession(SESSION_B) });
		await settle();

		expect(harness.tabs.writes.length).toBe(writesBefore);
		expect(harness.tabs.tab.selectedSessionId).toBe(SESSION_B);
	});

	it('leaves the tab linked when a stale session sync fails with a gone error', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(pending.promise as never);

		harness.tabs.edit({ selectedSessionId: SESSION_B } as Partial<Tab>);
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		const writesBefore = harness.tabs.writes.length;
		// A 404 proves the session is gone — under the preset it was asked for,
		// which is no longer the one on screen.
		pending.reject({ response: { status: 404 } });
		await settle();

		expect(harness.tabs.writes.length).toBe(writesBefore);
		expect(harness.tabs.tab.selectedSessionId).toBe(SESSION_B);
	});

	it('ignores a session list that resolves after the preset was switched under it', async () => {
		const presetASessions = [makeSession(SESSION_A)];
		const presetBSessions = [makeSession(SESSION_B, { preset_id: OTHER_PRESET_ID })];
		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		const pendingA = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockImplementation(
			((presetId: string) =>
				presetId === PRESET_ID
					? pendingA.promise
					: Promise.resolve({ success: true, data: presetBSessions })) as never
		);
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();

		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();
		expect(get(controller.state).sessions).toEqual(presetBSessions);

		pendingA.resolve({ success: true, data: presetASessions });
		await settle();
		harness.tabs.edit(draft('typing on'));
		await settle();

		expect(get(controller.state).sessions).toEqual(presetBSessions);
	});

	it('raises no error and schedules no retry when a list load fails under an obsolete preset', async () => {
		const presetBSessions = [makeSession(SESSION_B, { preset_id: OTHER_PRESET_ID })];
		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		const pendingA = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockImplementation(
			((presetId: string) =>
				presetId === PRESET_ID
					? pendingA.promise
					: Promise.resolve({ success: true, data: presetBSessions })) as never
		);
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();

		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		pendingA.reject(new Error('backend down'));
		await settle();
		harness.tabs.edit(draft('typing on'));
		await settle();

		expect(get(controller.state).error).toBeNull();
		expect(harness.timers.pending()).toBe(0);
	});

	it('installs no list-load retry for a rejection that lands after destroy', async () => {
		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		const pending = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockReturnValue(pending.promise as never);
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();
		expect(harness.timers.pending()).toBe(0);

		controller.destroy();
		pending.reject(new Error('backend down'));
		await settle();

		expect(harness.timers.pending()).toBe(0);
	});

	it('ignores a version list that lands after another session history was opened', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: ReturnType<typeof version>[] }>();
		const second = deferred<{ success: boolean; data: ReturnType<typeof version>[] }>();
		harness.api.getSessionVersions.mockImplementation(
			((id: string) => (id === SESSION_A ? first.promise : second.promise)) as never
		);

		void controller.openHistory(SESSION_A);
		await settle();
		void controller.openHistory(SESSION_B);
		await settle();

		second.resolve({ success: true, data: [version(2)] });
		await settle();
		first.resolve({ success: true, data: [version(1)] });
		await settle();
		harness.tabs.edit(draft('typing on'));
		await settle();

		const state = get(controller.state);
		expect(state.historySessionId).toBe(SESSION_B);
		expect(state.historyVersions.map((entry) => entry.version_number)).toEqual([2]);
		expect(state.isHistoryLoading).toBe(false);
	});

	it('shows no history error for a version list that fails after the panel moved on', async () => {
		await bootWithDirtySession();
		const first = deferred<{ success: boolean; data: ReturnType<typeof version>[] }>();
		const second = deferred<{ success: boolean; data: ReturnType<typeof version>[] }>();
		harness.api.getSessionVersions.mockImplementation(
			((id: string) => (id === SESSION_A ? first.promise : second.promise)) as never
		);

		void controller.openHistory(SESSION_A);
		await settle();
		void controller.openHistory(SESSION_B);
		await settle();

		second.resolve({ success: true, data: [version(2)] });
		await settle();
		first.reject(new Error('history unavailable'));
		await settle();
		harness.tabs.edit(draft('typing on'));
		await settle();

		const state = get(controller.state);
		expect(state.historyError).toBeNull();
		expect(state.historyVersions.map((entry) => entry.version_number)).toEqual([2]);
	});

	it('does not repopulate a history panel the user has closed', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: ReturnType<typeof version>[] }>();
		harness.api.getSessionVersions.mockReturnValue(pending.promise as never);

		void controller.openHistory(SESSION_A);
		await settle();
		controller.closeHistory();

		pending.resolve({ success: true, data: [version(1)] });
		await settle();
		harness.tabs.edit(draft('typing on'));
		await settle();

		const state = get(controller.state);
		expect(state.historySessionId).toBeNull();
		expect(state.historyVersions).toEqual([]);
		expect(state.isHistoryLoading).toBe(false);
	});

	// A read the context switch retired is not coming back to lower its own
	// flag, and it may never arrive at all. The new context must not wait on it.
	it('is not left busy by a selection the context switch retired', async () => {
		await bootWithDirtySession();
		const stalled = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(stalled.promise as never);

		void controller.select(SESSION_B);
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		// The new preset's list has answered; the old detail never will.
		const state = get(controller.state);
		expect(state.isSessionLoading).toBe(false);
		expect(state.error).toBeNull();
	});

	it('does not let a retired selection lower the flag of the one that replaced it', async () => {
		await bootWithDirtySession();
		const stalled = deferred<{ success: boolean; data: Session }>();
		const replacement = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockImplementation(
			((id: string) => (id === SESSION_B ? stalled.promise : replacement.promise)) as never
		);

		void controller.select(SESSION_B);
		await settle();

		harness.api.getSessionsForPreset.mockResolvedValue({
			success: true,
			data: [makeSession('session-c', { preset_id: OTHER_PRESET_ID })]
		});
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		void controller.select('session-c');
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		stalled.resolve({ success: true, data: makeSession(SESSION_B) });
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		replacement.resolve({
			success: true,
			data: makeSession('session-c', { preset_id: OTHER_PRESET_ID })
		});
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(false);
	});

	it('drops a selection error that belonged to the context the user left', async () => {
		await bootWithDirtySession();
		harness.api.getSessionById.mockRejectedValue(new Error('detail failed'));

		await controller.select(SESSION_B);
		await settle();
		expect(get(controller.state).error).toBe('detail failed');

		harness.api.getSessionsForPreset.mockResolvedValue({ success: true, data: [] });
		controller.setContext(context({ presetId: OTHER_PRESET_ID }));
		await settle();

		expect(get(controller.state).error).toBeNull();
	});

	it('drops a list error when the preset it belonged to is left', async () => {
		harness = createHarness({ tab: { selectedPreset: PRESET_ID, selectedMode: MODE } });
		harness.api.getSessionsForPreset.mockRejectedValue(new Error('backend down'));
		controller = harness.controller;

		controller.setContext(context());
		controller.start();
		await settle();
		expect(get(controller.state).error).toBe('backend down');

		controller.setContext(context({ presetId: null }));
		await settle();

		const state = get(controller.state);
		expect(state.error).toBeNull();
		expect(state.isSessionLoading).toBe(false);
	});

	// A dialog the user cancelled and reopened is a different dialog, even
	// though no second attempt was submitted and nothing about the context or
	// the command handle changed.
	it('does not answer for a save dialog reopened after the attempt was cancelled', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.saveSession.mockReturnValue(pending.promise as never);

		controller.openDialog('save');
		const saving = controller.saveAs('Fresh', 'save-as');
		await settle();

		controller.closeDialog('save');
		controller.openDialog('save');

		pending.resolve({ success: true, data: makeSession('created', { name: 'Fresh' }) });
		await settle();

		expect(await saving).toBe(false);
		// The record the server accepted still belongs in the list.
		expect(get(controller.state).sessions.some((entry) => entry.id === 'created')).toBe(true);
	});

	it('does not repopulate the name error of a save dialog reopened after a cancel', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.saveSession.mockReturnValue(pending.promise as never);

		controller.openDialog('save');
		void controller.saveAs('Fresh', 'save-as');
		await settle();

		controller.closeDialog('save');
		controller.openDialog('save');
		controller.clearNameError();

		pending.reject(new Error('server refused'));
		await settle();

		expect(get(controller.state).nameError).toBe('');
	});

	it('does not annotate a save dialog the user cancelled without reopening', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: Session }>();
		harness.api.saveSession.mockReturnValue(pending.promise as never);

		controller.openDialog('save');
		const saving = controller.saveAs('Fresh', 'save-as');
		await settle();

		controller.closeDialog('save');

		pending.reject(new Error('server refused'));
		await settle();

		expect(get(controller.state).nameError).toBe('');
		expect(await saving).toBe(false);
	});

	it('does not close a delete confirmation reopened after the attempt was cancelled', async () => {
		await bootWithDirtySession();
		const pending = deferred<{ success: boolean; data: { message: string } }>();
		harness.api.deleteSession.mockReturnValue(pending.promise as never);

		controller.openDialog('delete');
		const deleting = controller.deleteSession();
		await settle();

		controller.closeDialog('delete');
		controller.openDialog('delete');

		pending.resolve({ success: true, data: { message: 'ok' } });
		await settle();

		expect(await deleting).toBe(false);
		expect(get(controller.state).sessions.some((entry) => entry.id === SESSION_A)).toBe(false);
	});

	it('does not close a history panel reopened while a restore was applying', async () => {
		await bootWithDirtySession();
		harness.api.getSessionVersions.mockResolvedValue({ success: true, data: [version(1)] });
		const pending = deferred<{ success: boolean; data: unknown }>();
		harness.api.getSessionVersion.mockReturnValue(pending.promise as never);

		await controller.openHistory(SESSION_A);
		await settle();

		void controller.restoreVersion(SESSION_A, 2);
		await settle();

		controller.closeHistory();
		await controller.openHistory(SESSION_A);
		await settle();

		pending.resolve({
			success: true,
			data: { version_number: 2, created_at: '2026-01-01T00:00:00Z', summary: 'Save 2', data: {} }
		});
		await settle();

		// The restore itself still landed; only the panel and its notice are
		// scoped to the opening that asked for it.
		expect(harness.tabs.tab.savedSessionSignature).toBeNull();
		expect(get(controller.state).hasUnsavedChanges).toBe(true);
		expect(get(controller.state).historySessionId).toBe(SESSION_A);
		expect(harness.toasts.info).not.toHaveBeenCalled();
	});

	// The list read and the selection read are ordered against their own kind
	// only, yet both publish the busy flag and the error, and the list also
	// binds currentSession from a row. A list refresh that overlaps a selection
	// therefore has to keep its hands off what the selection owns.
	it('keeps the busy state with a selection when an older list refresh finishes under it', async () => {
		await bootWithDirtySession();
		const listPending = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockReturnValue(listPending.promise as never);
		void controller.loadSessions();
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		const detailPending = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(detailPending.promise as never);
		void controller.select(SESSION_B);
		await settle();

		listPending.resolve({ success: true, data: [makeSession(SESSION_A), makeSession(SESSION_B)] });
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(true);

		detailPending.resolve({ success: true, data: makeSession(SESSION_B) });
		await settle();
		expect(get(controller.state).isSessionLoading).toBe(false);
	});

	it('leaves a selection error standing when an older list load fails under it', async () => {
		await bootWithDirtySession();
		const listPending = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockReturnValue(listPending.promise as never);
		void controller.loadSessions();
		await settle();

		const detailPending = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(detailPending.promise as never);
		void controller.select(SESSION_B);
		await settle();

		detailPending.reject(new Error('session fetch failed'));
		await settle();
		expect(get(controller.state).error).toBe('session fetch failed');

		listPending.reject(new Error('list fetch failed'));
		await settle();
		expect(get(controller.state).error).toBe('session fetch failed');
	});

	it('does not replace a freshly hydrated session with an older list summary', async () => {
		await bootWithDirtySession();
		const listPending = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockReturnValue(listPending.promise as never);
		void controller.loadSessions();
		await settle();

		const detailPending = deferred<{ success: boolean; data: Session }>();
		harness.api.getSessionById.mockReturnValue(detailPending.promise as never);
		void controller.select(SESSION_B);
		await settle();

		detailPending.resolve({ success: true, data: makeSession(SESSION_B, { name: 'B detail' }) });
		await settle();
		expect(get(controller.state).currentSession?.name).toBe('B detail');

		listPending.resolve({
			success: true,
			data: [makeSession(SESSION_A), makeSession(SESSION_B, { name: 'B summary' })]
		});
		await settle();

		const state = get(controller.state);
		expect(state.currentSession?.name).toBe('B detail');
		// The rows themselves are still the list's to publish.
		expect(state.sessions.find((entry) => entry.id === SESSION_B)?.name).toBe('B summary');
	});

	it('does not replace a freshly saved session with an older list summary', async () => {
		await bootWithDirtySession();
		const listPending = deferred<{ success: boolean; data: Session[] }>();
		harness.api.getSessionsForPreset.mockReturnValue(listPending.promise as never);
		void controller.loadSessions();
		await settle();

		harness.api.updateSession.mockResolvedValue({
			success: true,
			data: makeSession(SESSION_A, { name: 'Saved just now' })
		});
		await controller.quickSave();
		await settle();
		expect(get(controller.state).currentSession?.name).toBe('Saved just now');

		listPending.resolve({
			success: true,
			data: [makeSession(SESSION_A, { name: 'Stale summary' })]
		});
		await settle();

		const state = get(controller.state);
		expect(state.currentSession?.name).toBe('Saved just now');
		expect(state.sessions.find((entry) => entry.id === SESSION_A)?.name).toBe('Stale summary');
	});
});
