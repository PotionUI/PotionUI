// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const getMock = vi.fn();

vi.mock('../../src/lib/services/api/index', () => ({
	api: { getClient: () => ({ get: getMock }) }
}));

const { default: CollectionLibrarySidebar } = await import(
	'../../src/lib/components/collections/CollectionLibrarySidebar.svelte'
);
const { autoOrganizeCounts } = await import('../../src/lib/stores/autoOrganizeCounts');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

const collections = [
	{ id: 'a', parent_id: null, name: 'Avatars', item_count: 5 },
	{ id: 'a1', parent_id: 'a', name: 'Anime', item_count: 2 },
	{ id: 'b', parent_id: null, name: 'Pets', item_count: 1 }
];

function view(id: string, label: string, active = false) {
	return { id, icon: 'folder', label, active, count: 9, onSelect: vi.fn() };
}

function mountSidebar(overrides: Record<string, unknown> = {}) {
	const props = {
		storageKey: 'test-sidebar',
		collections,
		activeId: undefined,
		allView: view('all', 'All generations', true),
		favoritesView: view('favorites', 'Favorites'),
		unsortedView: view('unsorted', 'Unsorted'),
		treeActions: {
			onSelect: vi.fn(),
			onRename: vi.fn(),
			onCreate: vi.fn(),
			onDelete: vi.fn(),
			onMove: vi.fn().mockResolvedValue({ success: true }),
			onBulkMove: vi.fn()
		},
		onCreateRoot: vi.fn(),
		embedded: true,
		...overrides
	};
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: CollectionLibrarySidebar as never,
		target,
		props
	});
	return {
		props,
		target,
		labels: () =>
			Array.from(target.querySelectorAll('[role="treeitem"]')).map(
				(el) => el.querySelector('span.truncate')?.textContent
			),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	await tick();
	await Promise.resolve();
	await tick();
}

let mounted: ReturnType<typeof mountSidebar> | null = null;

beforeEach(() => {
	getMock.mockReset();
	getMock.mockRejectedValue({ response: { status: 404 } });
	autoOrganizeCounts.reset();
});

afterEach(() => {
	mounted?.destroy();
	mounted = null;
});

describe('fixed rows', () => {
	it('renders All, Favorites and Unsorted before the user collections', async () => {
		mounted = mountSidebar();
		await settle();
		expect(mounted.labels()).toEqual([
			'All generations',
			'Favorites',
			'Unsorted',
			'Avatars',
			'Pets'
		]);
	});

	it('gives only user collections an actions menu', async () => {
		mounted = mountSidebar();
		await settle();
		const menus = mounted.target.querySelectorAll('[aria-label="Folder actions"]');
		expect(menus.length).toBe(2);
		const fixed = Array.from(mounted.target.querySelectorAll('[role="treeitem"]')).slice(0, 3);
		for (const row of fixed) {
			expect(row.querySelector('[aria-label="Folder actions"]')).toBeNull();
		}
	});

	it('makes collections draggable and the fixed rows not', async () => {
		mounted = mountSidebar();
		await settle();
		const draggable = mounted.target.querySelectorAll('[draggable="true"]');
		expect(draggable.length).toBe(2);
		for (const el of Array.from(draggable)) {
			expect(el.textContent).toMatch(/Avatars|Pets/);
		}
	});

	it('selects Unsorted and marks it selected when active', async () => {
		const unsortedView = view('unsorted', 'Unsorted', true);
		mounted = mountSidebar({ unsortedView, allView: view('all', 'All generations', false) });
		await settle();
		const rows = Array.from(mounted.target.querySelectorAll<HTMLElement>('[role="treeitem"]'));
		const unsorted = rows.find((r) => r.textContent?.includes('Unsorted'))!;
		expect(unsorted.getAttribute('aria-selected')).toBe('true');
		unsorted.click();
		expect(unsortedView.onSelect).toHaveBeenCalledTimes(1);
	});

	it('omits Favorites when the page has none', async () => {
		mounted = mountSidebar({ favoritesView: undefined });
		await settle();
		expect(mounted.labels().slice(0, 2)).toEqual(['All generations', 'Unsorted']);
	});
});

