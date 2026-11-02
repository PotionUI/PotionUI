import { generationTabs } from '$lib/types/tabs';
import { beforeEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import type { Segment } from '$lib/types/segments';
import type { PromptTabData, GenerationTab } from '$lib/types/tabs';
import {
	LOCKED_BATCH_COPY,
	MAX_BATCH_IMAGES,
	activePromptIndex,
	addPrompt,
	addPromptState,
	batchLine,
	batchSummary,
	canBatchPrompts,
	deletePrompt,
	duplicatePrompt,
	firstEmptyPrompt,
	imageWord,
	mirrorActivePrompt,
	promptCount,
	promptList,
	promptTitle,
	quantityOf,
	renamePrompt,
	selectPrompt
} from './promptBatch';

type PromptFieldsTab = Pick<
	GenerationTab,
	'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments' | 'promptTabs' | 'activePromptTab'
>;

function segment(id: string, content: string): Segment {
	return { id, content, type: 'content' } as Segment;
}

function singleTab(text = 'a fox'): PromptFieldsTab {
	return {
		prompt: text,
		negativePrompt: 'blur',
		promptSegments: [segment('s1', text)],
		negativePromptSegments: [segment('n1', 'blur')],
		promptTabs: undefined,
		activePromptTab: undefined
	};
}

function apply(tab: PromptFieldsTab, patch: Partial<PromptFieldsTab> | null): PromptFieldsTab {
	return { ...tab, ...(patch ?? {}) };
}

function entries(tab: PromptFieldsTab): PromptTabData[] {
	return tab.promptTabs ?? [];
}

describe('prompt list operations', () => {
	it('treats a tab without promptTabs as one prompt', () => {
		const tab = singleTab();

		expect(promptCount(tab)).toBe(1);
		expect(activePromptIndex(tab)).toBe(0);
		expect(promptList(tab)).toEqual([
			{ prompt: 'a fox', negativePrompt: 'blur', promptSegments: tab.promptSegments, negativePromptSegments: tab.negativePromptSegments }
		]);
	});

	it('adds an empty prompt, selects it and keeps the first one intact', () => {
		const next = apply(singleTab(), addPrompt(singleTab()));

		expect(promptCount(next)).toBe(2);
		expect(next.activePromptTab).toBe(1);
		expect(next.prompt).toBe('');
		expect(next.promptSegments).toHaveLength(1);
		expect(next.promptSegments?.[0]?.content).toBe('');
		expect(next.negativePromptSegments).toHaveLength(1);
		expect(entries(next)[0].prompt).toBe('a fox');
		expect(entries(next)[0].negativePromptSegments).toHaveLength(1);
	});

	it('mirrors the selected prompt onto the working fields', () => {
		let tab = apply(singleTab(), addPrompt(singleTab()));
		tab = apply(tab, selectPrompt(tab, 0));

		expect(tab.activePromptTab).toBe(0);
		expect(tab.prompt).toBe('a fox');
		expect(tab.negativePrompt).toBe('blur');
		expect(tab.promptSegments).toHaveLength(1);
	});

	it('duplicates every segment into the next slot and selects the copy', () => {
		const tab = apply(singleTab(), duplicatePrompt(singleTab(), 0));

		expect(promptCount(tab)).toBe(2);
		expect(tab.activePromptTab).toBe(1);
		expect({ ...entries(tab)[1], id: undefined }).toEqual({ ...entries(tab)[0], id: undefined });
		expect(entries(tab)[1].id).not.toBe(entries(tab)[0].id);
		expect(entries(tab)[1].promptSegments).not.toBe(entries(tab)[0].promptSegments);
	});

	it('refuses to delete the last prompt', () => {
		expect(deletePrompt(singleTab(), 0)).toBeNull();
	});

	it('collapses back to the plain single-prompt fields when one prompt is left', () => {
		let tab = apply(singleTab(), addPrompt(singleTab()));
		tab = apply(tab, deletePrompt(tab, 1));

		expect(tab.promptTabs).toBeUndefined();
		expect(tab.activePromptTab).toBeUndefined();
		expect(tab.prompt).toBe('a fox');
	});

	it('keeps the same prompt selected when an earlier one is deleted', () => {
		let tab = apply(singleTab('one'), addPrompt(singleTab('one')));
		tab = apply(tab, addPrompt(tab));
		tab = { ...tab, ...selectPrompt(tab, 2) };
		const before = promptList(tab)[2];

		tab = apply(tab, deletePrompt(tab, 0));

		expect(promptCount(tab)).toBe(2);
		expect(tab.activePromptTab).toBe(1);
		expect(promptList(tab)[1]).toBe(before);
	});

	it('renames a prompt and clears the name when blank', () => {
		let tab = apply(singleTab(), addPrompt(singleTab()));
		tab = apply(tab, renamePrompt(tab, 0, '  Red fox '));
		expect(promptTitle(entries(tab)[0], 0)).toBe('Red fox');

		tab = apply(tab, renamePrompt(tab, 0, '   '));
		expect(entries(tab)[0].name).toBeUndefined();
		expect(promptTitle(entries(tab)[0], 0)).toBe('a fox');
	});

	it('falls back to a numbered title for an empty prompt', () => {
		expect(promptTitle({ prompt: '', negativePrompt: '', promptSegments: [], negativePromptSegments: [] }, 2)).toBe('Prompt 3');
	});

	it('finds the first empty prompt', () => {
		const tab = apply(singleTab(), addPrompt(singleTab()));

		expect(firstEmptyPrompt(promptList(tab))).toBe(1);
		expect(firstEmptyPrompt(promptList(singleTab()))).toBe(-1);
	});
});

describe('batch cap', () => {
	it('exposes one cap constant', () => {
		expect(MAX_BATCH_IMAGES).toBe(20);
	});

	it('multiplies prompts by images per prompt', () => {
		expect(batchSummary({ prompts: 6, perPrompt: 2, canBatch: true })).toMatchObject({
			sent: 6,
			total: 12,
			overCap: false,
			unsupported: false,
			maxPrompts: 10
		});
	});

	it('is over the cap only above 20 images', () => {
		expect(batchSummary({ prompts: 10, perPrompt: 2, canBatch: true }).overCap).toBe(false);
		expect(batchSummary({ prompts: 11, perPrompt: 2, canBatch: true }).overCap).toBe(true);
	});

	it('never flags a single prompt, whatever the quantity', () => {
		expect(batchSummary({ prompts: 1, perPrompt: 30, canBatch: true }).overCap).toBe(false);
	});

	it('counts only the first prompt when the preset cannot batch', () => {
		const summary = batchSummary({ prompts: 6, perPrompt: 2, canBatch: false });

		expect(summary).toMatchObject({ sent: 1, total: 2, unsupported: true, overCap: false });
		expect(batchLine(summary)!.text).toBe('1 of 6 prompts × 2 images = 2 images');
	});

	it('words the count line and the generate label', () => {
		const line = batchLine(batchSummary({ prompts: 6, perPrompt: 2, canBatch: true }))!;

		expect(line).toEqual({ text: '6 prompts × 2 images = 12 images', generateLabel: 'Generate 12 images', over: false });
		expect(batchLine(batchSummary({ prompts: 2, perPrompt: 1, canBatch: true }))!.text).toBe('2 prompts × 1 image = 2 images');
		expect(batchLine(batchSummary({ prompts: 1, perPrompt: 2, canBatch: true }))).toBeNull();
	});

	it('reads the capability only from an explicit true', () => {
		expect(canBatchPrompts({ supports_prompt_batch: true })).toBe(true);
		expect(canBatchPrompts({ supports_prompt_batch: false })).toBe(false);
		expect(canBatchPrompts({})).toBe(false);
		expect(canBatchPrompts(undefined)).toBe(false);
	});

	it('reads quantity as a positive whole number', () => {
		expect(quantityOf({ quantity: 3 })).toBe(3);
		expect(quantityOf({ quantity: '4' })).toBe(4);
		expect(quantityOf({ quantity: 0 })).toBe(1);
		expect(quantityOf({})).toBe(1);
		expect(quantityOf(undefined)).toBe(1);
	});
});

describe('adding a prompt', () => {
	const room = { canBatch: true, count: 2, maxPrompts: 10, perPrompt: 2 };

	it('is open, with nothing to explain, while there is room', () => {
		expect(addPromptState(room)).toEqual({ atLimit: false, disabled: false, explanation: null });
	});

	it('is locked with the one-prompt copy for a preset that cannot batch, whatever the count', () => {
		expect(addPromptState({ ...room, canBatch: false })).toEqual({
			atLimit: false,
			disabled: true,
			explanation: LOCKED_BATCH_COPY
		});
		expect(addPromptState({ ...room, canBatch: false, count: 50 }).explanation).toBe(LOCKED_BATCH_COPY);
	});

	it('closes at the cap and quotes the image cap and the prompt cap', () => {
		const full = addPromptState({ ...room, count: 10 });
		expect(full.atLimit).toBe(true);
		expect(full.disabled).toBe(true);
		expect(full.explanation).toBe(
			`A run can make up to ${MAX_BATCH_IMAGES} images. At 2 images each, that is 10 prompts.`
		);
	});

	it('stays open one prompt short of the cap and closes past it', () => {
		expect(addPromptState({ ...room, count: 9 }).disabled).toBe(false);
		expect(addPromptState({ ...room, count: 11 }).disabled).toBe(true);
	});

	it('says image in the singular for one image per prompt', () => {
		expect(imageWord(1)).toBe('image');
		expect(imageWord(2)).toBe('images');
		expect(addPromptState({ ...room, count: 20, maxPrompts: 20, perPrompt: 1 }).explanation).toContain('At 1 image each');
	});
});

describe('active prompt mirroring', () => {
	it('writes edits of the working fields into the selected prompt', () => {
		const tab = apply(singleTab(), addPrompt(singleTab()));
		const edited = { ...tab, promptSegments: [segment('s9', 'two foxes')], prompt: 'two foxes' };

		const mirrored = mirrorActivePrompt(edited, { promptSegments: edited.promptSegments, prompt: 'two foxes' });

		expect(mirrored!.promptTabs![1].prompt).toBe('two foxes');
		expect(mirrored!.promptTabs![0].prompt).toBe('a fox');
	});

	it('leaves structural updates alone', () => {
		const tab = apply(singleTab(), addPrompt(singleTab()));

		expect(mirrorActivePrompt(tab, { promptTabs: tab.promptTabs, prompt: 'x' })).toBeNull();
		expect(mirrorActivePrompt(tab, { formData: {} } as never)).toBeNull();
		expect(mirrorActivePrompt(singleTab(), { prompt: 'x' })).toBeNull();
	});

	describe('through the tabs store', () => {
		beforeEach(() => tabsStore.reset());

		it('keeps the selected prompt in step with every writer of the working fields', () => {
			const id = get(tabsStore).activeTabId;
			const first = generationTabs(get(tabsStore).tabs)[0];
			tabsStore.updateTab(id, { ...singleTab(), ...addPrompt({ ...first, ...singleTab() }) });

			tabsStore.updateTab(id, { prompt: 'typed later', promptSegments: [segment('t', 'typed later')] });

			const tab = generationTabs(get(tabsStore).tabs)[0];
			expect(entries(tab)[1].prompt).toBe('typed later');
			expect(entries(tab)[0].prompt).toBe('a fox');
		});
	});
});
