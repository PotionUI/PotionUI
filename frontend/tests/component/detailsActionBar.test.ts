// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { A1111_TOOL, BOOMERANG_TOOL, CORE_TOOLS, FIX_COLORS_TOOL, toolDto } from './fixtures/mediaToolCatalog';
import { settle } from './helpers/settle';

const openMediaTool = vi.fn();
const openPaintEditor = vi.fn();
const listMediaTools = vi.fn();

vi.mock('$lib/media-tools/open', () => ({
	openMediaTool: (...args: unknown[]) => openMediaTool(...args),
	openPaintEditor: (...args: unknown[]) => openPaintEditor(...args)
}));
vi.mock('$lib/services/api/mediaTools', () => ({ listMediaTools: (...args: unknown[]) => listMediaTools(...args) }));
vi.mock('$lib/stores/toast', () => ({
	toasts: { show: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn().mockResolvedValue({ success: false }),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: false }),
		listCollections: vi.fn().mockResolvedValue({ success: false }),
		getTags: vi.fn().mockResolvedValue([
			{ id: 't1', name: 'Sky', type: 'GENERATION' },
			{ id: 't2', name: 'Sea', type: 'GENERATION' }
		])
	}
}));
vi.mock('$lib/media/editors/MediaEditors.svelte', async () => ({
	default: (await import('./fixtures/MediaEditorsStub.svelte')).default
}));
vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));

const { default: DetailsActionBar } = await import('$lib/media-tools/DetailsActionBar.svelte');
const { resetMediaToolCatalog } = await import('$lib/media-tools/catalog');
const { unregisterMediaTools } = await import('$lib/tools/tools');
const { registerEntryTools } = await import('$lib/tools/entryTools');
const { recentLooks } = await import('$lib/filters/recentLooks');

registerEntryTools();

type Kind = 'image' | 'video' | 'audio' | 'mesh';

const WIDTHS: Record<string, number> = {
	edit: 78,
	effects: 94,
	favorite: 80,
	tags: 66,
	reuse: 118,
	'copy-to-library': 124,
	'auto-file': 96,
	download: 100,
	more: 62
};

let observers: Array<(entries: unknown[]) => void> = [];
let cleanup: (() => void) | undefined;
let lastInstance: { triggerShortcut: (key: string) => boolean } | undefined;

class FakeResizeObserver {
	constructor(callback: (entries: unknown[]) => void) {
		observers.push(callback);
	}
	observe() {}
	unobserve() {}
	disconnect() {}
}

function item(kind: Kind, extra: Record<string, unknown> = {}) {
	return {
		id: 'row-1',
		kind,
		url: `/api/media/uploads/row-1.${kind}`,
		filename: kind === 'image' ? 'photo.png' : `clip.${kind}`,
		width: 640,
		height: 480,
		itemId: 'row-1',
		...extra
	};
}

function historyItem(kind: Kind, extra: Record<string, unknown> = {}) {
	return item(kind, { itemId: undefined, generationId: 'g1', generationFileId: '4', paramIndex: 0, ...extra });
}

function historyContext(kind: Kind, extra: Record<string, unknown> = {}) {
	const entry = historyItem(kind, extra);
	return {
		scope: 'history',
		items: [entry],
		kinds: new Set([kind]),
		collectionId: null,
		generations: [{ id: 'g1', files: [], status: 'completed' }],
		generationIds: ['g1'],
		files: [],
		entry: { status: 'completed', stackCells: null, looseCell: false, error: null }
	};
}

const action = (id: string, label: string, extra: Record<string, unknown> = {}) => ({
	id,
	label,
	icon: 'download',
	onclick: vi.fn(),
	...extra
});

function historyProps(kind: Kind = 'image', extra: Record<string, unknown> = {}) {
	return {
		scope: 'history',
		items: [historyItem(kind)],
		context: historyContext(kind),
		favorite: { active: false, onToggle: vi.fn() },
		tags: { selectedIds: ['t1', 't2', 't3'], onChange: vi.fn() },
		pageActions: [
			action('reuse', 'Reuse settings', { icon: 'refresh', tooltip: 'Reuse' }),
			action('copy-to-library', 'Copy to Library', { icon: 'photo' })
		],
		downloadActions: [
			action('download-file', 'Download file', { shortcut: 'D', description: 'a.png · 3 MB' }),
			action('export-bundle', 'Export bundle', { icon: 'box' })
		],
		moreActions: [
			action('publish', 'Publish to Inspirations', { icon: 'lightbulb' }),
			action('open-new-tab', 'Open in new tab', { icon: 'external-link' })
		],
		pluginContext: { generationId: 'g1', fileIndex: 0, fileType: kind },
		...extra
	};
}

