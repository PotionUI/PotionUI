import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const goto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));

const { tabsStore } = await import('$lib/stores/tabs');
const { isToolTab } = await import('$lib/types/tabs');
const { openFieldInEditor, openToolShortcut } = await import('./openShortcut');
const { LAST_TOOL_STORAGE_KEY, lastToolFor } = await import('./lastTool');

const media = (kind: 'image' | 'video' | 'audio', id: string, extra: Record<string, unknown> = {}) => ({
	id,
	kind,
	url: `/api/media/uploads/${id}`,
	filename: id,
	...extra
});

const toolTabs = () => get(tabsStore).tabs.filter(isToolTab);
const activeId = () => get(tabsStore).activeTabId;

beforeEach(() => {
	const store = new Map<string, string>();
	Object.defineProperty(globalThis, 'localStorage', {
		configurable: true,
		value: {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key)
		}
	});
	goto.mockReset();
	vi.stubGlobal('window', { location: { pathname: '/history' } });
	tabsStore.reset();
});

afterEach(async () => {
	await new Promise((resolve) => setTimeout(resolve, 5));
});

describe('openToolShortcut', () => {
	it('opens an Image editor tab on the item with the tool active, and goes to Generate once the caller has closed its dialog', async () => {
		const opened = openToolShortcut('crop', [media('image', 'a', { itemId: 'a' })]);

		expect(opened).toBe(true);
		expect(toolTabs()).toHaveLength(1);
		expect(toolTabs()[0]).toMatchObject({
			tool: 'image',
			activeTool: 'crop',
			source: { url: '/api/media/uploads/a', item_id: 'a', type: 'image' }
		});
		expect(activeId()).toBe(toolTabs()[0].id);
		expect(goto).not.toHaveBeenCalled();
		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/generate'));
	});

	it('opens a Video editor tab for a clip', () => {
		openToolShortcut('trim', [media('video', 'v')]);

		expect(toolTabs()[0]).toMatchObject({ tool: 'video', activeTool: 'trim' });
	});

	it('opens one tab per selected item with the first one active', () => {
		openToolShortcut('resize', [media('image', 'a'), media('image', 'b'), media('image', 'c')]);

		expect(toolTabs().map((tab) => tab.source?.name)).toEqual(['a', 'b', 'c']);
		expect(toolTabs().every((tab) => tab.activeTool === 'resize')).toBe(true);
		expect(activeId()).toBe(toolTabs()[0].id);
	});

	it('opens both editors for a mixed selection and activates the first item', () => {
		openToolShortcut('crop', [media('video', 'v'), media('image', 'a')]);

		expect(toolTabs().map((tab) => tab.tool)).toEqual(['video', 'image']);
		expect(activeId()).toBe(toolTabs()[0].id);
	});

	it('passes the hint of a look or an effect to the tab', () => {
		openToolShortcut('look:noir', [media('image', 'a')]);
		openToolShortcut('fix_colors', [media('video', 'v')]);

		expect(toolTabs().map((tab) => [tab.activeTool, tab.hint])).toEqual([
			['effects', { look: 'noir' }],
			['effects', { effect: 'fix_colors' }]
		]);
	});

	it('does nothing for a selection with no shortcut', async () => {
		expect(openToolShortcut('trim', [media('audio', 's')])).toBe(false);
		expect(openToolShortcut('concat', [media('video', 'v')])).toBe(false);
		expect(openToolShortcut('crop', [])).toBe(false);

		await new Promise((resolve) => setTimeout(resolve, 5));
		expect(toolTabs()).toHaveLength(0);
		expect(goto).not.toHaveBeenCalled();
	});

	it('does not navigate when Generate is already open', async () => {
		vi.stubGlobal('window', { location: { pathname: '/generate' } });

		openToolShortcut('crop', [media('image', 'a')]);
		await new Promise((resolve) => setTimeout(resolve, 5));

		expect(goto).not.toHaveBeenCalled();
		expect(toolTabs()).toHaveLength(1);
	});

	it('focuses the tab that already holds the item and switches its tool', () => {
		openToolShortcut('crop', [media('image', 'a')]);
		const first = toolTabs()[0].id;

		openToolShortcut('resize', [media('image', 'a')]);

		expect(toolTabs()).toHaveLength(1);
		expect(toolTabs()[0]).toMatchObject({ id: first, activeTool: 'resize' });
		expect(activeId()).toBe(first);
	});

	it('makes E reopen the last tool used, per kind, starting on Paint and Trim', () => {
		openToolShortcut('edit-image', [media('image', 'a')]);
		openToolShortcut('edit-image', [media('video', 'v')]);
		expect(toolTabs().map((tab) => tab.activeTool)).toEqual(['paint', 'trim']);

		openToolShortcut('resize', [media('image', 'b')]);
		openToolShortcut('speed', [media('video', 'w')]);
		openToolShortcut('edit-image', [media('image', 'c')]);
		openToolShortcut('edit-image', [media('video', 'x')]);

		expect(toolTabs().map((tab) => tab.activeTool)).toEqual(['paint', 'trim', 'resize', 'speed', 'resize', 'speed']);
	});

	it('persists the per-kind memory in storage', () => {
		openToolShortcut('crop', [media('image', 'a')]);
		openToolShortcut('split', [media('video', 'v')]);

		expect(JSON.parse(localStorage.getItem(LAST_TOOL_STORAGE_KEY)!)).toEqual({ image: 'crop', video: 'split' });
		expect(lastToolFor('image')).toBe('crop');
	});
});

describe('openFieldInEditor', () => {
	const field = {
		path: '/storage/uploads/u1.png',
		relative_path: 'uploads/u1.png',
		url: '/api/media/uploads/u1.png',
		name: 'portrait.png'
	};

	it('opens the matching tab on the field media with Send to this field first', () => {
		const generationTab = get(tabsStore).activeTabId;

		const id = openFieldInEditor('image', field, 'init_image');

		expect(id).toBe(toolTabs()[0].id);
		expect(toolTabs()[0]).toMatchObject({
			tool: 'image',
			activeTool: 'paint',
			source: { relative_path: 'uploads/u1.png', name: 'portrait.png', type: 'image' },
			hint: { sendTo: { tabId: generationTab, field: 'init_image' } }
		});
		expect(activeId()).toBe(id);
		expect(goto).not.toHaveBeenCalled();
	});

	it('opens the Video editor on the last video tool', () => {
		openToolShortcut('speed', [media('video', 'v')]);

		openFieldInEditor('video', { ...field, url: '/api/media/uploads/clip.mp4' }, 'video');

		expect(toolTabs().at(-1)).toMatchObject({ tool: 'video', activeTool: 'speed' });
	});

	it('sends no hint for a field without a name and nothing for a value with no media', () => {
		openFieldInEditor('image', field, null);
		expect(toolTabs()[0].hint).toBeNull();

		expect(openFieldInEditor('image', {}, 'init_image')).toBeNull();
		expect(openFieldInEditor('image', null, 'init_image')).toBeNull();
		expect(toolTabs()).toHaveLength(1);
	});
});
