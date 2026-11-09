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
	default: (await import('./fixtures/EditorTitleAndMain.svelte')).default
}));

const { default: PromptSection } = await import('../../src/routes/generate/components/PromptSection.svelte');
const { default: RegionalPromptEditor } = await import('../../src/lib/components/regional/RegionalPromptEditor.svelte');
const { tabsStore } = await import('../../src/lib/stores/tabs');
const { mountComponent } = await import('./helpers/mount');

const handlers = {
	handlePromptChange: () => {},
	handlePromptSegmentsChange: () => {},
	handleNegativePromptChange: () => {},
	handleNegativePromptSegmentsChange: () => {}
};

let cleanup: (() => void) | undefined;

beforeEach(() => {
	tabsStore.reset();
	HTMLElement.prototype.setPointerCapture = vi.fn();
	HTMLElement.prototype.releasePointerCapture = vi.fn();
	HTMLElement.prototype.hasPointerCapture = vi.fn(() => false);
});

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

async function renderSection(opts: { caps: { sizeField: string } | null; formData?: Record<string, unknown> }) {
	const id = get(tabsStore).activeTabId;
	tabsStore.updateTab(id, { selectedPreset: 'preset', selectedMode: 'txt2img', formData: opts.formData ?? {} });
	const target = document.createElement('div');
	document.body.appendChild(target);
	const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
	const instance = mount(PromptSection, {
		target,
		props: { tab, tabHandlers: handlers, promptRelayActive: false, canBatch: false, regionalCaps: opts.caps }
	});
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	flushSync();
	await tick();
	return { target, id };
}

const modeSwitch = (target: HTMLElement) => target.querySelector('[role="group"][aria-label="Prompt mode"]');

describe('Regional switch visibility', () => {
	it('is hidden when the preset declares no option', async () => {
		const { target } = await renderSection({ caps: null });
		expect(modeSwitch(target)).toBeNull();
		expect(target.querySelector('[data-testid="regional-editor"]')).toBeNull();
	});

	it('shows Text and Regional when the current mode is listed', async () => {
		const { target } = await renderSection({ caps: { sizeField: 'resolution' } });
		const buttons = [...modeSwitch(target)!.querySelectorAll('button')].map((b) => b.textContent!.trim());
		expect(buttons).toEqual(['Text', 'Regional']);
		expect(target.querySelector('[data-testid="regional-editor"]')).toBeNull();
	});

	it('writes the form value, shows the editor and counts regions when switched on', async () => {
		const { target, id } = await renderSection({ caps: { sizeField: 'resolution' }, formData: { resolution: '1536x1024' } });
		const regional = [...modeSwitch(target)!.querySelectorAll('button')].find((b) => b.textContent!.includes('Regional'))!;
		regional.click();
		flushSync();
		await tick();
		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
		expect(tab.formData.regional_prompt).toEqual({ enabled: true, items: [] });
		expect(tab.formData.resolution).toBe('1536x1024');
	});

	it('never writes the key without the option', async () => {
		const { id } = await renderSection({ caps: null });
		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
		expect(tab.formData).not.toHaveProperty('regional_prompt');
	});

	it('shows the count and canvas size once regions exist', async () => {
		const items = [
			{ x: 0.1, y: 0.1, w: 0.3, h: 0.3, caption: 'a' },
			{ x: 0.5, y: 0.5, w: 0.3, h: 0.3, caption: 'b' }
		];
		const { target } = await renderSection({
			caps: { sizeField: 'resolution' },
			formData: { resolution: '768x1344', regional_prompt: { enabled: true, items } }
		});
		expect(modeSwitch(target)!.textContent).toContain('2');
		expect(target.querySelectorAll('[data-region]')).toHaveLength(2);
		expect(target.querySelector('[data-testid="regional-size"]')!.textContent).toBe('768×1344');
	});
});

describe('RegionalPromptEditor selection sync', () => {
	const items = [
		{ x: 0.1, y: 0.1, w: 0.3, h: 0.3, caption: 'first' },
		{ x: 0.5, y: 0.5, w: 0.3, h: 0.3, caption: 'second' }
	];

	function mountEditor(onChange = vi.fn()) {
		const view = mountComponent(RegionalPromptEditor as never, { items, size: { width: 1024, height: 1024 }, onChange });
		return { target: view.target, onChange };
	}

	const box = (t: HTMLElement, i: number) => t.querySelector<HTMLElement>(`[data-region="${i}"]`)!;
	const card = (t: HTMLElement, i: number) => t.querySelector<HTMLElement>(`[data-card="${i}"]`)!;

	it('selecting a card selects its box', async () => {
		const { target } = mountEditor();
		card(target, 1).querySelector('textarea')!.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
		flushSync();
		expect(box(target, 1).classList.contains('sel')).toBe(true);
		expect(box(target, 0).classList.contains('sel')).toBe(false);
		expect(card(target, 1).classList.contains('sel')).toBe(true);
	});

	it('selecting a box selects its card', async () => {
		const { target } = mountEditor();
		box(target, 0).dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		expect(card(target, 0).classList.contains('sel')).toBe(true);
		expect(card(target, 1).classList.contains('sel')).toBe(false);
	});

	it('Escape deselects and Delete removes the selected region', async () => {
		const { target, onChange } = mountEditor();
		const canvas = target.querySelector<HTMLElement>('[data-testid="regional-canvas"]')!;
		box(target, 1).dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
		expect(onChange).toHaveBeenCalledWith([items[0]]);
		canvas.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(target.querySelector('.rg-box.sel')).toBeNull();
	});

	it('shows the empty-state copy without regions', async () => {
		const view = mountComponent(RegionalPromptEditor as never, { items: [], size: { width: 1024, height: 1024 }, onChange: vi.fn() });
		expect(view.target.textContent).toContain('Drag to place a region');
		expect(view.target.textContent).toContain('The prompt above describes the whole image. Regions add what goes where.');
	});

	it('editing a description reports the new caption', async () => {
		const { target, onChange } = mountEditor();
		const area = card(target, 0).querySelector('textarea')!;
		area.value = 'changed';
		area.dispatchEvent(new Event('input', { bubbles: true }));
		expect(onChange).toHaveBeenCalledWith([{ ...items[0], caption: 'changed' }, items[1]]);
	});
});
