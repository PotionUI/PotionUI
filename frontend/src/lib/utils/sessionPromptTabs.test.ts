import { describe, expect, it } from 'vitest';
import { collectTabSessionData } from './sessionTabState';
import { buildSessionRestoreTabPatch } from './sessionRestore';
import { buildModeSwitchPatch } from './modeState';
import { tabHasUnsavedSessionChanges } from './unsavedChangesGuard';
import { promptCount, promptList } from '$lib/generation/promptBatch';
import type { GenerationTab } from '$lib/types/tabs';

function segment(id: string, content: string) {
	return { id, content, type: 'content' as const };
}

function batchTab(): GenerationTab {
	const promptTabs = [
		{ name: 'Lighthouse', prompt: 'a lighthouse', negativePrompt: 'blur', promptSegments: [segment('a', 'a lighthouse')], negativePromptSegments: [segment('an', 'blur')] },
		{ prompt: 'a red fox', negativePrompt: '', promptSegments: [segment('b', 'a red fox'), segment('b2', 'golden hour')], negativePromptSegments: [] },
		{ name: 'Ramen', prompt: 'ramen stall', negativePrompt: 'text', promptSegments: [segment('c', 'ramen stall')], negativePromptSegments: [segment('cn', 'text')] }
	];
	return {
		id: 't1',
		selectedPreset: 'preset',
		selectedMode: 'txt2img',
		prompt: 'a red fox',
		negativePrompt: '',
		promptSegments: promptTabs[1].promptSegments,
		negativePromptSegments: [],
		promptTabs,
		activePromptTab: 1,
		formData: { quantity: 2 }
	} as unknown as GenerationTab;
}

describe('batch prompts in saved sessions', () => {
	it('round-trips every prompt, name, negative and the current one through a saved session', () => {
		const tab = batchTab();

		const saved = JSON.parse(JSON.stringify(collectTabSessionData(tab, 'txt2img', {}, 'v1')));
		const patch = buildSessionRestoreTabPatch(saved.txt2img);

		expect(patch.promptTabs).toEqual(tab.promptTabs);
		expect(patch.activePromptTab).toBe(1);
		expect(promptCount(patch as GenerationTab)).toBe(3);
		expect(patch.promptSegments).toEqual(tab.promptSegments);
		expect(patch.prompt).toBe('a red fox');
	});

	it('loads an old single-prompt session as one prompt', () => {
		const legacy = { prompt: 'a cat', negativePrompt: '', promptSegments: [segment('x', 'a cat')], negativePromptSegments: [], formData: {} };

		const patch = buildSessionRestoreTabPatch(legacy);

		expect(patch.promptTabs).toBeUndefined();
		expect(promptCount(patch as GenerationTab)).toBe(1);
		expect(promptList(patch as GenerationTab)[0].prompt).toBe('a cat');
	});

	it('keeps the batch when the mode is switched away and back', () => {
		const tab = batchTab();

		const away = buildModeSwitchPatch(tab, 'txt2img', 'img2img');
		const back = buildModeSwitchPatch({ ...tab, ...away } as GenerationTab, 'img2img', 'txt2img');

		expect(away.promptTabs).toBeUndefined();
		expect(back.promptTabs).toEqual(tab.promptTabs);
		expect(back.activePromptTab).toBe(1);
	});

	it('reports unsaved changes when a prompt other than the current one is edited', () => {
		const tab = batchTab();
		const savedSessionSignature = JSON.stringify(collectTabSessionData(tab, 'txt2img'));
		const base = { ...tab, selectedSessionId: 's1', savedSessionSignature } as GenerationTab;
		const edited = {
			...base,
			promptTabs: base.promptTabs!.map((entry, i) => (i === 2 ? { ...entry, name: 'Noodles' } : entry))
		} as GenerationTab;

		expect(tabHasUnsavedSessionChanges(base)).toBe(false);
		expect(tabHasUnsavedSessionChanges(edited)).toBe(true);
	});
});
