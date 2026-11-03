import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { ModeBasedSessionData, Session } from '$lib/types/api';
import type { GenerationTab } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import { collectTabSessionData } from '$lib/utils/sessionTabState';
import { createSessionLiveSync, type LiveSyncTabsState } from './sessionLiveSync';

const WINDOW = 'win1';
const seg = (id: string, content: string): Segment => ({ id, content });

function makeTab(id: string, overrides: Partial<GenerationTab> = {}): GenerationTab {
	const tab = {
		id,
		kind: 'generation',
		name: `Tab ${id}`,
		selectedPreset: 'krea2',
		selectedMode: 'txt2img',
		selectedVariant: 'default',
		selectedSessionId: 's1',
		prompt: 'a cat on a roof',
		negativePrompt: '',
		promptSegments: [seg('a', 'a cat'), seg('b', 'on a roof')],
		negativePromptSegments: [],
		formData: { steps: 20, cfg: 4 },
		variables: {},
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		sessionVersion: 1,
		...overrides
	} as unknown as GenerationTab;
	return tab;
}

function withBaseline(tab: GenerationTab): GenerationTab {
	return { ...tab, savedSessionSignature: JSON.stringify(collectTabSessionData(tab, tab.selectedMode, {})) };
}

function sessionFrom(tab: GenerationTab, version: number, edit: (data: ModeBasedSessionData) => void): Session {
	const data = JSON.parse(tab.savedSessionSignature as string) as ModeBasedSessionData;
	edit(data);
	return {
		id: 's1',
		preset_id: 'krea2',
		name: 'Monica',
		data,
		created_at: '2026-11-03T10:00:00Z',
		updated_at: '2026-11-03T10:00:00Z',
		version
	};
}

function harness(initial: GenerationTab[], activeTabId: string) {
	let state: LiveSyncTabsState = { tabs: initial, activeTabId };
	const listeners = new Set<(value: LiveSyncTabsState) => void>();
	const emit = () => listeners.forEach((run) => run(state));
	const getSessionById = vi.fn();
	const info = vi.fn();
	const sync = createSessionLiveSync({
		tabs: {
			subscribe(run) {
				listeners.add(run);
				run(state);
				return () => listeners.delete(run);
			},
			updateTab(tabId, patch) {
				state = { ...state, tabs: state.tabs.map((tab) => (tab.id === tabId ? { ...tab, ...patch } : tab)) };
				emit();
			}
		},
		api: { getSessionById },
		notify: { info },
		windowId: WINDOW,
		highlightMs: 2000
	});
	sync.start();
	return {
		sync,
		getSessionById,
		info,
		tab: (id: string) => state.tabs.find((tab) => tab.id === id)!,
		activate(id: string) {
			state = { ...state, activeTabId: id };
			emit();
		},
		edit(id: string, patch: Partial<GenerationTab>) {
			state = { ...state, tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)) };
			emit();
		}
	};
}

const changed = (version: number, extra: Record<string, unknown> = {}) => ({
	session_id: 's1',
	version,
	origin: { kind: 'mcp', label: 'Claude Code' },
	client_id: null,
	...extra
});

