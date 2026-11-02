// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { A1111_TOOL, CORE_TOOLS } from './fixtures/mediaToolCatalog';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: vi.fn(),
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));
vi.mock('$lib/stores/toast', () => ({
	toasts: { show: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		addUploadsToCollection: vi.fn(),
		removeUploadsFromCollection: vi.fn(),
		addToCollection: vi.fn(),
		removeFromCollection: vi.fn(),
		listCollections: vi.fn(),
		listLibraryItems: vi.fn(),
		getGenerationHistory: vi.fn(),
		getLibraryFacets: vi.fn(),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { historyStore } = await import('$lib/stores/history');
const { syncCatalogTools } = await import('$lib/tools/catalogTools');
const { unregisterMediaTools } = await import('$lib/tools/tools');
const { registerCoreHistoryTools } = await import('../../src/routes/history/tools/coreTools');
const { recentLooks } = await import('$lib/filters/recentLooks');
const { default: LibrarySelectionToolbar } = await import(
	'../../src/routes/library/components/LibrarySelectionToolbar.svelte'
);
const { default: LibraryCard } = await import('../../src/routes/library/components/LibraryCard.svelte');
const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createHistoryEntryMenu, createLibraryEntryMenu } = await import('$lib/tools/entryMenu');
const { default: HistorySelectionToolbar } = await import(
	'../../src/routes/history/components/HistorySelectionToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

registerCoreHistoryTools();

type Kind = 'image' | 'video' | 'audio';

interface MenuGroup {
	label: string;
	tools: string[];
	subs: Record<string, string>;
	count?: number;
}

const EXT: Record<Kind, string> = { image: 'png', video: 'mp4', audio: 'wav' };

function libraryItem(id: string, kind: Kind) {
	return {
		id,
		filename: `${id}.${EXT[kind]}`,
		media_type: kind,
		url: `/u/${id}.${EXT[kind]}`,
		width: 640,
		height: 480,
		tags: []
	};
}

function generation(id: string, kind: Kind) {
	return {
		id,
		preset_id: 'p',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [
			{
				id: 1,
				file_path: `out/${id}.${EXT[kind]}`,
				file_type: kind,
				is_final: true,
				width: 640,
				height: 480,
				created_at: '2026-01-01T00:00:00Z'
			}
		],
		rating: 0,
		is_favorite: false
	};
}

function mountToolbar(component: unknown) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: component as never,
		target,
		props: { onBulkDeleteClick: () => {}, onToolSelect: () => {} }
	});
	return () => {
		instance.$destroy();
		target.remove();
	};
}

function readGroup(group: Element, label?: string): MenuGroup {
	const rows = Array.from(group.querySelectorAll('[data-tool]'));
	return {
		label: label ?? group.getAttribute('aria-label') ?? '',
		tools: rows.map((row) => row.getAttribute('data-tool') ?? ''),
		subs: Object.fromEntries(
			rows.map((row) => [row.getAttribute('data-tool') ?? '', row.querySelector('[data-tool-sub]')?.textContent ?? ''])
		)
	};
}

interface TopRow {
	id: string;
	label: string;
	count: number;
}

function topRows(): TopRow[] {
	return Array.from(document.body.querySelectorAll('[data-tools-menu] [data-submenu-row]')).map((row) => ({
		id: row.getAttribute('data-submenu-row') ?? '',
		label: (row.querySelector('span.truncate')?.textContent ?? '').trim(),
		count: Number(row.querySelector('[data-submenu-count]')?.textContent ?? '0')
	}));
}

async function readMenu(): Promise<MenuGroup[]> {
	const top = Array.from(document.body.querySelectorAll('[data-tools-menu] [role="group"]')).map((group) =>
		readGroup(group)
	);
	const drilled: MenuGroup[] = [];
	for (const entry of topRows()) {
		document.body.querySelector<HTMLButtonElement>(`[data-tools-menu] [data-submenu-row="${entry.id}"]`)!.click();
		await settle();
		const group = document.body.querySelector(`[data-tools-menu] [data-submenu-group="${entry.id}"]`)!;
		drilled.push({ ...readGroup(group, entry.label), count: entry.count });
		document.body.querySelector<HTMLButtonElement>('[data-tools-menu] [data-submenu-back]')!.click();
		await settle();
	}
	return [...drilled, ...top];
}

async function openMenu(): Promise<MenuGroup[]> {
	const trigger = Array.from(document.body.querySelectorAll('button')).find((button) =>
		button.textContent?.includes('Tools')
	)!;
	trigger.click();
	await settle();
	return readMenu();
}

let destroy: (() => void) | undefined;

async function libraryMenu(kind: Kind): Promise<MenuGroup[]> {
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: { items: [libraryItem('u1', kind)], total: 1, limit: 24, offset: 0 }
	} as never);
	await libraryStore.load();
	libraryStore.toggleSelect('u1');
	destroy = mountToolbar(LibrarySelectionToolbar);
	await settle();
	return openMenu();
}