function libraryProps(kind: Kind = 'image', extra: Record<string, unknown> = {}) {
	return {
		scope: 'library',
		items: [item(kind)],
		favorite: { active: true, onToggle: vi.fn() },
		tags: { selectedIds: [], tagType: 'UPLOAD', onChange: vi.fn() },
		pageActions: [action('auto-file', 'Auto-file…', { icon: 'sparkles' })],
		downloadActions: [action('download-file', 'Download file', { shortcut: 'D' })],
		moreActions: [action('open-new-tab', 'Open in new tab', { icon: 'external-link' })],
		dangerActions: [action('delete', 'Delete', { icon: 'trash', tone: 'danger' })],
		...extra
	};
}

function render(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(DetailsActionBar, { target, props: props as never });
	lastInstance = instance as never;
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

async function renderSettled(props: Record<string, unknown>, width = 1200) {
	const target = render(props);
	await settle();
	flushSync();
	resize(width);
	await settle(3);
	flushSync();
	return target;
}

function resize(available: number) {
	for (const callback of observers) callback([{ contentRect: { width: available } }]);
	flushSync();
}

const bar = (root: ParentNode) => root.querySelector<HTMLElement>('[data-details-action-bar]')!;
const barItems = (root: ParentNode) =>
	Array.from(bar(root).querySelectorAll<HTMLElement>('[data-bar-item]'));
const barIds = (root: ParentNode) => barItems(root).map((node) => node.dataset.barItem);
const itemLabel = (node: HTMLElement) => (node.textContent ?? '').replace(/\s+/g, ' ').trim();
const barLabels = (root: ParentNode) => barItems(root).map(itemLabel);
const trigger = (root: ParentNode, id: string) =>
	bar(root).querySelector<HTMLElement>(`[data-bar-item="${id}"]`)!;
const menu = () => document.body.querySelector<HTMLElement>('[data-bar-menu]');
const rows = () =>
	Array.from(document.body.querySelectorAll<HTMLElement>('[data-bar-menu] [data-bar-row]'));
const rowIds = () => rows().map((row) => row.dataset.barRow);
const dividers = () => document.body.querySelectorAll('[data-bar-menu] [role="separator"]').length;

async function open(root: ParentNode, id: string) {
	trigger(root, id).click();
	flushSync();
	await settle(4);
	flushSync();
}

function key(target: Element | null, name: string, init: KeyboardEventInit = {}) {
	(target ?? document.body).dispatchEvent(
		new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init })
	);
	flushSync();
}

