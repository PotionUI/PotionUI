import { describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { filterCatalog } from '$lib/filters/catalog';
import type { FilterItem } from '$lib/filters/types';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

vi.mock('$lib/media/editors/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 640, naturalHeight: 480 })
}));

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/dusk.png',
	url: '/api/media/generations/gen-1/dusk.png',
	name: 'dusk.png',
	type: 'image',
	generation_file_id: '501'
};

const REEL: FilterItem = {
	id: 'reel',
	name: 'Reel',
	group: 'Film',
	order: 30,
	intensity: 80,
	source: 'builtin',
	owned: false,
	has_lut: false,
	steps: [{ op: 'grain', amount: 24 }],
	unavailable_ops: [],
	needs_plugin: null,
	revision: '1'
};

async function settle() {
	for (let i = 0; i < 12; i++) await Promise.resolve();
	flushSync();
}

function render(hint: Record<string, unknown>, activeTool: string | null = null) {
	const cleared: number[] = [];
	const tools: Array<string | null> = [];
	const host = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state: null,
		activeTool,
		run: null,
		phone: false,
		hint,
		clearHint: () => cleared.push(1),
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		setActiveTool: (tool: string | null) => tools.push(tool),
		setRun: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	} as unknown as ToolTabHost;
	const view = mountComponent(ImageEditorTab, { host });
	const q = (selector: string) => view.target.querySelector<HTMLElement>(selector);
	return { view, q, cleared, tools };
}

describe('ImageEditorTab hints', () => {
	it('opens Crop with the rotation applied as a pending step and clears the hint once', async () => {
		const { q, cleared } = render({ orientation: 'rotate' }, 'crop');
		await settle();
		expect(cleared).toEqual([1]);
		expect(q('[data-image-editor]')?.dataset.tool).toBe('crop');
		expect(q('[data-stack-row="pending"]')?.textContent).toContain('Crop');
		expect(q('[data-tool-rail] button[data-tool="crop"] [data-tool-pending]')).not.toBeNull();
	});

	it('previews the hinted look in Effects', async () => {
		filterCatalog.set({ status: 'ready', items: [REEL], ops: [], groups: ['Film'], error: null });
		const { q, cleared } = render({ look: 'reel' }, 'effects');
		await settle();
		expect(cleared).toEqual([1]);
		expect(q('[data-image-editor]')?.dataset.tool).toBe('effects');
		const pending = q('[data-stack-row="pending"]')?.textContent?.replace(/\s+/g, ' ');
		expect(pending).toContain('Reel');
		expect(pending).toContain('Intensity 80');
	});

	it('falls back to Crop for a tool id the rail does not have', async () => {
		const { q } = render({}, 'rotate');
		await settle();
		expect(q('[data-image-editor]')?.dataset.tool).toBe('crop');
	});
});