async function historyMenu(kind: Kind): Promise<MenuGroup[]> {
	vi.mocked(api.getGenerationHistory).mockResolvedValue({
		success: true,
		data: { generations: [generation('g1', kind)], total: 1 }
	} as never);
	await historyStore.loadGenerations();
	historyStore.toggleSelect('g1');
	destroy = mountToolbar(HistorySelectionToolbar);
	await settle();
	return openMenu();
}

const byLabel = (groups: MenuGroup[], label: string) => groups.find((group) => group.label === label);

const HOST = {
	open: vi.fn(),
	remove: vi.fn(),
	deselect: vi.fn(),
	favorite: vi.fn(),
	addToCollection: vi.fn(),
	reuse: vi.fn(),
	rate: vi.fn(),
	copyToLibrary: vi.fn()
};

async function openCardTrigger(target: HTMLElement): Promise<MenuGroup[]> {
	target.querySelector<HTMLButtonElement>('[data-entry-menu-trigger]')!.click();
	await settle();
	return readMenu();
}

async function libraryCardMenu(kind: Kind): Promise<MenuGroup[]> {
	const item = libraryItem('u1', kind);
	const entryMenu = createLibraryEntryMenu({
		items: () => [item as never],
		selectedIds: () => [],
		host: HOST,
		onTool: vi.fn()
	});
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: LibraryCard as never,
		target,
		props: { item, entryMenu, tile: { width: 320, height: 160 } }
	});
	destroy = () => {
		instance.$destroy();
		target.remove();
	};
	return openCardTrigger(target);
}

async function historyCardMenu(kind: Kind): Promise<MenuGroup[]> {
	const gen = generation('g1', kind) as never;
	const entryMenu = createHistoryEntryMenu({
		generations: () => [gen],
		selectedIds: () => [],
		host: HOST,
		onTool: vi.fn()
	});
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: GenerationCard as never,
		target,
		props: { generation: gen, entryMenu, tile: { width: 320, height: 160 } }
	});
	destroy = () => {
		instance.$destroy();
		target.remove();
	};
	return openCardTrigger(target);
}

async function reopened<T>(open: () => Promise<T>): Promise<T> {
	destroy?.();
	destroy = undefined;
	document.body.innerHTML = '';
	libraryStore.reset();
	return open();
}