beforeEach(() => {
	observers = [];
	vi.stubGlobal('ResizeObserver', FakeResizeObserver);
	vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
		const id = this.dataset?.barMeasure;
		const width = id ? (WIDTHS[id] ?? 90) : 0;
		return { width, height: 32, top: 0, left: 0, right: width, bottom: 32, x: 0, y: 0, toJSON: () => ({}) };
	});
	recentLooks.set([]);
	openMediaTool.mockReset();
	openPaintEditor.mockReset();
	listMediaTools.mockReset();
	listMediaTools.mockResolvedValue([...CORE_TOOLS, A1111_TOOL]);
	resetMediaToolCatalog();
	unregisterMediaTools((tool) => !!tool.catalogToolId);
});

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('bar content', () => {
	it('lists the History image bar in the approved order, labelled', async () => {
		const root = await renderSettled(historyProps());

		expect(barLabels(root)).toEqual([
			'Edit',
			'Effects',
			'Favorite',
			'Tags 3',
			'Reuse settings',
			'Copy to Library',
			'Download',
			'More'
		]);
		expect(bar(root).getAttribute('role')).toBe('toolbar');
	});

	it('lists the Library image bar with Auto-file and a plain Download button', async () => {
		const root = await renderSettled(libraryProps());

		expect(barLabels(root)).toEqual(['Edit', 'Effects', 'Favorite', 'Tags', 'Auto-file…', 'Download', 'More']);
		expect(trigger(root, 'download').hasAttribute('aria-haspopup')).toBe(false);
	});

	it('draws a divider between the tool menus, Favorite and Tags, and the page actions', async () => {
		const root = await renderSettled(historyProps());

		expect(bar(root).querySelectorAll('[data-bar-divider]')).toHaveLength(2);
	});

	it('pins Download and More to the right', async () => {
		const root = await renderSettled(historyProps());

		const right = trigger(root, 'more').closest('.ml-auto')!;
		expect(Array.from(right.querySelectorAll('[data-bar-item]')).map((node) => (node as HTMLElement).dataset.barItem)).toEqual([
			'download',
			'more'
		]);
	});

	it('gives a video no Effects, and keeps its Edit list', async () => {
		const root = await renderSettled(historyProps('video'));

		expect(barIds(root)).not.toContain('effects');
		await open(root, 'edit');
		expect(rowIds()).toEqual(['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame']);
	});

	it('lists Fix colours under Effects for a video and runs it through openMediaTool', async () => {
		listMediaTools.mockResolvedValue([...CORE_TOOLS, A1111_TOOL, FIX_COLORS_TOOL]);
		const props = libraryProps('video');
		const root = await renderSettled(props);

		await open(root, 'edit');
		expect(rowIds()).not.toContain('fix_colors');
		trigger(root, 'edit').click();
		flushSync();
		await settle(2);

		await open(root, 'effects');
		expect(rowIds()).toEqual(['fix_colors']);
		expect(rows()[0].textContent).toContain('Fix colours');
		rows()[0].click();
		flushSync();

		expect(openMediaTool).toHaveBeenCalledTimes(1);
		expect(openMediaTool.mock.calls[0][0]).toMatchObject({ toolId: 'fix_colors', scope: 'library' });
	});

	it('gives an audio item the catalog audio tools and no Effects', async () => {
		const root = await renderSettled(libraryProps('audio'));

		expect(barIds(root)).not.toContain('effects');
		await open(root, 'edit');
		expect(rowIds()).toEqual(['trim', 'speed', 'split']);
	});

	it('shows no Edit or Effects for media nothing edits, but keeps the page actions', async () => {
		const root = await renderSettled(libraryProps('mesh'));

		expect(barIds(root)).toEqual(['favorite', 'tags', 'auto-file', 'download', 'more']);
	});

	it('shows Favorite signal blue and pressed when on, and calls the toggle', async () => {
		const props = libraryProps();
		const root = await renderSettled(props);
		const favorite = trigger(root, 'favorite');

		expect(favorite.getAttribute('aria-pressed')).toBe('true');
		expect(favorite.className).toContain('text-signal');
		expect(favorite.hasAttribute('data-library-favorite')).toBe(true);
		favorite.click();
		expect(props.favorite.onToggle).toHaveBeenCalledTimes(1);
	});

	it('leaves Favorite unpressed when off', async () => {
		const root = await renderSettled(historyProps());

		expect(trigger(root, 'favorite').getAttribute('aria-pressed')).toBe('false');
		expect(trigger(root, 'favorite').className).not.toContain('text-signal');
	});

	it('shows the tag count on Tags, and none when empty', async () => {
		const root = await renderSettled(historyProps());
		expect(trigger(root, 'tags').querySelector('[data-tags-count]')?.textContent).toBe('3');
		cleanup?.();

		const empty = await renderSettled(libraryProps());
		expect(trigger(empty, 'tags').querySelector('[data-tags-count]')).toBeNull();
	});

	it('opens the tag selector as a popover from Tags', async () => {
		const root = await renderSettled(historyProps());

		trigger(root, 'tags').click();
		flushSync();
		await settle(3);
		flushSync();

		expect(trigger(root, 'tags').getAttribute('aria-expanded')).toBe('true');
		expect(document.body.querySelector('input[role="combobox"]')).not.toBeNull();
	});
});

