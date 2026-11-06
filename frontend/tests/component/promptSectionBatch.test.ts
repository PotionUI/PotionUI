// @vitest-environment jsdom
import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { get } from 'svelte/store';

vi.mock('../../src/lib/utils/presetPromptResourcesCache', () => ({
	getPresetPromptResources: () => Promise.resolve({ specs: [], fieldLabels: {} })
}));

vi.mock('../../src/lib/utils/presetPromptSyntaxCache', () => ({
	getPresetPromptSyntax: () => Promise.resolve([])
}));

vi.mock('../../src/lib/components/SegmentedPromptEditor.svelte', async () => ({
	default: (await import('./fixtures/EditorTitleSlot.svelte')).default
}));

const { default: PromptSection } = await import('../../src/routes/generate/components/PromptSection.svelte');
const { tabsStore } = await import('../../src/lib/stores/tabs');

const handlers = {
	handlePromptChange: () => {},
	handlePromptSegmentsChange: () => {},
	handleNegativePromptChange: () => {},
	handleNegativePromptSegmentsChange: () => {}
};

function entry(prompt: string) {
	return { prompt, negativePrompt: '', promptSegments: [], negativePromptSegments: [] };
}

let cleanup: (() => void) | undefined;

beforeEach(() => tabsStore.reset());

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

async function render(count: number, formData: Record<string, unknown>, canBatch: boolean) {
	const id = get(tabsStore).activeTabId;
	const promptTabs = Array.from({ length: count }, (_, i) => entry(`prompt ${i + 1}`));
	tabsStore.updateTab(id, {
		selectedPreset: 'preset',
		selectedMode: 'txt2img',
		formData,
		...(count > 1 ? { promptTabs, activePromptTab: count - 1, prompt: promptTabs[count - 1].prompt } : {})
	});
	const target = document.createElement('div');
	document.body.appendChild(target);
	const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
	const instance = mount(PromptSection, {
		target,
		props: { tab, tabHandlers: handlers, promptRelayActive: false, canBatch }
	});
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	flushSync();
	await tick();
	return { target, id };
}

describe('PromptSection with several prompts', () => {
	it('shows one tab per prompt inside the editor title, with the active one selected', async () => {
		const { target } = await render(3, { quantity: 1 }, true);

		const tabs = [...target.querySelectorAll<HTMLElement>('[data-editor-title] [role="tab"]')];
		expect(tabs.map((tab) => tab.textContent!.trim())).toEqual(['Prompt 1', 'Prompt 2', 'Prompt 3']);
		expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true']);
		expect(target.querySelector('[data-editor-title] [data-prompt-picker]')!.getAttribute('aria-label')).toBe('All prompts');
	});

	it('selects another prompt from its tab', async () => {
		const { target, id } = await render(3, { quantity: 1 }, true);

		target.querySelectorAll<HTMLElement>('[data-editor-title] [role="tab"]')[0].click();
		flushSync();

		expect(generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!.activePromptTab).toBe(0);
	});

	it('adds a prompt from the plus button', async () => {
		const { target, id } = await render(3, { quantity: 1 }, true);

		target.querySelector<HTMLButtonElement>('[data-editor-title] button[aria-label="Add prompt"]')!.click();
		flushSync();

		expect(generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!.promptTabs).toHaveLength(4);
	});

	it('shows a single Prompt tab and no list for one prompt', async () => {
		const { target } = await render(1, { quantity: 1 }, true);

		const tabs = [...target.querySelectorAll<HTMLElement>('[data-editor-title] [role="tab"]')];
		expect(tabs.map((tab) => tab.textContent!.trim())).toEqual(['Prompt']);
		expect(target.querySelector('[data-editor-title] [data-prompt-picker]')).toBeNull();
	});

	it('shows the over-limit alert and removes the last prompt from it', async () => {
		const { target, id } = await render(11, { quantity: 2 }, true);

		expect(target.textContent).toContain('Up to 20 images per run.');
		const remove = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Remove prompt 11')!;
		remove.click();
		flushSync();

		expect(generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!.promptTabs).toHaveLength(10);
	});

	it('drops quantity to one from the over-limit alert', async () => {
		const { target, id } = await render(11, { quantity: 2 }, true);

		[...target.querySelectorAll('button')].find((b) => b.textContent?.includes('Make 1 image per prompt'))!.click();
		flushSync();

		expect((generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!.formData as { quantity: number }).quantity).toBe(1);
	});

	it('warns that only prompt 1 runs when the preset cannot batch, without blocking', async () => {
		const { target } = await render(6, { quantity: 2 }, false);

		expect(target.textContent).toContain('only prompt 1 will be generated');
		expect(target.textContent).toContain('Your other 5 prompts are kept');
		expect(target.textContent).not.toContain('Up to 20 images per run.');
	});

	it('shows no alert for a single prompt', async () => {
		const { target } = await render(1, { quantity: 4 }, true);

		expect(target.querySelector('[role="alert"], [role="status"]')).toBeNull();
	});
});
