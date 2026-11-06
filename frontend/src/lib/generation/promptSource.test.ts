import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { prepareRequest } from './prepareRequest';
import { tabsStore } from '$lib/stores/tabs';
import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
import { buildModeSwitchPatch, modeStateFromSessionData } from '$lib/utils/modeState';
import { createBlankEditorSegment } from '$lib/utils/richSegments';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { tabHasUnsavedSessionChanges } from '$lib/utils/unsavedChangesGuard';
import type { GenerationTab } from '$lib/types/tabs';

const STALE = 'best quality, score_7, 1girl, solo, sitting, looking at viewer';

function request(tab: Partial<GenerationTab>, optional = true) {
	return prepareRequest({
		tab: {
			selectedMode: 'talk',
			selectedVariant: null,
			formData: { seed: 1, quantity: 1 },
			prompt: '',
			negativePrompt: '',
			promptSegments: [],
			negativePromptSegments: [],
			activePromptTab: 0,
			autoTagIds: [],
			sourcePromptId: null,
			...tab,
			selectedPreset: 'MiniMax-H3'
		} as never,
		tabId: 't',
		canBatch: true,
		segmentJoin: 'space',
		promptRelayActive: false,
		promptlessActive: false,
		promptOptionalActive: optional,
		videoDirector: null,
		musicDirector: null,
		random: () => 0,
		now: () => 1
	} as never);
}

function sent(tab: Partial<GenerationTab>): string[] {
	const result = request(tab);
	if (!result.ok) throw new Error(result.code);
	return (result.request.prompts ?? []).map((p) => p.positive);
}

function visible(tab: Pick<GenerationTab, 'promptSegments'>): string {
	return resolvePromptSegments(tab.promptSegments || []).trim();
}

function activeTab(): GenerationTab {
	const state = get(tabsStore);
	return generationTabs(state.tabs).find((t) => t.id === state.activeTabId)!;
}

describe('the request carries what the prompt editor shows', () => {
	beforeEach(() => tabsStore.reset());

	it('sends nothing when the editor holds only its blank placeholder, whatever tab.prompt says', () => {
		expect(sent({ prompt: STALE, promptSegments: [createBlankEditorSegment()] })).toEqual(['']);
	});

	it('sends nothing when the segment list is empty, whatever tab.prompt says', () => {
		expect(sent({ prompt: STALE, promptSegments: [] })).toEqual(['']);
	});

	it('sends nothing for a stale plain string on the negative side too', () => {
		const result = request({ negativePrompt: STALE, negativePromptSegments: [createBlankEditorSegment()] });
		expect(result.ok && result.request.prompts?.[0].negative).toBe('');
	});

	it('sends the plain string of every batch entry that has no segments, never an unseen one', () => {
		const entry = (prompt: string) => ({
			prompt,
			negativePrompt: '',
			promptSegments: [createBlankEditorSegment()],
			negativePromptSegments: []
		});
		const promptTabs = [entry(STALE), entry('')];
		expect(sent({ promptTabs, activePromptTab: 0, ...promptTabs[0] })).toEqual(['', '']);
	});

	it('a prompt written only as a string reaches the editor instead of staying invisible', () => {
		const id = get(tabsStore).activeTabId;
		tabsStore.updateTab(id, { prompt: STALE, promptSegments: [] });
		const tab = activeTab();
		expect(visible(tab)).toBe(STALE);
		expect(sent(tab)).toEqual([STALE]);
	});

	it('a prompt string over a blank placeholder reaches the editor', () => {
		const id = get(tabsStore).activeTabId;
		tabsStore.updateTab(id, { prompt: STALE, promptSegments: [createBlankEditorSegment()] });
		expect(visible(activeTab())).toBe(STALE);
	});

	it('a restored session that only stored the plain prompt loads it into the editor', () => {
		const id = get(tabsStore).activeTabId;
		tabsStore.updateTab(id, buildSessionRestoreTabPatch({ prompt: STALE, negativePrompt: 'blur' }));
		const tab = activeTab();
		expect(visible(tab)).toBe(STALE);
		expect(resolvePromptSegments(tab.negativePromptSegments || []).trim()).toBe('blur');
		expect(sent(tab)).toEqual([STALE]);
	});

	it('a restored session with segments keeps them as the only source', () => {
		const id = get(tabsStore).activeTabId;
		const segs = [{ id: 's', type: 'content', content: 'new text', chips: {}, enabled: true }];
		tabsStore.updateTab(id, buildSessionRestoreTabPatch({ prompt: STALE, promptSegments: segs as never }));
		expect(sent(activeTab())).toEqual(['new text']);
	});

	it('switching to a mode cached from a plain-prompt session shows that prompt', () => {
		const id = get(tabsStore).activeTabId;
		tabsStore.updateTab(id, {
			selectedPreset: 'p',
			selectedMode: 'a',
			modeStateByMode: { talk: modeStateFromSessionData({ prompt: STALE }) }
		});
		tabsStore.updateTab(id, { ...buildModeSwitchPatch(activeTab(), 'a', 'talk'), selectedMode: 'talk' });
		const tab = activeTab();
		expect(visible(tab)).toBe(STALE);
		expect(sent(tab)).toEqual([STALE]);
	});

	it('switching to a never-visited mode sends nothing from the mode left', () => {
		const id = get(tabsStore).activeTabId;
		tabsStore.updateTab(id, {
			selectedPreset: 'p',
			selectedMode: 'txt2img',
			prompt: STALE,
			promptSegments: [{ id: 's', type: 'content', content: STALE, chips: {}, enabled: true }] as never
		});
		tabsStore.updateTab(id, { ...buildModeSwitchPatch(activeTab(), 'txt2img', 'talk'), selectedMode: 'talk' });
		const tab = activeTab();
		expect(visible(tab)).toBe('');
		expect(sent(tab)).toEqual(['']);
	});

	it('a batch entry stored only as a string is shown in its own slot', () => {
		const id = get(tabsStore).activeTabId;
		const bare = (prompt: string) => ({ prompt, negativePrompt: '', promptSegments: [], negativePromptSegments: [] });
		tabsStore.updateTab(id, { promptTabs: [bare('one'), bare(STALE)], activePromptTab: 0 });
		const tab = activeTab();
		expect(tab.promptTabs!.map((e) => resolvePromptSegments(e.promptSegments || []).trim())).toEqual(['one', STALE]);
		expect(sent(tab)).toEqual(['one', STALE]);
	});

	it('a restored plain-prompt session is not reported as edited', () => {
		const id = get(tabsStore).activeTabId;
		const saved = { txt2img: { prompt: STALE } };
		tabsStore.updateTab(id, {
			selectedSessionId: 's',
			selectedMode: 'txt2img',
			savedSessionSignature: JSON.stringify(saved),
			...buildSessionRestoreTabPatch(saved.txt2img)
		});
		expect(visible(activeTab())).toBe(STALE);
		expect(tabHasUnsavedSessionChanges(activeTab())).toBe(false);
	});
});