describe('Edit and Effects menus', () => {
	it('lists the image Edit tools with Edit image last, and runs a catalog tool through openMediaTool', async () => {
		const props = historyProps();
		const root = await renderSettled(props);
		await open(root, 'edit');

		expect(rowIds()).toEqual([
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'color_key',
			'crop_subject',
			'edit-image'
		]);
		rows()[0].click();
		flushSync();

		expect(openMediaTool).toHaveBeenCalledTimes(1);
		expect(openMediaTool.mock.calls[0][0]).toMatchObject({
			toolId: 'crop',
			scope: 'history',
			context: props.context
		});
		expect(openMediaTool.mock.calls[0][0].announce).toBeDefined();
		expect(menu()).toBeNull();
	});

	it('does not announce a Library run', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'edit');
		rows()[0].click();

		expect(openMediaTool.mock.calls[0][0].announce).toBeUndefined();
		expect(openMediaTool.mock.calls[0][0].scope).toBe('library');
	});

	it('disables Crop to subject for an image known to be opaque, with its reason', async () => {
		const root = await renderSettled(libraryProps('image', { items: [item('image', { hasAlpha: false })] }));
		await open(root, 'edit');

		const row = rows().find((entry) => entry.dataset.barRow === 'crop_subject')!;
		expect(row.getAttribute('aria-disabled')).toBe('true');
		expect(row.textContent).toContain('Needs a transparent image');
		row.click();
		expect(openMediaTool).not.toHaveBeenCalled();
	});

	it('enables Crop to subject for a transparent image', async () => {
		const root = await renderSettled(libraryProps('image', { items: [item('image', { hasAlpha: true })] }));
		await open(root, 'edit');

		const row = rows().find((entry) => entry.dataset.barRow === 'crop_subject')!;
		expect(row.getAttribute('aria-disabled')).toBeNull();
	});

	it('puts plugin Edit tools after a divider and names the plugin', async () => {
		listMediaTools.mockResolvedValue([...CORE_TOOLS, BOOMERANG_TOOL]);
		const root = await renderSettled(libraryProps('video'));
		await open(root, 'edit');

		expect(rowIds().at(-1)).toBe('boomerang.loop');
		expect(dividers()).toBe(1);
		expect(rows().at(-1)!.textContent).toContain('Plugin');
		expect(rows().at(-1)!.textContent).toContain('Boomerang');
	});

	it('lists Apply a look and the recent looks under Effects, and updates when one is applied', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'effects');
		expect(rowIds()).toEqual(['apply-look']);
		trigger(root, 'effects').click();
		flushSync();
		await settle(2);

		recentLooks.set([{ id: 'noir', name: 'Noir', group: 'Black and white' }]);
		flushSync();
		await open(root, 'effects');

		expect(rowIds()).toEqual(['apply-look', 'look:noir']);
		expect(rows()[1].textContent).toContain('Black and white');
	});

	it('shows the same Edit and Effects rows on both pages for the same media', async () => {
		const library = await renderSettled(libraryProps('image'));
		await open(library, 'edit');
		const libraryEdit = rowIds();
		cleanup?.();
		document.body.innerHTML = '';

		const history = await renderSettled(historyProps('image'));
		await open(history, 'edit');

		expect(rowIds()).toEqual(libraryEdit);
	});
});

describe('running things', () => {
	it('hands the tool the generated file with its generation file id', async () => {
		const root = await renderSettled(historyProps());
		await open(root, 'edit');
		rows()[0].click();

		expect(openMediaTool.mock.calls[0][0].items[0]).toMatchObject({ generationId: 'g1', generationFileId: '4' });
	});

	it('passes what a tool produced back to the page', async () => {
		const onResult = vi.fn();
		const root = await renderSettled(libraryProps('image', { onResult }));
		await open(root, 'edit');
		rows()[1].click();

		const produced = [{ id: 'new-1', filename: 'photo-resized.png' }];
		const info = { toolId: 'resize', replaced: false };
		await openMediaTool.mock.calls[0][0].onResult(produced, info);

		expect(onResult).toHaveBeenCalledWith(produced, info);
	});

	it('runs a page action when it is pressed, and disables it while loading', async () => {
		const props = historyProps('image', {
			pageActions: [action('reuse', 'Reuse settings'), action('copy-to-library', 'Copy to Library', { loading: true })]
		});
		const root = await renderSettled(props);

		trigger(root, 'reuse').click();
		trigger(root, 'copy-to-library').click();

		expect(props.pageActions[0].onclick).toHaveBeenCalledTimes(1);
		expect((trigger(root, 'copy-to-library') as HTMLButtonElement).disabled).toBe(true);
		expect(props.pageActions[1].onclick).not.toHaveBeenCalled();
	});

	it('loads the catalog by itself so a page that never did still shows tools', async () => {
		await renderSettled(libraryProps());

		expect(listMediaTools).toHaveBeenCalled();
	});
});

