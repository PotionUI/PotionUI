import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { cropDraft } from '$lib/tool-tabs/image/steps';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import { createHostEditorsApi, markEditorExtensionsReady, resetEditorExtensionsReady, type PluginEditorsApi } from '$lib/editors';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';
import PluginToolOptions from './PluginToolOptions.svelte';
import ThrowingToolOptions from './ThrowingToolOptions.svelte';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const SOURCE: MediaRef = {
	path: 'outputs/a.png',
	relative_path: 'outputs/a.png',
	url: '/api/media/generations/gen-1/a.png',
	name: 'a.png',
	type: 'image',
	generation_file_id: '501'
};

const KIND = {
	id: 'acme.halftone',
	editor: 'image' as const,
	label: 'Halftone',
	icon: 'extension',
	summary: () => 'Factor 2',
	payload: () => [{ tool: 'acme_halftone', params: {} }]
};

function stateWithPluginStep(tool = 'acme.halftone') {
	let stack = apply(beginEdit(createStack(), cropDraft({ plan: EMPTY_IMAGE_PLAN, aspect: 'free' }, 'Free', { width: 480, height: 480 })));
	stack = apply(beginEdit(stack, { tool, icon: 'extension', label: 'Halftone', summary: 'Factor 2', params: { v: 1, factor: 2 }, lane: 'paint' }));
	return writeImageState({ ...emptyImageState('crop'), stack });
}

function render(state: unknown = null) {
	const host: ToolTabHost = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state,
		activeTool: null,
		run: null,
		hint: null,
		phone: false,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	} as ToolTabHost;
	const view = mountComponent(ImageEditorTab, { host });
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const all = (selector: string) => Array.from(view.target.querySelectorAll<HTMLElement>(selector));
	return { view, q, all };
}

async function settle() {
	for (let i = 0; i < 6; i++) await Promise.resolve();
	flushSync();
}

let editors: PluginEditorsApi;
const disposers: Array<() => void> = [];

beforeEach(() => {
	vi.useFakeTimers();
	editors = createHostEditorsApi();
	(window as unknown as { __potionui: { editors: PluginEditorsApi } }).__potionui = { editors };
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
});

afterEach(() => {
	disposers.splice(0).forEach((dispose) => dispose());
	delete (window as unknown as { __potionui?: unknown }).__potionui;
	vi.useRealTimers();
});

function registerPluginTool(options: unknown) {
	disposers.push(
		editors.registerRailGroup({ id: 'acme.stylize', editor: 'image', label: 'Stylize', order: 450 }, { pluginId: 'acme' }),
		editors.registerStepKind(KIND, { pluginId: 'acme' }),
		editors.registerRailTool({ id: 'acme.halftone', editor: 'image', group: 'acme.stylize', label: 'Halftone', icon: 'extension', options: { component: options as never } }, { pluginId: 'acme' })
	);
}

