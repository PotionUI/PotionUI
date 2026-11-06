import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const confirmDialog = vi.hoisted(() => vi.fn());
vi.mock('$lib/stores/confirm', async (importActual) => ({ ...(await importActual<object>()), confirmDialog }));

const { default: VideoEditorTab } = await import('$lib/components/tool-tabs/video/VideoEditorTab.svelte');

const CLIP: MediaRef = { path: 'uploads/dock.mp4', relative_path: 'uploads/dock.mp4', url: '/uploads/dock.mp4', name: 'dock.mp4', type: 'video', item_id: 'v-1' };

function renderEditor(source: MediaRef | null) {
	const replace = vi.fn();
	const host: ToolTabHost = {
		tabId: 'tab-v',
		tool: 'video',
		source,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone: false,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: replace,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	};
	const view = mountComponent(VideoEditorTab, { host });
	return { view, q: <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector) };
}

beforeEach(() => confirmDialog.mockReset());

afterEach(() => {
	document.body.innerHTML = '';
});

describe('video editor Replace control', () => {
	it('is offered while a clip is open and lists Library and History', () => {
		const { q } = renderEditor(CLIP);
		q<HTMLButtonElement>('[data-frame-replace]')!.click();
		flushSync();
		const entries = Array.from(document.querySelectorAll<HTMLElement>('[data-replace-entry]')).map((row) => row.dataset.replaceEntry);
		expect(entries).toEqual(['library', 'history']);
	});

	it('is absent from the empty state', () => {
		const { q } = renderEditor(null);
		expect(q('[data-frame-replace]')).toBeNull();
		expect(q('[data-testid="tool-tab-empty"]')?.textContent).toContain('Library');
	});
});