describe('key hints', () => {
	it('every hint shown in a bar menu is bound', async () => {
		const props = historyProps();
		const root = await renderSettled(props);
		const hints: string[] = [];
		for (const id of ['edit', 'download', 'more']) {
			await open(root, id);
			for (const node of document.body.querySelectorAll('[data-bar-menu] kbd')) hints.push((node.textContent ?? '').trim());
			key(document.activeElement, 'Escape');
			await settle(3);
			flushSync();
		}

		expect(hints.sort()).toEqual(['D', 'E']);
		expect(lastInstance!.triggerShortcut('e')).toBe(true);
		expect(lastInstance!.triggerShortcut('d')).toBe(true);
		expect(props.downloadActions[0].onclick).toHaveBeenCalledTimes(1);
	});

	it('F toggles the favorite the tooltip names, and Z is not a shortcut', async () => {
		const props = historyProps();
		await renderSettled(props);

		expect(lastInstance!.triggerShortcut('f')).toBe(true);
		expect(props.favorite.onToggle).toHaveBeenCalledTimes(1);
		expect(lastInstance!.triggerShortcut('z')).toBe(false);
	});

	it('E does nothing for a video, where Edit image is not offered', async () => {
		await renderSettled(historyProps('video'));

		expect(lastInstance!.triggerShortcut('e')).toBe(false);
	});
});

describe('menu details', () => {
	it('puts a divider before Edit image', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'edit');

		const labels = Array.from(document.body.querySelectorAll('[data-bar-menu] [role="separator"], [data-bar-menu] [data-bar-row]')).map(
			(node) => (node as HTMLElement).dataset.barRow ?? 'sep'
		);
		expect(labels.slice(-3)).toEqual(['crop_subject', 'sep', 'edit-image']);
	});

	it('shows only a described action second line, not its tooltip', async () => {
		const root = await renderSettled(historyProps(), 304);
		await open(root, 'more');

		expect(rows().find((row) => row.dataset.barRow === 'reuse')!.querySelector('[data-bar-row-sub]')).toBeNull();
	});

	it('labels the folded Tags row with its count on the right', async () => {
		const root = await renderSettled(historyProps(), 304);
		await open(root, 'more');

		const row = rows().find((entry) => entry.dataset.barRow === 'tags')!;
		expect(row.textContent).toContain('Tags…');
		expect(row.textContent).toContain('3');
	});

	it('opens Edit and Effects at the narrower menu width and the rest wider', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'edit');

		expect(menu()!.style.width).toBe('256px');
	});

	it('gives a phone sheet a title, the file name and a close button', async () => {
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: query.includes('max-width: 639px'),
			media: query,
			addEventListener: () => undefined,
			removeEventListener: () => undefined
		}));
		const root = await renderSettled(historyProps(), 374);
		await open(root, 'more');

		const header = document.body.querySelector('[data-bar-sheet-header]')!;
		expect(header.textContent).toContain('More');
		expect(header.textContent).toContain('photo.png');
		(header.querySelector('button') as HTMLElement).click();
		flushSync();
		await settle(2);
		expect(menu()).toBeNull();
	});
});

describe('image editor entry points', () => {
	const stub = () => document.body.querySelector('[data-editor-stub]');

	it('opens the paint editor from Edit image', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'edit');
		rows().find((row) => row.dataset.barRow === 'edit-image')!.click();
		flushSync();

		expect(stub()?.getAttribute('data-kind')).toBe('paint');
		expect(stub()?.getAttribute('data-initial-tool')).toBe('');
	});

	it('opens the editor on Filters from Apply a look', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'effects');
		rows()[0].click();
		flushSync();

		expect(stub()?.getAttribute('data-initial-tool')).toBe('filters');
		expect(stub()?.getAttribute('data-has-initial-filter')).toBe('false');
	});

	it('opens the editor on the look that was clicked, keeping a colon in the id', async () => {
		recentLooks.set([{ id: 'retro-tape:vhs', name: 'VHS', group: 'Retro Tape' }]);
		const root = await renderSettled(libraryProps());
		await open(root, 'effects');
		rows()[1].click();
		flushSync();

		expect(stub()?.getAttribute('data-initial-filter')).toBe('retro-tape:vhs');
	});

	it('reports editing while the editor is open', async () => {
		const onEditingChange = vi.fn();
		const root = await renderSettled(libraryProps('image', { onEditingChange }));
		await open(root, 'edit');
		rows().find((row) => row.dataset.barRow === 'edit-image')!.click();
		flushSync();
		await settle(2);

		expect(onEditingChange).toHaveBeenLastCalledWith(true);
	});
});

