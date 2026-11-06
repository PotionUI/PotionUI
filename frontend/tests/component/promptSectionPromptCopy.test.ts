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
	default: (await import('./fixtures/EditorTitleAndPlaceholder.svelte')).default
}));

const { default: PromptSection } = await import('../../src/routes/generate/components/PromptSection.svelte');
const { tabsStore } = await import('../../src/lib/stores/tabs');

const handlers = {
	handlePromptChange: () => {},
	handlePromptSegmentsChange: () => {},
	handleNegativePromptChange: () => {},
	handleNegativePromptSegmentsChange: () => {}
};

let cleanup: (() => void) | undefined;

beforeEach(() => tabsStore.reset());

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

async function render(promptCopy?: { label: string | null; placeholder: string | null; optional: boolean }) {
	const id = get(tabsStore).activeTabId;
	tabsStore.updateTab(id, { selectedPreset: 'preset', selectedMode: 'text', formData: {} });
	const target = document.createElement('div');
	document.body.appendChild(target);
	const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
	const instance = mount(PromptSection, {
		target,
		props: { tab, tabHandlers: handlers, promptRelayActive: false, canBatch: false, ...(promptCopy ? { promptCopy } : {}) }
	});
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	flushSync();
	await tick();
	return target;
}

describe('PromptSection prompt copy', () => {
	it('titles the prompt with the mode label, marks it optional and uses the mode placeholder', async () => {
		const target = await render({
			label: 'Placement and style',
			placeholder: 'in white capitals at the top',
			optional: true
		});

		expect(target.querySelector('[data-editor-title] [role="tab"]')!.textContent!.trim()).toBe(
			'Placement and style (optional)'
		);
		expect(target.querySelector('[data-editor-placeholder]')!.textContent).toBe('in white capitals at the top');
	});

	it('shows the label alone for a required prompt', async () => {
		const target = await render({ label: 'Scene', placeholder: null, optional: false });

		expect(target.querySelector('[data-editor-title] [role="tab"]')!.textContent!.trim()).toBe('Scene');
	});

	it('keeps Prompt and the stock placeholder when the mode declares nothing', async () => {
		const target = await render();

		expect(target.querySelector('[data-editor-title] [role="tab"]')!.textContent!.trim()).toBe('Prompt');
		expect(target.querySelector('[data-editor-placeholder]')!.textContent).toContain('Enter prompt content');
	});
});
