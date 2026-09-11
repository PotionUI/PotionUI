// @vitest-environment jsdom
//
// The Library page's Tools menu is built from the same scope-aware registry
// as History's (`listToolGroups`/`buildLibraryToolContext`), so what actually
// proves the wiring is: (1) the core tools scoped to both pages show up for a
// two-image library selection, and (2) a tool scoped to `history` only is
// left out of the `library` menu entirely - not just disabled. This mounts
// the real toolbar against the real libraryStore.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		addUploadsToCollection: vi.fn(),
		removeUploadsFromCollection: vi.fn(),
		addToCollection: vi.fn(),
		removeFromCollection: vi.fn(),
		listCollections: vi.fn(),
		listLibraryItems: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { registerMediaTool, unregisterMediaTools } = await import('$lib/tools/tools');
const { registerCoreHistoryTools } = await import('../../src/routes/history/tools/coreTools');
const { default: LibrarySelectionToolbar } = await import(
	'../../src/routes/library/components/LibrarySelectionToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

registerCoreHistoryTools();

// A synthetic history-only tool, standing in for a real one (core has none -
// every core tool is scoped to both pages) - this is what proves a scope
// mismatch hides a tool rather than merely disabling it.
function registerHistoryOnlyTool() {
	registerMediaTool({
		id: 'history-only-test-tool',
		label: 'History Only Tool',
		icon: 'wand',
		category: 'analyze',
		source: 'core',
		scopes: ['history'],
		applies: () => ({ enabled: true })
	});
}

function mountToolbar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LibrarySelectionToolbar as never,
		target,
		props: { onBulkDeleteClick: () => {}, onToolSelect: () => {} }
	});
	return {
		target,
		toolsButton: () =>
			Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Tools')),
		toolLabels: () =>
			Array.from(target.querySelectorAll('[data-tool]')).map((el) => el.textContent?.trim()),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountToolbar> | undefined;

beforeEach(() => {
	libraryStore.reset();
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: {
			items: [
				{ id: 'item-1', filename: 'a.png', media_type: 'image', url: '/a.png', tags: [] },
				{ id: 'item-2', filename: 'b.png', media_type: 'image', url: '/b.png', tags: [] }
			],
			total: 2,
			limit: 24,
			offset: 0
		}
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	libraryStore.reset();
	unregisterMediaTools((tool) => tool.id === 'history-only-test-tool');
	vi.clearAllMocks();
});

describe('library tools menu', () => {
	it('lists the core tools for two selected library images', async () => {
		await libraryStore.load();
		libraryStore.toggleSelect('item-1');
		libraryStore.toggleSelect('item-2');

		mounted = mountToolbar();
		await settle();
		mounted.toolsButton()!.click();
		await settle();

		const labels = mounted.toolLabels();
		expect(labels).toEqual(expect.arrayContaining(['Compare', 'Download .zip', 'Stitch']));
	});

	it('omits a tool scoped to history only, not just disables it', async () => {
		registerHistoryOnlyTool();
		await libraryStore.load();
		libraryStore.toggleSelect('item-1');
		libraryStore.toggleSelect('item-2');

		mounted = mountToolbar();
		await settle();
		mounted.toolsButton()!.click();
		await settle();

		expect(mounted.toolLabels()).not.toContain('History Only Tool');
	});
});