describe('sessionLiveSync', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('fetches the new version and applies a field the tab did not touch', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 4 }))
		});

		await h.sync.receive(changed(2));

		expect(h.getSessionById).toHaveBeenCalledWith('s1');
		expect(h.tab('t1').formData).toEqual({ steps: 30, cfg: 4 });
		expect(h.tab('t1').sessionVersion).toBe(2);
		expect(get(h.sync.marks).t1.highlights).toHaveProperty('form:steps');
		expect(h.sync.hasConflicts('t1')).toBe(false);
	});

	it('records the new version as the clean baseline so the merged tab is not dirty', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		const session = sessionFrom(tab, 2, (d) => {
			d.txt2img.promptSegments = [seg('a', 'a dog'), seg('b', 'on a roof')];
			d.txt2img.prompt = 'a dog on a roof';
		});
		h.getSessionById.mockResolvedValue({ success: true, data: session });

		await h.sync.receive(changed(2));

		const merged = h.tab('t1');
		expect(merged.promptSegments).toEqual([seg('a', 'a dog'), seg('b', 'on a roof')]);
		expect(merged.savedSessionSignature).toBe(JSON.stringify(collectTabSessionData(merged, 'txt2img', session.data)));
	});

	it('merges a change to another mode into that mode\'s cached state without touching the open form', async () => {
		const tab = makeTab('t1');
		const seeded = {
			...tab,
			savedSessionSignature: JSON.stringify(
				collectTabSessionData(tab, 'txt2img', { img2img: { formData: { denoise: 0.5 }, promptSegments: [] } })
			)
		} as GenerationTab;
		const h = harness([seeded], 't1');
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(seeded, 2, (d) => (d.img2img.formData = { denoise: 0.8 }))
		});

		await h.sync.receive(changed(2));

		expect(h.tab('t1').modeStateByMode?.img2img.formData).toEqual({ denoise: 0.8 });
		expect(h.tab('t1').formData).toEqual({ steps: 20, cfg: 4 });
		expect(get(h.sync.marks).t1).toBeUndefined();
	});

	it('ignores its own echo without fetching', async () => {
		const h = harness([withBaseline(makeTab('t1'))], 't1');

		await h.sync.receive(changed(2, { client_id: `${WINDOW}:t1` }));

		expect(h.getSessionById).not.toHaveBeenCalled();
	});

	it('skips a tab that already holds that version', async () => {
		const h = harness([withBaseline(makeTab('t1', { sessionVersion: 3 }))], 't1');

		await h.sync.receive(changed(3));

		expect(h.getSessionById).not.toHaveBeenCalled();
	});

	it('leaves tabs bound to other sessions alone', async () => {
		const h = harness([withBaseline(makeTab('t1', { selectedSessionId: 'other' }))], 't1');

		await h.sync.receive(changed(2));

		expect(h.getSessionById).not.toHaveBeenCalled();
	});

	it('merges into every other tab bound to the session, naming a sibling tab of this window', async () => {
		const writer = withBaseline(makeTab('t1'));
		const reader = withBaseline(makeTab('t2'));
		const h = harness([writer, reader], 't2');
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(writer, 2, (d) => (d.txt2img.formData = { steps: 25, cfg: 4 }))
		});
		h.edit('t2', { formData: { steps: 21, cfg: 4 } });

		await h.sync.receive(changed(2, { client_id: `${WINDOW}:t1`, origin: { kind: 'window', label: 'another window' } }));

		expect(h.tab('t1').sessionVersion).toBe(1);
		expect(get(h.sync.marks).t2.conflicts[0]).toMatchObject({ key: 'form:steps', label: 'another tab' });
	});

	it('keeps the local value and raises one inline conflict when both sides changed a field', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { formData: { steps: 25, cfg: 4 } });
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 6 }))
		});

		await h.sync.receive(changed(2));

		expect(h.tab('t1').formData).toEqual({ steps: 25, cfg: 6 });
		expect(get(h.sync.marks).t1.conflicts).toEqual([
			{ id: 'txt2img|form:steps', mode: 'txt2img', key: 'form:steps', local: 25, remote: 30, label: 'Claude Code' }
		]);
		expect(h.sync.hasConflicts('t1')).toBe(true);
	});

	it('"Use theirs" writes the remote value, clears the conflict and highlights the field', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { formData: { steps: 25, cfg: 4 } });
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 4 }))
		});
		await h.sync.receive(changed(2));
		const saves = vi.fn();
		h.sync.onSaveRequest(saves);

		h.sync.resolve('t1', 'txt2img|form:steps', 'theirs');

		expect(h.tab('t1').formData).toEqual({ steps: 30, cfg: 4 });
		expect(h.sync.hasConflicts('t1')).toBe(false);
		expect(get(h.sync.marks).t1.highlights).toHaveProperty('form:steps');
		expect(saves).not.toHaveBeenCalled();
	});

	it('"Keep mine" leaves the tab value and asks for a save once no conflict is left', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { formData: { steps: 25, cfg: 9 } });
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 6 }))
		});
		await h.sync.receive(changed(2));
		const saves = vi.fn();
		h.sync.onSaveRequest(saves);

		h.sync.resolve('t1', 'txt2img|form:steps', 'mine');
		expect(saves).not.toHaveBeenCalled();
		h.sync.resolve('t1', 'txt2img|form:cfg', 'mine');

		expect(h.tab('t1').formData).toEqual({ steps: 25, cfg: 9 });
		expect(saves).toHaveBeenCalledExactlyOnceWith('t1');
	});

	it('"Keep mine" on a segment the tab had deleted removes it again', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { promptSegments: [seg('b', 'on a roof')] });
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.promptSegments = [seg('a', 'a dog'), seg('b', 'on a roof')]))
		});
		await h.sync.receive(changed(2));
		expect(h.tab('t1').promptSegments?.map((s) => s.id)).toEqual(['a', 'b']);

		h.sync.resolve('t1', 'txt2img|seg:promptSegments:a', 'mine');

		expect(h.tab('t1').promptSegments?.map((s) => s.id)).toEqual(['b']);
	});

	it('marks a background tab unseen with one toast, and clears it when the tab is shown', async () => {
		const background = withBaseline(makeTab('t2', { name: 'Balcony' }));
		const h = harness([withBaseline(makeTab('t1', { selectedSessionId: null })), background], 't1');
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(background, 2, (d) => {
				d.txt2img.formData = { steps: 30, cfg: 6 };
				d.txt2img.selectedVariant = 'turbo';
			})
		});

		await h.sync.receive(changed(2));

		expect(get(h.sync.marks).t2.unseen).toBe(true);
		expect(h.info).toHaveBeenCalledExactlyOnceWith('Claude Code updated Balcony');
		vi.advanceTimersByTime(5000);
		expect(Object.keys(get(h.sync.marks).t2.highlights)).toHaveLength(3);

		h.activate('t2');
		expect(get(h.sync.marks).t2.unseen).toBe(false);
		vi.advanceTimersByTime(2000);
		expect(get(h.sync.marks).t2).toBeUndefined();
	});

	it('does not toast for the tab that is on screen and fades its highlight', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.seed = 42))
		});

		await h.sync.receive(changed(2));

		expect(h.info).not.toHaveBeenCalled();
		expect(get(h.sync.marks).t1.highlights).toHaveProperty('seed');
		vi.advanceTimersByTime(2000);
		expect(get(h.sync.marks).t1).toBeUndefined();
	});

	it('reconciles a refused save from the 409 payload without fetching', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		const session = sessionFrom(tab, 3, (d) => (d.txt2img.formData = { steps: 40, cfg: 4 }));

		await h.sync.reconcile('t1', session, { kind: 'mcp', label: 'Claude Code' }, null);

		expect(h.getSessionById).not.toHaveBeenCalled();
		expect(h.tab('t1').formData).toEqual({ steps: 40, cfg: 4 });
		expect(h.tab('t1').sessionVersion).toBe(3);
		expect(get(h.sync.records).s1.version).toBe(3);
	});

	it('ignores a 409 payload the tab has already caught up with', async () => {
		const tab = withBaseline(makeTab('t1', { sessionVersion: 4 }));
		const h = harness([tab], 't1');
		const session = sessionFrom(tab, 4, (d) => (d.txt2img.formData = { steps: 40, cfg: 4 }));

		await h.sync.reconcile('t1', session, { kind: 'mcp', label: 'Claude Code' }, null);

		expect(h.tab('t1').formData).toEqual({ steps: 20, cfg: 4 });
	});

	it('reconciles its own lost race quietly', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		const session = sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 40, cfg: 4 }));

		await h.sync.reconcile('t1', session, { kind: 'window', label: 'another window' }, `${WINDOW}:t1`);

		expect(h.tab('t1').formData).toEqual({ steps: 40, cfg: 4 });
		expect(get(h.sync.marks).t1).toBeUndefined();
	});

	it('keeps an open conflict through a later change elsewhere and drops it once the remote agrees', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { formData: { steps: 25, cfg: 4 } });
		h.getSessionById.mockResolvedValueOnce({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 4 }))
		});
		await h.sync.receive(changed(2));

		h.getSessionById.mockResolvedValueOnce({
			success: true,
			data: sessionFrom(tab, 3, (d) => (d.txt2img.formData = { steps: 30, cfg: 5 }))
		});
		await h.sync.receive(changed(3));
		expect(get(h.sync.marks).t1.conflicts.map((c) => c.key)).toEqual(['form:steps']);

		h.getSessionById.mockResolvedValueOnce({
			success: true,
			data: sessionFrom(tab, 4, (d) => (d.txt2img.formData = { steps: 25, cfg: 5 }))
		});
		await h.sync.receive(changed(4));
		expect(h.sync.hasConflicts('t1')).toBe(false);
	});

	it('refetches when the answer is older than the announced version', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.getSessionById
			.mockResolvedValueOnce({ success: true, data: sessionFrom(tab, 2, () => {}) })
			.mockResolvedValueOnce({ success: true, data: sessionFrom(tab, 3, (d) => (d.txt2img.seed = 9)) });

		await h.sync.receive(changed(3));

		expect(h.getSessionById).toHaveBeenCalledTimes(2);
		expect(h.tab('t1').seed).toBe(9);
	});

	it('drops pending conflicts when the tab moves to another session', async () => {
		const tab = withBaseline(makeTab('t1'));
		const h = harness([tab], 't1');
		h.edit('t1', { formData: { steps: 25, cfg: 4 } });
		h.getSessionById.mockResolvedValue({
			success: true,
			data: sessionFrom(tab, 2, (d) => (d.txt2img.formData = { steps: 30, cfg: 4 }))
		});
		await h.sync.receive(changed(2));

		h.edit('t1', { selectedSessionId: 's2' });

		expect(h.sync.hasConflicts('t1')).toBe(false);
	});
});