beforeEach(() => {
	recentLooks.set([]);
	libraryStore.reset();
	historyStore.clearSelection();
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	syncCatalogTools([...CORE_TOOLS, A1111_TOOL]);
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({ success: false } as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	document.body.innerHTML = '';
	libraryStore.reset();
	historyStore.clearSelection();
	vi.clearAllMocks();
});

describe('Tools menu: Edit and Effects on both pages', () => {
	it('Library, video: Edit lists the video tools, concat disabled with its range, no Effects', async () => {
		const groups = await libraryMenu('video');

		expect(groups.map((group) => group.label).slice(0, 1)).toEqual(['Edit']);
		expect(byLabel(groups, 'Edit')!.tools).toEqual([
			'trim',
			'crop',
			'resize',
			'rotate',
			'flip',
			'speed',
			'frame',
			'concat'
		]);
		expect(byLabel(groups, 'Edit')!.subs.frame).toBe('');
		expect(byLabel(groups, 'Edit')!.subs.concat).toBe('Select 2 to 8 items');
		expect(byLabel(groups, 'Effects')).toBeUndefined();
	});

	it('Library, image: Edit lists the image tools and Effects offers Apply a look', async () => {
		const groups = await libraryMenu('image');

		expect(byLabel(groups, 'Edit')!.tools).toEqual([
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'color_key',
			'crop_subject'
		]);
		expect(byLabel(groups, 'Effects')!.tools).toEqual(['apply-look']);
		expect(groups.map((group) => group.label).slice(0, 2)).toEqual(['Edit', 'Effects']);
	});

	it('Library, audio: Edit lists trim, speed and split and nothing else', async () => {
		const groups = await libraryMenu('audio');

		expect(byLabel(groups, 'Edit')!.tools).toEqual(['trim', 'speed', 'split']);
		expect(byLabel(groups, 'Edit')!.subs.split).toBe('Saves several files');
		expect(byLabel(groups, 'Effects')).toBeUndefined();
	});

	it.each(['image', 'video', 'audio'] as const)(
		'shows History the same Edit and Effects groups as Library for %s',
		async (kind) => {
			const library = await libraryMenu(kind);
			destroy?.();
			document.body.innerHTML = '';
			libraryStore.reset();
			const history = await historyMenu(kind);

			expect(byLabel(history, 'Edit')).toEqual(byLabel(library, 'Edit'));
			expect(byLabel(history, 'Effects')).toEqual(byLabel(library, 'Effects'));
		}
	);

	it('keeps the existing groups after Edit and Effects, in the same order on both pages', async () => {
		const library = (await libraryMenu('image')).map((group) => group.label);
		destroy?.();
		document.body.innerHTML = '';
		libraryStore.reset();
		const history = (await historyMenu('image')).map((group) => group.label);

		const shared = (labels: string[]) =>
			labels.filter((label) => ['Edit', 'Effects', 'Analyze', 'Compose', 'Export'].includes(label));
		expect(shared(library)).toEqual(['Edit', 'Effects', 'Analyze', 'Compose', 'Export']);
		expect(shared(history)).toEqual(shared(library));
	});

	it('offers the A1111 export on History only, in a Plugins group with the plugin named', async () => {
		const history = await historyMenu('image');
		expect(byLabel(history, 'Plugins')!.tools).toEqual([A1111_TOOL.id]);
		expect(byLabel(history, 'Plugins')!.subs[A1111_TOOL.id]).toBe('A1111 Metadata Export');
		destroy?.();
		document.body.innerHTML = '';
		libraryStore.reset();

		const library = await libraryMenu('image');
		expect(byLabel(library, 'Plugins')).toBeUndefined();
	});

	it('lists up to three recent looks under Apply a look, with the look group underneath, on both pages', async () => {
		recentLooks.set([
			{ id: 'noir', name: 'Noir', group: 'Black and white' },
			{ id: 'ember', name: 'Ember', group: 'Colour' },
			{ id: 'retro-tape:vhs', name: 'VHS', group: 'Retro Tape' }
		]);
		const library = await libraryMenu('image');
		destroy?.();
		document.body.innerHTML = '';
		libraryStore.reset();
		const history = await historyMenu('image');

		for (const groups of [library, history]) {
			const effects = byLabel(groups, 'Effects')!;
			expect(effects.tools).toEqual(['apply-look', 'look:noir', 'look:ember', 'look:retro-tape:vhs']);
			expect(effects.subs['look:noir']).toBe('Black and white');
			expect(effects.subs['look:retro-tape:vhs']).toBe('Retro Tape');
		}
	});

	it('shows no recent looks for a video or audio, where Effects is hidden', async () => {
		recentLooks.set([{ id: 'noir', name: 'Noir', group: 'Black and white' }]);

		const video = await libraryMenu('video');

		expect(byLabel(video, 'Effects')).toBeUndefined();
	});

	it('rebuilds an open bar menu when a look is applied', async () => {
		const before = await libraryMenu('image');
		expect(byLabel(before, 'Effects')!.tools).toEqual(['apply-look']);
		destroy?.();
		document.body.innerHTML = '';
		libraryStore.reset();

		recentLooks.set([{ id: 'noir', name: 'Noir', group: 'Black and white' }]);
		const after = await libraryMenu('image');

		expect(byLabel(after, 'Effects')!.tools).toEqual(['apply-look', 'look:noir']);
	});
});

describe('Tools menu: the Edit and Effects submenus at the top level', () => {
	const rowsNow = () => topRows().map((entry) => [entry.id, entry.label, entry.count]);

	it('Library, image: an Edit row of 7 and an Effects row of 1 stand in for the eight tools', async () => {
		const groups = await libraryMenu('image');

		expect(rowsNow()).toEqual([
			['edit', 'Edit', 7],
			['effects', 'Effects', 1]
		]);
		expect(byLabel(groups, 'Edit')!.count).toBe(byLabel(groups, 'Edit')!.tools.length);
		expect(document.body.querySelector('[data-tools-menu] > [data-tool="crop"]')).toBeNull();
		expect(document.body.querySelector('[data-tools-menu] [data-tool="crop"]')).toBeNull();
	});

	it('Library, video: an Edit row only, counting Concat that waits for a second clip', async () => {
		await libraryMenu('video');

		expect(rowsNow()).toEqual([['edit', 'Edit', 8]]);
	});

	it('Library, audio: an Edit row of three and no Effects row', async () => {
		await libraryMenu('audio');

		expect(rowsNow()).toEqual([['edit', 'Edit', 3]]);
	});

	it.each(['image', 'video', 'audio'] as const)('shows History the same rows and counts as Library for %s', async (kind) => {
		await libraryMenu(kind);
		const library = rowsNow();
		await reopened(() => historyMenu(kind));

		expect(rowsNow()).toEqual(library);
	});

	it('keeps the existing groups below the rows on the bar', async () => {
		await libraryMenu('image');

		const order = Array.from(
			document.body.querySelectorAll('[data-tools-menu] [data-submenu-row], [data-tools-menu] [role="group"]')
		).map((node) => node.getAttribute('data-submenu-row') ?? node.getAttribute('aria-label'));
		expect(order.slice(0, 2)).toEqual(['edit', 'effects']);
		expect(order).toEqual(expect.arrayContaining(['Analyze', 'Compose', 'Export']));
	});
});

describe('card menu: the same submenus', () => {
	const rowsNow = () => topRows().map((entry) => [entry.id, entry.label, entry.count]);

	it('Library card, image: Edit and Effects rows lead the menu, before Open', async () => {
		await libraryCardMenu('image');

		expect(rowsNow()).toEqual([
			['edit', 'Edit', 7],
			['effects', 'Effects', 1]
		]);
		const order = Array.from(
			document.body.querySelectorAll('[data-tools-menu] [data-submenu-row], [data-tools-menu] [role="group"]')
		).map((node) => node.getAttribute('data-submenu-row') ?? node.getAttribute('aria-label'));
		expect(order.slice(0, 3)).toEqual(['edit', 'effects', 'Open']);
	});

	it('hides what one card can never satisfy, so a video card counts one fewer than the bar', async () => {
		await libraryCardMenu('video');

		expect(rowsNow()).toEqual([['edit', 'Edit', 7]]);
	});

	it.each(['image', 'video', 'audio'] as const)(
		'gives a Library card and a History card the same Edit and Effects submenus for %s',
		async (kind) => {
			const library = await libraryCardMenu(kind);
			const libraryRows = rowsNow();
			const history = await reopened(() => historyCardMenu(kind));

			expect(rowsNow()).toEqual(libraryRows);
			expect(byLabel(history, 'Edit')).toEqual(byLabel(library, 'Edit'));
			expect(byLabel(history, 'Effects')).toEqual(byLabel(library, 'Effects'));
		}
	);

	it.each(['image', 'audio'] as const)('gives the card menu and the bar menu one submenu content for %s', async (kind) => {
		const bar = await libraryMenu(kind);
		const card = await reopened(() => libraryCardMenu(kind));

		expect(byLabel(card, 'Edit')).toEqual(byLabel(bar, 'Edit'));
		expect(byLabel(card, 'Effects')).toEqual(byLabel(bar, 'Effects'));
	});

	it('gives a video card the bar menu submenu without Concat', async () => {
		const bar = await libraryMenu('video');
		const card = await reopened(() => libraryCardMenu('video'));

		expect(byLabel(card, 'Edit')!.tools).toEqual(byLabel(bar, 'Edit')!.tools.filter((id) => id !== 'concat'));
		expect(byLabel(card, 'Edit')!.subs.frame).toBe('');
	});

	it('shows recent looks in the Effects submenu of a card, on both pages', async () => {
		recentLooks.set([
			{ id: 'noir', name: 'Noir', group: 'Black and white' },
			{ id: 'ember', name: 'Ember', group: 'Colour' }
		]);
		const library = await libraryCardMenu('image');
		const history = await reopened(() => historyCardMenu('image'));

		for (const groups of [library, history]) {
			expect(byLabel(groups, 'Effects')!.tools).toEqual(['apply-look', 'look:noir', 'look:ember']);
			expect(byLabel(groups, 'Effects')!.count).toBe(3);
		}
	});

	it('keeps the A1111 export in the Plugins group of a History card, outside the Edit submenu', async () => {
		const groups = await historyCardMenu('image');

		expect(byLabel(groups, 'Plugins')!.tools).toEqual([A1111_TOOL.id]);
		expect(byLabel(groups, 'Edit')!.tools).not.toContain(A1111_TOOL.id);
	});
});