describe('Download and More menus', () => {
	it('lists Download file, Export bundle and the A1111 plugin entry after a divider, naming its plugin', async () => {
		const root = await renderSettled(historyProps());
		await open(root, 'download');

		expect(rowIds()).toEqual(['download-file', 'export-bundle', A1111_TOOL.id]);
		expect(dividers()).toBe(1);
		const plugin = rows()[2];
		expect(plugin.textContent).toContain('Download with A1111 metadata');
		expect(plugin.textContent).toContain('Plugin');
		expect(rows()[0].textContent).toContain('a.png · 3 MB');
	});

	it('filters plugin download entries by what they accept', async () => {
		const root = await renderSettled(historyProps('video'));
		await open(root, 'download');

		expect(rowIds()).toEqual(['download-file', 'export-bundle']);
	});

	it('runs the plugin download entry through openMediaTool with the history context', async () => {
		const props = historyProps();
		const root = await renderSettled(props);
		await open(root, 'download');
		rows()[2].click();

		expect(openMediaTool.mock.calls[0][0]).toMatchObject({ toolId: A1111_TOOL.id, scope: 'history' });
		expect(openMediaTool.mock.calls[0][0].context).toBe(props.context);
	});

	it('runs an entry and closes the menu', async () => {
		const props = historyProps();
		const root = await renderSettled(props);
		await open(root, 'download');
		rows()[1].click();
		flushSync();

		expect(props.downloadActions[1].onclick).toHaveBeenCalledTimes(1);
		expect(menu()).toBeNull();
	});

	it('turns a plain Library Download into a menu once a plugin adds an entry', async () => {
		const libraryPlugin = toolDto({
			id: 'civitai.upload',
			label: 'Upload to Civitai',
			icon: 'download',
			accepts: ['image'],
			scopes: ['library', 'history'],
			component: 'plugin:civitai:Upload.js',
			runnable: false,
			source: 'civitai'
		});
		listMediaTools.mockResolvedValue([...CORE_TOOLS, libraryPlugin]);
		const root = await renderSettled(libraryProps());

		expect(trigger(root, 'download').getAttribute('aria-haspopup')).toBe('menu');
		await open(root, 'download');
		expect(rowIds()).toEqual(['download-file', 'civitai.upload']);
	});

	it('calls the plain Library Download directly', async () => {
		const props = libraryProps();
		const root = await renderSettled(props);
		trigger(root, 'download').click();

		expect(props.downloadActions[0].onclick).toHaveBeenCalledTimes(1);
	});

	it('lists Publish, Open in new tab and the plugin actions in More, with plugin actions after a divider', async () => {
		const root = await renderSettled(historyProps());
		await open(root, 'more');

		expect(rowIds()).toEqual(['publish', 'open-new-tab']);
		const slot = document.body.querySelector('[data-bar-plugin-actions] [data-plugin-slot-stub]')!;
		expect(slot.getAttribute('data-hook')).toBe('image.actions');
		expect(slot.getAttribute('data-context')).toContain('g1');
	});

	it('keeps a plugin that already has a Download entry out of the More plugin actions', async () => {
		const root = await renderSettled(historyProps());
		await open(root, 'more');

		const slot = document.body.querySelector('[data-plugin-slot-stub]')!;
		expect(slot.getAttribute('data-exclude')).toBe('a1111-metadata-export');
	});

	it('renders no plugin slot on the Library bar', async () => {
		const root = await renderSettled(libraryProps());
		await open(root, 'more');

		expect(document.body.querySelector('[data-plugin-slot-stub]')).toBeNull();
	});

	it('puts Library Delete last in More, in danger style', async () => {
		const props = libraryProps();
		const root = await renderSettled(props);
		await open(root, 'more');

		expect(rowIds()).toEqual(['open-new-tab', 'delete']);
		const remove = rows()[1];
		expect(remove.className).toContain('text-danger');
		remove.click();
		expect(props.dangerActions[0].onclick).toHaveBeenCalledTimes(1);
	});

	it('shows a loading action as disabled and does not run it', async () => {
		const props = historyProps('image', {
			downloadActions: [
				action('download-file', 'Download file'),
				action('export-bundle', 'Export bundle', { loading: true })
			]
		});
		const root = await renderSettled(props);
		await open(root, 'download');

		expect(rows()[1].getAttribute('aria-disabled')).toBe('true');
		rows()[1].click();
		expect(props.downloadActions[1].onclick).not.toHaveBeenCalled();
	});
});

