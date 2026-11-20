import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const getMock = vi.fn();
const confirmMock = vi.fn();

vi.mock('../../src/lib/services/api/index', () => ({
	api: { getClient: () => ({ get: getMock }) }
}));

vi.mock('$lib/stores/confirm', () => ({
	confirmDialog: (...args: unknown[]) => confirmMock(...args)
}));

const { default: CollectionLibrarySidebar } = await import(
	'../../src/lib/components/collections/CollectionLibrarySidebar.svelte'
);
const { autoOrganizeCounts } = await import('../../src/lib/stores/autoOrganizeCounts');
const { resetLayerStackForTests } = await import('../../src/lib/actions/layerStack');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');

const collections = [
	{ id: 'a', parent_id: null, name: 'Avatars', item_count: 5 },
	{ id: 'a1', parent_id: 'a', name: 'Anime', item_count: 2 },
	{ id: 'b', parent_id: null, name: 'Pets', item_count: 1 }
];

function view(id: string, label: string, active = false) {
	return { id, icon: 'folder', label, active, count: 9, onSelect: vi.fn() };
}

function treeActions(overrides: Record<string, unknown> = {}) {
	return {
		onSelect: vi.fn(),
		onRename: vi.fn().mockResolvedValue({ success: true }),
		onCreate: vi.fn().mockResolvedValue({ success: true }),
		onDelete: vi.fn().mockResolvedValue({ success: true }),
		onMove: vi.fn().mockResolvedValue({ success: true }),
		onBulkMove: vi.fn().mockResolvedValue({ success: true, moved: 1, failed: 0, errors: [] }),
		...overrides
	};
}

function mountSidebar(overrides: Record<string, unknown> = {}) {
	const props = {
		storageKey: 'test-sidebar',
		label: 'History',
		collections,
		activeId: undefined,
		allView: view('all', 'All generations', true),
		favoritesView: view('favorites', 'Favorites'),
		unsortedView: view('unsorted', 'Unsorted'),
		treeActions: treeActions(),
		onCreateRoot: vi.fn().mockResolvedValue({ success: true }),
		...overrides
	};
	const { target } = mountComponent(CollectionLibrarySidebar as never, props);
	return { props, target };
}

const viewLabels = (target: HTMLElement) =>
	Array.from(target.querySelectorAll('[aria-label="History views"] [role="option"]')).map(
		(el) => el.querySelector('span.truncate')?.textContent
	);

const treeLabels = (target: HTMLElement) =>
	Array.from(target.querySelectorAll('[role="treeitem"]')).map((el) => el.querySelector('span.truncate')?.textContent);

function button(root: ParentNode, label: string) {
	return root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
}

function menuItem(text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]')).find(
		(el) => el.textContent?.trim() === text
	)!;
}

function search(target: HTMLElement, value: string) {
	const input = target.querySelector<HTMLInputElement>('input[aria-label="Find a collection"]')!;
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	return input;
}

async function openOptions(target: HTMLElement, item: string) {
	button(target, 'Collection options').click();
	await settle();
	menuItem(item).click();
	await settle();
}

function dragEvent(type: string) {
	const event = new Event(type, { bubbles: true, cancelable: true });
	(event as unknown as { dataTransfer: unknown }).dataTransfer = { setData: vi.fn(), effectAllowed: '' };
	return event;
}

const draggableRow = (target: HTMLElement, name: string) =>
	Array.from(target.querySelectorAll<HTMLElement>('[draggable="true"]')).find((el) =>
		el.textContent?.includes(name)
	)!;

