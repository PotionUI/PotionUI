import { beforeEach, describe, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import type { GenerationTab } from '$lib/types/tabs';

const state = writable<{ tabs: GenerationTab[] }>({ tabs: [] });
const updates: Array<Partial<GenerationTab>> = [];
const getSessionById = vi.fn();

vi.mock('$lib/services/api', () => ({ api: { getSessionById: (...args: unknown[]) => getSessionById(...args) } }));
vi.mock('$lib/stores/tabs', () => ({
	tabsStore: {
		subscribe: state.subscribe,
		updateTab: (id: string, patch: Partial<GenerationTab>) => {
			updates.push(patch);
			state.update((current) => ({ tabs: current.tabs.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)) }));
		}
	}
}));
vi.mock('$lib/stores/tabDraftSync', () => ({
	tabDraftSync: { settled: vi.fn(async () => true), replaceDraft: vi.fn() }
}));
vi.mock('$lib/stores/toast', () => ({ toasts: { warning: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const { restoreTabSession } = await import('./restoreTabSession');

const LONG_FORM = {
	total_seconds: 24,
	piece_prompts: 'The lamp turns on\nA storm rolls in',
	start_image: '/data/uploads/start.png',
	soundtrack: '/data/uploads/track.wav'
};

function tab(over: Partial<GenerationTab> = {}): GenerationTab {
	return {
		id: 'tab-1',
		name: 'MiniMax-H3',
		selectedPreset: 'preset-1',
		selectedMode: 'long',
		selectedSessionId: 'session-1',
		selectedVariant: null,
		savedSessionSignature: undefined,
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {},
		modeStateByMode: {},
		...over
	} as GenerationTab;
}

function session(data: Record<string, unknown>) {
	return { success: true, data: { id: 'session-1', preset_id: 'preset-1', name: 'S', version: 7, data } };
}

const longData = {
	selectedPreset: 'preset-1',
	selectedMode: 'long',
	prompt: 'A lighthouse at dusk',
	negativePrompt: '',
	promptSegments: [],
	negativePromptSegments: [],
	formData: LONG_FORM
};

const current = () => {
	let value!: GenerationTab;
	state.subscribe((s) => (value = s.tabs[0]))();
	return value;
};

beforeEach(() => {
	updates.length = 0;
	getSessionById.mockReset();
});

describe('restoreTabSession when the session mode is still the tab mode', () => {
	it('applies the mode data to the live form and caches only the other modes', async () => {
		const start = tab();
		state.set({ tabs: [start] });
		getSessionById.mockResolvedValue(session({ long: longData, video: { prompt: 'other', formData: { a: 1 } } }));
		await restoreTabSession(start, { presets: [] });
		const result = current();
		expect(result.formData).toEqual(LONG_FORM);
		expect(result.prompt).toBe('A lighthouse at dusk');
		expect(Object.keys(result.modeStateByMode ?? {})).toEqual(['video']);
	});
});

describe('restoreTabSession when the tab already left the session mode', () => {
	const retired = () => tab({ selectedMode: 'video', formData: { duration: 5 }, prompt: 'keep me' });

	it('caches the stored mode under its own key and leaves the live form alone', async () => {
		const start = retired();
		state.set({ tabs: [start] });
		getSessionById.mockResolvedValue(session({ long: longData }));
		await restoreTabSession({ ...start, selectedMode: 'long' }, { presets: [] });
		const result = current();
		expect(result.selectedMode).toBe('video');
		expect(result.modeStateByMode?.long?.formData).toEqual(LONG_FORM);
		expect(result.modeStateByMode?.long?.prompt).toBe('A lighthouse at dusk');
		expect(result.formData).toEqual({ duration: 5 });
		expect(result.prompt).toBe('keep me');
	});

	it('never leaks the stored keys into the other mode form, in any write', async () => {
		const start = retired();
		state.set({ tabs: [start] });
		getSessionById.mockResolvedValue(session({ long: longData }));
		await restoreTabSession({ ...start, selectedMode: 'long' }, { presets: [] });
		for (const write of updates) {
			const form = (write.formData ?? {}) as Record<string, unknown>;
			for (const key of Object.keys(LONG_FORM)) expect(form).not.toHaveProperty(key);
		}
	});

	it('applies the saved data of the mode the tab is on when the session has some', async () => {
		const start = retired();
		state.set({ tabs: [start] });
		getSessionById.mockResolvedValue(
			session({ long: longData, video: { selectedPreset: 'preset-1', selectedMode: 'video', prompt: 'video prompt', formData: { duration: 8 } } })
		);
		await restoreTabSession({ ...start, selectedMode: 'long' }, { presets: [] });
		const result = current();
		expect(result.formData).toEqual({ duration: 8 });
		expect(result.prompt).toBe('video prompt');
		expect(result.modeStateByMode?.long?.formData).toEqual(LONG_FORM);
		expect(Object.keys(result.modeStateByMode ?? {})).toEqual(['long']);
	});

	it('keeps the saved baseline equal to the server copy so the session stays convertible and clean', async () => {
		const start = retired();
		state.set({ tabs: [start] });
		const saved = session({ long: longData });
		getSessionById.mockResolvedValue(saved);
		await restoreTabSession({ ...start, selectedMode: 'long' }, { presets: [] });
		const baseline = JSON.parse(current().savedSessionSignature as string);
		expect(baseline.long.formData).toEqual(LONG_FORM);
		expect(baseline.long.prompt).toBe('A lighthouse at dusk');
		expect(baseline.video.formData ?? {}).not.toHaveProperty('total_seconds');
	});

	it('is generic: any missing mode is cached under its own key, not only long', async () => {
		const start = tab({ selectedMode: 'video', formData: { duration: 5 } });
		state.set({ tabs: [start] });
		getSessionById.mockResolvedValue(session({ gone_mode: { ...longData, selectedMode: 'gone_mode', formData: { secret: 1 } } }));
		await restoreTabSession({ ...start, selectedMode: 'gone_mode' }, { presets: [] });
		expect(current().modeStateByMode?.gone_mode?.formData).toEqual({ secret: 1 });
		expect(current().formData).toEqual({ duration: 5 });
	});
});