describe('overflow', () => {
	const history = (width: number) => renderSettled(historyProps(), width);

	it('shows everything at a wide bar', async () => {
		const root = await history(808);

		expect(barIds(root)).toEqual([
			'edit',
			'effects',
			'favorite',
			'tags',
			'reuse',
			'copy-to-library',
			'download',
			'more'
		]);
	});

	it('folds Download first', async () => {
		const root = await history(700);

		expect(barIds(root)).toEqual(['edit', 'effects', 'favorite', 'tags', 'reuse', 'copy-to-library', 'more']);
	});

	it('folds Copy to Library second, then Reuse settings, then Tags, then Favorite', async () => {
		expect(barIds(await history(580))).toEqual(['edit', 'effects', 'favorite', 'tags', 'reuse', 'more']);
		cleanup?.();
		expect(barIds(await history(454))).toEqual(['edit', 'effects', 'favorite', 'tags', 'more']);
		cleanup?.();
		expect(barIds(await history(374))).toEqual(['edit', 'effects', 'favorite', 'more']);
		cleanup?.();
		expect(barIds(await history(304))).toEqual(['edit', 'effects', 'more']);
	});

	it('never folds Edit, Effects or More, however narrow', async () => {
		const root = await history(40);

		expect(barIds(root)).toEqual(['edit', 'effects', 'more']);
	});

	it('matches the approved 1024 state: Edit, Effects, Favorite, Tags, More', async () => {
		const root = await history(454);
		await open(root, 'more');

		expect(barLabels(root)).toEqual(['Edit', 'Effects', 'Favorite', 'Tags 3', 'More']);
		expect(rowIds()).toEqual([
			'reuse',
			'copy-to-library',
			'download-file',
			'export-bundle',
			A1111_TOOL.id,
			'publish',
			'open-new-tab'
		]);
		expect(dividers()).toBe(2);
	});

	it('lists folded Favorite and Tags at the top of More, in bar order', async () => {
		const root = await history(304);
		await open(root, 'more');

		expect(rowIds().slice(0, 4)).toEqual(['favorite', 'tags', 'reuse', 'copy-to-library']);
	});

	it('toggles Favorite from More when it has folded', async () => {
		const props = historyProps();
		const root = await renderSettled(props, 304);
		await open(root, 'more');
		rows()[0].click();

		expect(props.favorite.onToggle).toHaveBeenCalledTimes(1);
	});

	it('opens the tag popover from the folded Tags row', async () => {
		const root = await renderSettled(historyProps(), 304);
		await open(root, 'more');
		rows()[1].click();
		flushSync();
		await settle(3);
		flushSync();

		expect(document.body.querySelector('input[role="combobox"]')).not.toBeNull();
	});

	it('folds Library Auto-file in the Reuse settings slot, after Download', async () => {
		expect(barIds(await renderSettled(libraryProps(), 430))).toEqual([
			'edit',
			'effects',
			'favorite',
			'tags',
			'more'
		]);
		cleanup?.();
		const root = await renderSettled(libraryProps(), 360);
		expect(barIds(root)).toEqual(['edit', 'effects', 'favorite', 'more']);
		await open(root, 'more');
		expect(rowIds()).toEqual(['tags', 'auto-file', 'download-file', 'open-new-tab', 'delete']);
	});

	it('refolds as the bar grows again', async () => {
		const root = await history(304);
		expect(barIds(root)).toEqual(['edit', 'effects', 'more']);

		resize(808);
		await settle(2);
		flushSync();

		expect(barIds(root)).toHaveLength(8);
	});
});

