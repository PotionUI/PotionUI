import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { cropDraft, lookDraft } from '$lib/tool-tabs/image/steps';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const confirmDialog = vi.hoisted(() => vi.fn());
const upload = vi.hoisted(() => vi.fn());
const history = vi.hoisted(() => vi.fn());

vi.mock('$lib/stores/confirm', async (importActual) => ({ ...(await importActual<object>()), confirmDialog }));
vi.mock('$lib/services/api/mediaTools', async (importActual) => ({ ...(await importActual<object>()), uploadMediaToolArtifact: upload }));
vi.mock('$lib/services/api/index', async (importActual) => {
	const actual = await importActual<{ api: Record<string, unknown> }>();
	return { ...actual, api: { ...actual.api, getGenerationHistory: history } };
});

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const { isToolTab } = await import('$lib/types/tabs');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { default: ToolTabHost } = await import('$lib/tool-tabs/ToolTabHost.svelte');
const { default: MountCountFixture } = await import('./MountCountFixture.svelte');
const { replaceSource } = await import('$lib/tool-tabs/sourceEntries');

const CURRENT: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

const OTHER: MediaRef = {
	path: 'outputs/2026-11-02/gen-2/harbor.png',
	relative_path: 'outputs/2026-11-02/gen-2/harbor.png',
	url: '/api/media/generations/gen-2/harbor.png',
	name: 'harbor.png',
	type: 'image',
	generation_file_id: '777'
};

function edited() {
	let stack = apply(
		beginEdit(
			createStack(),
			cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.125, y: 0, width: 0.75, height: 1 } }, aspect: '1:1' }, '1:1', { width: 480, height: 480 })
		)
	);
	stack = apply(beginEdit(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
	return writeImageState({ ...emptyImageState('effects'), stack });
}

function renderEditor(overrides: Partial<ToolTabHost> = {}) {
	const replace = vi.fn();
	const host: ToolTabHost = {
		tabId: 'tab-1',
		tool: 'image',
		source: CURRENT,
		state: null,
		activeTool: 'crop',
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
		openToolTab: () => 'tab-2',
		...overrides
	};
	const view = mountComponent(ImageEditorTab, { host });
	return { view, replace, q: <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector) };
}

async function settle() {
	for (let i = 0; i < 12; i++) await Promise.resolve();
	flushSync();
}

function pasteImage(target: EventTarget = document.body) {
	const file = new File(['x'], 'clip.png', { type: 'image/png' });
	const event = new Event('paste', { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
	event.clipboardData = { items: [{ kind: 'file', type: 'image/png', getAsFile: () => file }] };
	target.dispatchEvent(event);
	return event;
}

beforeEach(() => {
	confirmDialog.mockReset();
	upload.mockReset();
	upload.mockResolvedValue({ id: 'art-1', url: '/api/media-tools/artifacts/art-1.png', width: 10, height: 10 });
	history.mockReset();
	history.mockResolvedValue({ success: false });
});

afterEach(() => {
	tabsStore.reset();
	document.body.innerHTML = '';
	delete (globalThis as { __mounts?: string[] }).__mounts;
});

describe('replaceSource', () => {
	it('swaps immediately when there are no edits', async () => {
		const host = { replaceSource: vi.fn() };
		expect(await replaceSource(host, CURRENT, OTHER, 0, 'picture')).toBe(true);
		expect(confirmDialog).not.toHaveBeenCalled();
		expect(host.replaceSource).toHaveBeenCalledWith(OTHER);
	});

	it('asks first when there are unsaved edits and keeps everything on cancel', async () => {
		confirmDialog.mockResolvedValue(false);
		const host = { replaceSource: vi.fn() };
		expect(await replaceSource(host, CURRENT, OTHER, 2, 'picture')).toBe(false);
		expect(confirmDialog).toHaveBeenCalledWith(
			expect.objectContaining({ title: 'Discard 2 unsaved edits?', confirmLabel: 'Replace', cancelLabel: 'Keep editing' })
		);
		expect(host.replaceSource).not.toHaveBeenCalled();
	});

	it('swaps after the edits are confirmed away', async () => {
		confirmDialog.mockResolvedValue(true);
		const host = { replaceSource: vi.fn() };
		expect(await replaceSource(host, CURRENT, OTHER, 1, 'clip')).toBe(true);
		expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ title: 'Discard 1 unsaved edit?' }));
		expect(host.replaceSource).toHaveBeenCalledWith(OTHER);
	});

	it('does nothing for the item that is already open', async () => {
		const host = { replaceSource: vi.fn() };
		expect(await replaceSource(host, CURRENT, { ...CURRENT }, 3, 'picture')).toBe(false);
		expect(confirmDialog).not.toHaveBeenCalled();
		expect(host.replaceSource).not.toHaveBeenCalled();
	});
});