describe('Image editor with a plugin registered through window.__potionui.editors', () => {
	it('lists the plugin tool in the rail after the core groups, even when registered after mount', async () => {
		const { q, all } = render(stateWithPluginStep());
		expect(q('[data-tool-rail] button[data-tool="acme.halftone"]')).toBeNull();
		registerPluginTool(PluginToolOptions);
		await settle();
		expect(all('[data-tool-rail] button[data-tool]').map((button) => button.dataset.tool).slice(-1)).toEqual(['acme.halftone']);
		expect(all('[data-tool-rail] button[data-tool]').map((button) => button.dataset.tool).slice(0, 6)).toEqual(['edit_region', 'remove_object', 'replace_background', 'expand', 'crop', 'resize']);
	});

	it('renders the registered options through the first branch and lets the tool begin and apply a step', async () => {
		registerPluginTool(PluginToolOptions);
		const { q, all } = render(stateWithPluginStep());
		await settle();
		q<HTMLButtonElement>('[data-tool-rail] button[data-tool="acme.halftone"]')!.click();
		await settle();
		expect(q('[data-image-editor]')?.dataset.tool).toBe('acme.halftone');
		expect(q('[data-plugin-options]')).not.toBeNull();
		expect(q('[data-plugin-options]')?.getAttribute('data-phone')).toBe('false');
		const before = all('[data-stack-row]').length;
		q<HTMLButtonElement>('[data-plugin-begin]')!.click();
		await settle();
		expect(all('[data-stack-section="edits"] [data-stack-row]').at(0)?.dataset.status).toBe('pending');
		q<HTMLButtonElement>('[data-plugin-label]')!.click();
		await settle();
		expect(document.body.textContent).toContain('Make halftone');
		q<HTMLButtonElement>('[data-plugin-apply]')!.click();
		await settle();
		expect(all('[data-stack-row]')).toHaveLength(before + 1);
		expect(all('[data-stack-section="edits"] [data-stack-row]').at(0)?.textContent).toContain('Halftone');
	});

	it('shows a fallback with a Close button when the registered options throw, and keeps the editor working', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => undefined);
		registerPluginTool(ThrowingToolOptions);
		const { q, all } = render(stateWithPluginStep());
		await settle();
		q<HTMLButtonElement>('[data-tool-rail] button[data-tool="acme.halftone"]')!.click();
		await settle();
		expect(q('[data-editor-entry-failed]')?.textContent).toContain('This tool could not load');
		expect(q('[data-tool-rail]')).not.toBeNull();
		expect(all('[data-stack-row]').length).toBeGreaterThan(1);
		const close = Array.from(q('[data-editor-entry-failed]')!.querySelectorAll('button')).find((button) => button.textContent?.includes('Close'))!;
		close.click();
		await settle();
		expect(q('[data-editor-entry-failed]')).toBeNull();
		expect(q('[data-image-editor]')?.dataset.tool ?? '').not.toBe('acme.halftone');
	});

	it('draws a step of an unregistered kind as not available, keeps it, and makes it normal when the kind registers', async () => {
		const { q, all } = render(stateWithPluginStep());
		await settle();
		const rowsOf = () => all('[data-stack-row]').sort((a, b) => Number(a.dataset.stackIndex) - Number(b.dataset.stackIndex)).map((row) => row.dataset.stackRow);
		expect(rowsOf()).toEqual(['original', 's1', 's2']);
		const gone = q('[data-stack-row="s2"]')!;
		expect(gone.textContent).toContain('Halftone');
		expect(gone.textContent).toContain('Not available');
		expect(gone.querySelector('[data-stack-locked]')).not.toBeNull();
		expect(gone.querySelector('[data-stack-open]')).toBeNull();
		expect(gone.querySelector('[data-stack-remove]')).not.toBeNull();

		registerPluginTool(PluginToolOptions);
		await settle();
		const back = q('[data-stack-row="s2"]')!;
		expect(back.textContent).not.toContain('Not available');
		expect(back.querySelector('[data-stack-open]')).not.toBeNull();
		expect(rowsOf()).toEqual(['original', 's1', 's2']);
	});

	it('shows Loading, not Not available, until the first plugin snapshot has been applied', async () => {
		resetEditorExtensionsReady();
		const { q } = render(stateWithPluginStep());
		await settle();
		expect(q('[data-stack-row="s2"]')?.textContent).toContain('Loading');
		expect(q('[data-stack-row="s2"]')?.textContent).not.toContain('Not available');
		markEditorExtensionsReady();
		await settle();
		expect(q('[data-stack-row="s2"]')?.textContent).toContain('Not available');
	});

	it('settles a row of a missing kind from Loading to Not available once the media tool catalog has loaded', async () => {
		const { q } = render(stateWithPluginStep());
		await settle();
		mediaToolCatalogRevision.set(0);
		await settle();
		expect(q('[data-stack-row="s2"]')?.textContent).toContain('Loading');
		expect(q('[data-stack-row="s2"]')?.textContent).not.toContain('Not available');
		mediaToolCatalogRevision.set(1);
		await settle();
		expect(q('[data-stack-row="s2"]')?.textContent).toContain('Not available');
		expect(q('[data-stack-row="s2"]')?.textContent).not.toContain('Loading');
	});
});