describe('keyboard', () => {
	it('keeps one tab stop in the bar', async () => {
		const root = await renderSettled(historyProps());

		expect(barItems(root).filter((node) => node.tabIndex === 0)).toHaveLength(1);
		expect(barItems(root).filter((node) => node.tabIndex === 0)[0].dataset.barItem).toBe('edit');
	});

	it('moves between buttons with Left and Right, wrapping, and with Home and End', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'edit').focus();

		key(document.activeElement, 'ArrowRight');
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('effects');
		key(document.activeElement, 'ArrowLeft');
		key(document.activeElement, 'ArrowLeft');
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('more');
		key(document.activeElement, 'ArrowRight');
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('edit');
		key(document.activeElement, 'End');
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('more');
		key(document.activeElement, 'Home');
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('edit');
	});

	it('moves the tab stop to the focused button', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'favorite').focus();
		flushSync();

		expect(trigger(root, 'favorite').tabIndex).toBe(0);
		expect(trigger(root, 'edit').tabIndex).toBe(-1);
	});

	it('does not reach folded buttons with the arrows', async () => {
		const root = await renderSettled(historyProps(), 304);
		trigger(root, 'effects').focus();

		key(document.activeElement, 'ArrowRight');

		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('more');
	});

	it('opens a menu with ArrowDown and lands on its first enabled row', async () => {
		const root = await renderSettled(libraryProps('image', { items: [item('image', { hasAlpha: false })] }));
		trigger(root, 'edit').focus();

		key(document.activeElement, 'ArrowDown');
		await settle(4);
		flushSync();

		expect(menu()).not.toBeNull();
		expect((document.activeElement as HTMLElement).dataset.barRow).toBe('crop');
	});

	it('walks a menu with Up and Down, wrapping, skipping disabled rows, with Home and End', async () => {
		const root = await renderSettled(libraryProps('image', { items: [item('image', { hasAlpha: false })] }));
		await open(root, 'edit');
		const focusedRow = () => (document.activeElement as HTMLElement).dataset.barRow;

		expect(focusedRow()).toBe('crop');
		key(document.activeElement, 'ArrowUp');
		expect(focusedRow()).toBe('edit-image');
		key(document.activeElement, 'ArrowDown');
		expect(focusedRow()).toBe('crop');
		key(document.activeElement, 'End');
		expect(focusedRow()).toBe('edit-image');
		key(document.activeElement, 'ArrowUp');
		expect(focusedRow()).toBe('color_key');
		key(document.activeElement, 'Home');
		expect(focusedRow()).toBe('crop');
	});

	it('closes a menu with Escape and returns focus to its trigger', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'edit').focus();
		await open(root, 'edit');

		key(document.activeElement, 'Escape');
		await settle(3);
		flushSync();

		expect(menu()).toBeNull();
		expect(document.activeElement).toBe(trigger(root, 'edit'));
	});

	it('closes a menu on Tab and leaves focus on the trigger so Tab moves on from there', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'edit').focus();
		await open(root, 'edit');

		key(document.activeElement, 'Tab');
		await settle(2);
		flushSync();

		expect(menu()).toBeNull();
		expect(document.activeElement).toBe(trigger(root, 'edit'));
	});

	it('closes a menu on Right and moves to the neighbouring bar button', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'edit').focus();
		await open(root, 'edit');

		key(document.activeElement, 'ArrowRight');
		await settle(2);
		flushSync();

		expect(menu()).toBeNull();
		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('effects');
	});

	it('closes a menu on Left and moves to the previous bar button, wrapping to More from Edit', async () => {
		const root = await renderSettled(historyProps());
		trigger(root, 'edit').focus();
		await open(root, 'edit');

		key(document.activeElement, 'ArrowLeft');
		await settle(2);
		flushSync();

		expect((document.activeElement as HTMLElement).dataset.barItem).toBe('more');
	});

	it('closes a menu when the click lands outside it', async () => {
		const root = await renderSettled(historyProps());
		await open(root, 'edit');

		document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		flushSync();
		await settle(2);

		expect(menu()).toBeNull();
	});
});

describe('phones', () => {
	beforeEach(() => {
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: query.includes('max-width: 639px'),
			media: query,
			addEventListener: () => undefined,
			removeEventListener: () => undefined
		}));
	});

	it('opens menus as a bottom sheet with a scrim', async () => {
		const root = await renderSettled(historyProps(), 374);
		await open(root, 'more');

		expect(document.body.querySelector('[data-bar-sheet]')).not.toBeNull();
		expect(document.body.querySelector('[data-bar-sheet-root] .bg-black\\/60')).not.toBeNull();
	});

	it('hides the keyboard hints in a sheet', async () => {
		const root = await renderSettled(historyProps(), 374);
		await open(root, 'edit');

		expect(document.body.querySelector('[data-bar-menu] kbd')).toBeNull();
	});
});