describe('replacing a tool tab source', () => {
	it('clears the edits and remounts the tool for the new item while the tool choice stays', async () => {
		const off = registerToolKind({ tool: 'acme.mounts', label: 'Mounts', icon: 'brush', accepts: [], pluginId: 'acme', component: MountCountFixture as never });
		try {
			const id = tabsStore.openToolTab('acme.mounts', CURRENT, { activeTool: 'effects' });
			tabsStore.updateToolTab(id, { state: { v: 1 }, dirty: 4, thumbnail: '/t.png' });
			const tab = () => get(tabsStore).tabs.find((entry) => entry.id === id && isToolTab(entry))! as import('$lib/types/tabs').ToolTab;
			const view = mountComponent(ToolTabHost, { tab: tab() });
			await new Promise((resolve) => setTimeout(resolve, 0));
			await settle();
			expect((globalThis as { __mounts?: string[] }).__mounts).toEqual(['observatory-dusk.png']);

			tabsStore.replaceToolTabSource(id, OTHER);
			view.rerender({ tab: tab() });
			await settle();

			expect(tab().source).toEqual(OTHER);
			expect(tab().state).toBeNull();
			expect(tab().dirty).toBe(0);
			expect(tab().thumbnail).toBeNull();
			expect(tab().activeTool).toBe('effects');
			expect(tab().name).toBe('harbor');
			expect((globalThis as { __mounts?: string[] }).__mounts).toEqual(['observatory-dusk.png', 'harbor.png']);
			view.destroy();
		} finally {
			off();
		}
	});

	it('keeps a name the user typed', () => {
		const id = tabsStore.openToolTab('image', CURRENT);
		tabsStore.renameTab(id, 'My edit');
		tabsStore.replaceToolTabSource(id, OTHER);
		const tab = get(tabsStore).tabs.find((entry) => entry.id === id)!;
		expect(tab.name).toBe('My edit');
	});
});

describe('image editor Replace control', () => {
	it('is offered while a picture is open and lists the same sources as the empty state', () => {
		const { q } = renderEditor();
		q<HTMLButtonElement>('[data-frame-replace]')!.click();
		flushSync();
		const entries = Array.from(document.querySelectorAll<HTMLElement>('[data-replace-entry]')).map((row) => row.dataset.replaceEntry);
		expect(entries).toEqual(['history', 'library', 'kits', 'browse']);
	});

	it('is absent from the empty state', () => {
		const { q } = renderEditor({ source: null });
		expect(q('[data-frame-replace]')).toBeNull();
	});

	it('replaces from a recent picture without asking when nothing is edited', async () => {
		history.mockResolvedValue({
			success: true,
			data: { generations: [{ files: [{ id: 777, file_path: OTHER.path, file_type: 'image', is_final: true }] }] }
		});
		const { q, replace } = renderEditor();
		q<HTMLButtonElement>('[data-frame-replace]')!.click();
		await settle();
		document.querySelector<HTMLButtonElement>('[data-recent-history] button')!.click();
		await settle();
		expect(document.querySelector('[role="menu"]')).toBeNull();
		expect(confirmDialog).not.toHaveBeenCalled();
		expect(replace).toHaveBeenCalledTimes(1);
		expect(replace.mock.calls[0][0]).toMatchObject({ generation_file_id: '777' });
	});

	it('does nothing when the recent picture is the one already open', async () => {
		history.mockResolvedValue({
			success: true,
			data: { generations: [{ files: [{ id: 501, file_path: CURRENT.path, file_type: 'image', is_final: true }] }] }
		});
		const { q, replace } = renderEditor();
		q<HTMLButtonElement>('[data-frame-replace]')!.click();
		await settle();
		document.querySelector<HTMLButtonElement>('[data-recent-history] button')!.click();
		await settle();
		expect(replace).not.toHaveBeenCalled();
	});

	it('keeps the picture and its edits when the discard prompt is cancelled', async () => {
		confirmDialog.mockResolvedValue(false);
		const { replace } = renderEditor({ state: edited(), activeTool: 'effects' });
		pasteImage();
		await settle();
		expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ title: 'Discard 2 unsaved edits?' }));
		expect(replace).not.toHaveBeenCalled();
	});

	it('swaps after the discard prompt is confirmed', async () => {
		confirmDialog.mockResolvedValue(true);
		const { replace } = renderEditor({ state: edited(), activeTool: 'effects' });
		pasteImage();
		await settle();
		expect(replace).toHaveBeenCalledTimes(1);
		expect(replace.mock.calls[0][0]).toMatchObject({ artifact_id: 'art-1', name: 'clip.png' });
	});
});

describe('image editor paste while a picture is open', () => {
	it('replaces the picture', async () => {
		const { replace } = renderEditor();
		const event = pasteImage();
		await settle();
		expect(event.defaultPrevented).toBe(true);
		expect(upload).toHaveBeenCalledTimes(1);
		expect(replace).toHaveBeenCalledTimes(1);
	});

	it('leaves paste alone inside a text field', async () => {
		const { replace } = renderEditor();
		const field = document.body.appendChild(document.createElement('textarea'));
		const event = pasteImage(field);
		await settle();
		expect(event.defaultPrevented).toBe(false);
		expect(upload).not.toHaveBeenCalled();
		expect(replace).not.toHaveBeenCalled();
	});

	it('keeps the old picture and tells the user when the upload fails', async () => {
		upload.mockRejectedValue(new Error('boom'));
		const { replace } = renderEditor();
		pasteImage();
		await settle();
		expect(replace).not.toHaveBeenCalled();
	});
});
