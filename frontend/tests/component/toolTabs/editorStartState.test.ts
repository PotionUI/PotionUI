import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import type { ToolTabActions, ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');
const { default: VideoEditorTab } = await import('$lib/components/tool-tabs/video/VideoEditorTab.svelte');

const PICTURE: MediaRef = { path: 'uploads/dusk.png', relative_path: 'uploads/dusk.png', url: '/uploads/dusk.png', name: 'dusk.png', type: 'image', item_id: 'lib-1' };
const CLIP: MediaRef = { path: 'uploads/dock.mp4', relative_path: 'uploads/dock.mp4', url: '/uploads/dock.mp4', name: 'dock.mp4', type: 'video', item_id: 'v-1' };

const CHROME = [
	'[data-editor-frame]',
	'[data-frame-divider]',
	'[data-tool-rail]',
	'[data-frame-panel]',
	'[data-editor-toolbar]',
	'[data-editor-menubar]',
	'[data-frame-undo]',
	'[data-frame-redo]',
	'[data-frame-save]',
	'[data-frame-file]',
	'[data-frame-size]',
	'[data-frame-edits]',
	'[data-options-bar]'
];

const published: Array<ToolTabActions | null> = [];

function host(tool: 'image' | 'video', source: MediaRef | null, phone: boolean): ToolTabHost {
	published.length = 0;
	return {
		tabId: 'tab-s',
		tool,
		source,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: (actions) => published.push(actions),
		openToolTab: () => 'tab-2'
	};
}

function mount(tool: 'image' | 'video', source: MediaRef | null, phone: boolean) {
	const view = mountComponent(tool === 'image' ? ImageEditorTab : VideoEditorTab, { host: host(tool, source, phone) });
	flushSync();
	return view.target;
}

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe.each([
	['image', PICTURE, 'Open a picture to edit'],
	['video', CLIP, 'Open a clip to edit']
] as const)('%s editor start state', (tool, source, copy) => {
	for (const phone of [false, true]) {
		const label = phone ? 'phone' : 'desktop';

		it(`${label}: renders the start screen and none of the frame`, () => {
			const target = mount(tool, null, phone);
			expect(target.querySelector('[data-testid="tool-tab-empty"]')?.textContent).toContain(copy);
			for (const selector of CHROME) expect(target.querySelector(selector), selector).toBeNull();
			if (phone) expect(published[published.length - 1]).toEqual({ hidden: true });
		});

		it(`${label}: opening ${tool === 'image' ? 'a picture' : 'a clip'} brings the full frame`, () => {
			const target = mount(tool, source, phone);
			expect(target.querySelector('[data-testid="tool-tab-empty"]')).toBeNull();
			expect(target.querySelector(`[data-editor-frame="${phone ? 'phone' : 'desktop'}"]`)).not.toBeNull();
			expect(target.querySelector('[data-tool-rail]')).not.toBeNull();
			if (phone) expect(published[published.length - 1]?.hidden).toBeUndefined();
			if (!phone) {
				expect(target.querySelector('[data-frame-divider]')).not.toBeNull();
				expect(target.querySelector('[data-editor-menubar]')).not.toBeNull();
			}
		});
	}
});