describe('tree semantics', () => {
	it('nests the collections inside a group under the All treeitem', async () => {
		mounted = mountSidebar();
		await settle();
		const trees = mounted.target.querySelectorAll('[role="tree"]');
		expect(trees.length).toBe(1);
		const group = mounted.target.querySelector('[role="group"]')!;
		expect(group.textContent).toContain('Avatars');
		expect(group.textContent).not.toContain('All generations');
		const all = mounted.target.querySelector('[role="treeitem"]')!;
		expect(all.getAttribute('aria-expanded')).toBe('true');
	});
});

describe('drag and drop', () => {
	function dragEvent(type: string) {
		const event = new Event(type, { bubbles: true, cancelable: true });
		(event as unknown as { dataTransfer: unknown }).dataTransfer = {
			setData: vi.fn(),
			effectAllowed: ''
		};
		return event;
	}

	it('moves a nested collection to the root when dropped on All', async () => {
		mounted = mountSidebar();
		await settle();
		const nested = Array.from(
			mounted.target.querySelectorAll<HTMLElement>('[draggable="true"]')
		).find((el) => el.textContent?.includes('Avatars'))!;
		nested.querySelector<HTMLElement>('[aria-label="Collapse"], [aria-label="Expand"]')?.click();
		await settle();
		const child = Array.from(
			mounted.target.querySelectorAll<HTMLElement>('[draggable="true"]')
		).find((el) => el.textContent?.includes('Anime'))!;
		const allWrapper = mounted.target.querySelector('[role="none"]') as HTMLElement;
		child.dispatchEvent(dragEvent('dragstart'));
		allWrapper.dispatchEvent(dragEvent('dragover'));
		allWrapper.dispatchEvent(dragEvent('drop'));
		await settle();
		expect(mounted.props.treeActions.onMove).toHaveBeenCalledWith('a1', null);
	});

	it('ignores a drop of a root collection on All', async () => {
		mounted = mountSidebar();
		await settle();
		const pets = Array.from(
			mounted.target.querySelectorAll<HTMLElement>('[draggable="true"]')
		).find((el) => el.textContent?.includes('Pets'))!;
		const allWrapper = mounted.target.querySelector('[role="none"]') as HTMLElement;
		pets.dispatchEvent(dragEvent('dragstart'));
		allWrapper.dispatchEvent(dragEvent('dragover'));
		allWrapper.dispatchEvent(dragEvent('drop'));
		await settle();
		expect(mounted.props.treeActions.onMove).not.toHaveBeenCalled();
	});
});

describe('auto-organize link', () => {
	it('shows the active count and links to the subject page', async () => {
		getMock.mockResolvedValue({
			data: { subjects: { generation: { active: 3, needs_attention: 0 } } }
		});
		mounted = mountSidebar({ autoOrganizeSubject: 'generations' });
		await settle();
		await settle();
		const link = mounted.target.querySelector<HTMLAnchorElement>(
			'[data-testid="auto-organize-link"]'
		)!;
		expect(link.getAttribute('href')).toBe('/auto-organize?subject=generations');
		expect(link.textContent).toContain('Auto-organize');
		expect(link.textContent).toContain('3 active');
		expect(getMock).toHaveBeenCalledWith('/api/organize/summary');
	});

	it('keeps the link but hides the count when the summary 404s', async () => {
		mounted = mountSidebar({ autoOrganizeSubject: 'models' });
		await settle();
		await settle();
		const link = mounted.target.querySelector('[data-testid="auto-organize-link"]')!;
		expect(link.getAttribute('href')).toBe('/auto-organize?subject=models');
		expect(link.textContent).not.toContain('active');
	});

	it('is absent for pages without rules', async () => {
		mounted = mountSidebar();
		await settle();
		expect(mounted.target.querySelector('[data-testid="auto-organize-link"]')).toBeNull();
		expect(getMock).not.toHaveBeenCalled();
	});
});

describe('direct only toggle', () => {
	it('is hidden until a collection is selected', async () => {
		mounted = mountSidebar({ onDirectOnlyChange: vi.fn() });
		await settle();
		expect(mounted.target.querySelector('[role="switch"]')).toBeNull();
	});

	it('reports the new value when flipped', async () => {
		const onDirectOnlyChange = vi.fn();
		mounted = mountSidebar({ activeId: 'a', onDirectOnlyChange });
		await settle();
		const toggle = mounted.target.querySelector<HTMLInputElement>('[role="switch"]')!;
		toggle.click();
		await settle();
		expect(onDirectOnlyChange).toHaveBeenCalledWith(true);
	});
});