beforeEach(() => {
	localStorage.clear();
	resetLayerStackForTests();
	getMock.mockReset();
	getMock.mockRejectedValue({ response: { status: 404 } });
	confirmMock.mockReset();
	autoOrganizeCounts.reset();
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('fixed views', () => {
	it('lists All, Favorites and Unsorted outside the collection tree', async () => {
		const { target } = mountSidebar();
		await settle();
		expect(viewLabels(target)).toEqual(['All generations', 'Favorites', 'Unsorted']);
		expect(treeLabels(target)).toEqual(['Avatars', 'Pets']);
	});

	it('stays visible when every collection is collapsed', async () => {
		const { target } = mountSidebar();
		await settle();
		await openOptions(target, 'Expand all');
		expect(treeLabels(target)).toEqual(['Avatars', 'Anime', 'Pets']);
		await openOptions(target, 'Collapse all');
		expect(treeLabels(target)).toEqual(['Avatars', 'Pets']);
		expect(viewLabels(target)).toEqual(['All generations', 'Favorites', 'Unsorted']);
	});

	it('selects Unsorted and marks it selected when active', async () => {
		const unsortedView = view('unsorted', 'Unsorted', true);
		const { target } = mountSidebar({ unsortedView, allView: view('all', 'All generations', false) });
		await settle();
		const rows = Array.from(target.querySelectorAll<HTMLElement>('[role="option"]'));
		const unsorted = rows.find((r) => r.textContent?.includes('Unsorted'))!;
		expect(unsorted.getAttribute('aria-selected')).toBe('true');
		unsorted.click();
		expect(unsortedView.onSelect).toHaveBeenCalledTimes(1);
	});

	it('omits Favorites when the page has none', async () => {
		const { target } = mountSidebar({ favoritesView: undefined });
		await settle();
		expect(viewLabels(target)).toEqual(['All generations', 'Unsorted']);
	});

	it('sits its page-name header on the app header height with the same bottom border', async () => {
		const { target } = mountSidebar();
		await settle();
		const title = Array.from(target.querySelectorAll('span')).find((el) => el.textContent?.trim() === 'History')!;
		const header = title.parentElement as HTMLElement;
		const classes = header.className.split(/\s+/);
		expect(classes).toEqual(expect.arrayContaining(['min-h-header', 'border-b', 'border-line']));
		expect(title.className.split(/\s+/)).toEqual(expect.arrayContaining(['text-md', 'font-semibold']));
	});

	it('labels its sections without adding headings to the page outline', async () => {
		const { target } = mountSidebar();
		await settle();
		expect(target.querySelector('h1, h2, h3, h4, h5, h6')).toBeNull();
		expect(target.textContent).toContain('Collections');
	});

	it('no longer carries a Direct only switch', async () => {
		const { target } = mountSidebar({ activeId: 'a' });
		await settle();
		expect(target.querySelector('[role="switch"]')).toBeNull();
	});
});

describe('collection search', () => {
	it('reveals a nested match with its ancestors and hides unrelated branches', async () => {
		const { target } = mountSidebar();
		await settle();
		expect(treeLabels(target)).toEqual(['Avatars', 'Pets']);
		search(target, 'ani');
		await settle();
		expect(treeLabels(target)).toEqual(['Avatars', 'Anime']);
	});

	it('restores the expansion the tree had before the search', async () => {
		const { target } = mountSidebar();
		await settle();
		search(target, 'ani');
		await settle();
		button(target, 'Clear collection search').click();
		await settle();
		expect(treeLabels(target)).toEqual(['Avatars', 'Pets']);
		expect(localStorage.getItem('test-sidebar')).toBeNull();
	});

	it('clears on Escape without letting the key close a surrounding sheet', async () => {
		const { target } = mountSidebar();
		await settle();
		const input = search(target, 'ani');
		await settle();
		const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
		input.dispatchEvent(escape);
		await settle();
		expect(escape.defaultPrevented).toBe(true);
		expect(input.value).toBe('');
		expect(treeLabels(target)).toEqual(['Avatars', 'Pets']);
	});

	it('offers to create the searched name when nothing matches', async () => {
		const { target, props } = mountSidebar();
		await settle();
		search(target, 'Storyboards');
		await settle();
		expect(target.textContent).toContain('No matching collections');
		const create = Array.from(target.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Create “Storyboards”'
		)!;
		create.click();
		await settle();
		const field = document.querySelector<HTMLInputElement>('[role="dialog"] input')!;
		expect(field.value).toBe('Storyboards');
		const save = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find((b) =>
			b.textContent?.includes('Save')
		)!;
		save.click();
		await settle();
		expect(props.onCreateRoot).toHaveBeenCalledWith('Storyboards');
	});
});

describe('collection actions', () => {
	it('gives only collections an actions menu', async () => {
		const { target } = mountSidebar();
		await settle();
		const triggers = Array.from(target.querySelectorAll('button[aria-label$=" actions"]')).map((b) =>
			b.getAttribute('aria-label')
		);
		expect(triggers).toEqual(['Avatars actions', 'Pets actions']);
		for (const row of Array.from(target.querySelectorAll('[role="option"]'))) {
			expect(row.querySelector('button[aria-label$=" actions"]')).toBeNull();
		}
	});

	it('opens a menu titled with the full name, with an icon on every action and a red delete', async () => {
		const { target } = mountSidebar();
		await settle();
		button(target, 'Avatars actions').click();
		await settle();
		const menu = document.querySelector<HTMLElement>('[role="menu"]')!;
		expect(menu.querySelector('[data-testid="collection-menu-title"]')?.textContent?.trim()).toBe('Avatars');
		const items = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]'));
		expect(items.map((i) => i.textContent?.trim())).toEqual([
			'New subcollection',
			'Rename…',
			'Move to…',
			'Delete collection…'
		]);
		for (const item of items) expect(item.querySelector('svg')).not.toBeNull();
		expect(items[3].className).toContain('text-danger');
	});

	it('deletes only after the confirmation is accepted', async () => {
		const { target, props } = mountSidebar();
		await settle();
		confirmMock.mockResolvedValueOnce(false);
		button(target, 'Avatars actions').click();
		await settle();
		menuItem('Delete collection…').click();
		await settle();
		expect(props.treeActions.onDelete).not.toHaveBeenCalled();
		confirmMock.mockResolvedValueOnce(true);
		button(target, 'Avatars actions').click();
		await settle();
		menuItem('Delete collection…').click();
		await settle();
		expect(confirmMock).toHaveBeenLastCalledWith(expect.objectContaining({ variant: 'danger' }));
		expect(props.treeActions.onDelete).toHaveBeenCalledWith('a', new Set(['a', 'a1']));
	});
});

describe('collection name dialog', () => {
	async function chooseFromRowMenu(target: HTMLElement, row: string, item: string) {
		button(target, `${row} actions`).click();
		await settle();
		menuItem(item).click();
		await settle();
		return document.querySelector<HTMLInputElement>('[role="dialog"] input[id^="collection-name-"]')!;
	}

	function type(input: HTMLInputElement, value: string) {
		input.value = value;
		input.dispatchEvent(new Event('input', { bubbles: true }));
	}

	async function save() {
		Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button'))
			.find((b) => b.textContent?.includes('Save'))!
			.click();
		await settle();
	}

	it('renames with the trimmed name', async () => {
		const { target, props } = mountSidebar();
		await settle();
		const input = await chooseFromRowMenu(target, 'Avatars', 'Rename…');
		expect(input.value).toBe('Avatars');
		type(input, '  New name  ');
		await settle();
		await save();
		expect(props.treeActions.onRename).toHaveBeenCalledWith('a', 'New name');
		expect(document.querySelector('[role="dialog"] input[id^="collection-name-"]')).toBeNull();
	});

	it('creates a subcollection under the row it was opened from', async () => {
		const { target, props } = mountSidebar();
		await settle();
		const input = await chooseFromRowMenu(target, 'Pets', 'New subcollection');
		expect(input.value).toBe('');
		type(input, 'Cats');
		await settle();
		await save();
		expect(props.treeActions.onCreate).toHaveBeenCalledWith('Cats', 'b');
	});

	it('stays open and shows the reason when saving fails', async () => {
		const actions = treeActions({ onRename: vi.fn().mockResolvedValue({ success: false, message: 'Taken' }) });
		const { target } = mountSidebar({ treeActions: actions });
		await settle();
		const input = await chooseFromRowMenu(target, 'Avatars', 'Rename…');
		type(input, 'Pets');
		await settle();
		await save();
		expect(actions.onRename).toHaveBeenCalledWith('a', 'Pets');
		expect(document.querySelector('[role="dialog"] input[id^="collection-name-"]')).not.toBeNull();
		expect(document.querySelector('[role="dialog"] [role="alert"]')?.textContent?.trim()).toBe('Taken');
	});
});

describe('drag and drop', () => {
	it('makes collections draggable and the fixed views not', async () => {
		const { target } = mountSidebar();
		await settle();
		const draggable = Array.from(target.querySelectorAll('[draggable="true"]'));
		expect(draggable.map((el) => el.textContent?.match(/Avatars|Pets/)?.[0])).toEqual(['Avatars', 'Pets']);
	});

	it('moves a nested collection to the top level when dropped on All', async () => {
		const { target, props } = mountSidebar();
		await settle();
		button(target, 'Expand Avatars').click();
		await settle();
		const all = target.querySelector<HTMLElement>('[aria-label="History views"] [role="none"]')!;
		draggableRow(target, 'Anime').dispatchEvent(dragEvent('dragstart'));
		all.dispatchEvent(dragEvent('dragover'));
		all.dispatchEvent(dragEvent('drop'));
		await settle();
		expect(props.treeActions.onMove).toHaveBeenCalledWith('a1', null);
	});

	it('ignores a drop of a top-level collection on All', async () => {
		const { target, props } = mountSidebar();
		await settle();
		const all = target.querySelector<HTMLElement>('[aria-label="History views"] [role="none"]')!;
		draggableRow(target, 'Pets').dispatchEvent(dragEvent('dragstart'));
		all.dispatchEvent(dragEvent('dragover'));
		all.dispatchEvent(dragEvent('drop'));
		await settle();
		expect(props.treeActions.onMove).not.toHaveBeenCalled();
	});

	it('highlights only the collection under the pointer and reparents onto it', async () => {
		const { target, props } = mountSidebar();
		await settle();
		button(target, 'Expand Avatars').click();
		await settle();
		const pets = draggableRow(target, 'Pets');
		draggableRow(target, 'Anime').dispatchEvent(dragEvent('dragstart'));
		pets.dispatchEvent(dragEvent('dragover'));
		await settle();
		expect(pets.querySelector('[role="treeitem"]')!.className).toContain('ring-signal');
		expect(target.querySelector('[role="tree"] > [role="none"]')!.className).not.toContain('bg-signal');
		pets.dispatchEvent(dragEvent('drop'));
		await settle();
		expect(props.treeActions.onMove).toHaveBeenCalledTimes(1);
		expect(props.treeActions.onMove).toHaveBeenCalledWith('a1', 'b');
	});
});

describe('selection', () => {
	it('bulk deletes only the top-level selected collections after confirmation', async () => {
		const { target, props } = mountSidebar();
		await settle();
		await openOptions(target, 'Expand all');
		await openOptions(target, 'Select collections');
		target.querySelector<HTMLInputElement>('input[aria-label="Select Avatars"]')!.click();
		target.querySelector<HTMLInputElement>('input[aria-label="Select Anime"]')!.click();
		await settle();
		expect(target.querySelector('[data-testid="collection-selected-count"]')?.textContent).toBe('2 selected');
		confirmMock.mockResolvedValueOnce(true);
		Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Delete…')!.click();
		await settle();
		expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Delete collection?' }));
		expect(props.treeActions.onDelete).toHaveBeenCalledTimes(1);
		expect(props.treeActions.onDelete).toHaveBeenCalledWith('a', new Set(['a', 'a1']));
	});

	it('moves one by one when the page has no bulk move', async () => {
		const actions = treeActions({ onBulkMove: undefined });
		const { target } = mountSidebar({ treeActions: actions });
		await settle();
		await openOptions(target, 'Select collections');
		target.querySelector<HTMLInputElement>('input[aria-label="Select Pets"]')!.click();
		await settle();
		Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Move…')!.click();
		await settle();
		const select = document.querySelector<HTMLSelectElement>('[role="dialog"] select')!;
		expect(Array.from(select.options).map((o) => o.textContent?.trim())).toEqual(['Top level', 'Avatars', 'Anime']);
		select.value = 'a';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		Array.from(document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button'))
			.find((b) => b.textContent?.includes('Move'))!
			.click();
		await settle();
		expect(actions.onMove).toHaveBeenCalledWith('b', 'a');
	});
});

describe('auto-organize footer', () => {
	it('shows the active count and links to the subject page', async () => {
		getMock.mockResolvedValue({
			data: { subjects: { generation: { active: 3, needs_attention: 0 } } }
		});
		const { target } = mountSidebar({ autoOrganizeSubject: 'generations' });
		await settle();
		const link = target.querySelector<HTMLAnchorElement>('[data-testid="auto-organize-link"]')!;
		expect(link.getAttribute('href')).toBe('/auto-organize?subject=generations');
		expect(link.textContent).toContain('Auto-organize');
		expect(link.textContent).toContain('3 active');
		expect(getMock).toHaveBeenCalledWith('/api/organize/summary');
	});

	it('keeps the link but hides the count when the summary 404s', async () => {
		const { target } = mountSidebar({ autoOrganizeSubject: 'models' });
		await settle();
		const link = target.querySelector('[data-testid="auto-organize-link"]')!;
		expect(link.getAttribute('href')).toBe('/auto-organize?subject=models');
		expect(link.textContent).not.toContain('active');
	});

	it('is absent for pages without rules', async () => {
		const { target } = mountSidebar();
		await settle();
		expect(target.querySelector('[data-testid="auto-organize-link"]')).toBeNull();
		expect(getMock).not.toHaveBeenCalled();
	});
});
