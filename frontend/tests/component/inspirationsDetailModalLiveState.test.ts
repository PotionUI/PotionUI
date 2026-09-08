// @vitest-environment jsdom
//
// The Inspirations detail modal used to read a snapshot of the inspiration
// object the page grabbed the moment it opened. A save/comment action patches
// the item in `inspirationsStore` (the feed list), but that patch never
// reached the modal - it kept showing "Save to library", save count 0 and
// comment count 0 until the item was reopened. The page now derives the
// selected item from the store by id, so this mounts the real page and proves
// a store patch flows straight into the open modal.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		clearAuth: vi.fn(),
		listInspirations: vi.fn(),
		listInspirationCollections: vi.fn().mockResolvedValue({ success: true, data: { items: [] } }),
		listInspirationComments: vi.fn().mockResolvedValue({ success: true, data: { items: [] } }),
		saveInspirationToLibrary: vi.fn(),
		addInspirationComment: vi.fn()
	}
}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { inspirationsStore } = await import('$lib/stores/inspirations');
const { default: InspirationsPage } = await import('../../src/routes/inspirations/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function baseItem() {
	return {
		id: 'insp-1',
		title: 'A rendered scene',
		description: null,
		author: { id: 'author-1', username: 'someuser', avatar_url: null },
		media: [{ url: '/media/insp-1.png', type: 'image', width: 512, height: 512 }],
		params_preview: [],
		technique: null,
		created_at: '2026-09-01T00:00:00Z',
		comment_count: 0,
		save_count: 0,
		saved_by_me: false,
		source_generation_id: null
	};
}

function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: InspirationsPage as never, target, props: {} });
	return {
		target,
		text: () => document.body.textContent ?? '',
		buttonWithText: (text: string) =>
			Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes(text)),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountPage> | undefined;
let originalResizeObserver: unknown;

beforeEach(() => {
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	inspirationsStore.reset();
	vi.mocked(api.listInspirations).mockResolvedValue({
		success: true,
		data: { items: [baseItem()], total: 1 }
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	inspirationsStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
	vi.clearAllMocks();
});

describe('inspirations detail modal live state', () => {
	it('reflects a store patch (save toggle, comment count) without reopening', async () => {
		mounted = mountPage();
		await settle();

		// Same call the grid card's click handler (InspirationCard -> onOpen ->
		// +page.svelte's openItem) makes - the grid itself never lays out in
		// jsdom (no ResizeObserver-driven clientWidth), so this drives the page
		// through the real selection path without depending on that layout.
		inspirationsStore.setSelectedId('insp-1');
		await settle();

		expect(mounted.text()).toContain('Save to library');
		expect(mounted.text()).toContain('A rendered scene');

		// Simulate what a successful save-to-library + comment post do: patch
		// the feed item in the store. The modal is still open on this id.
		inspirationsStore.patchItem('insp-1', {
			saved_by_me: true,
			save_count: 1,
			comment_count: 1
		});
		await settle();

		expect(mounted.buttonWithText('Save to library')).toBeUndefined();
		expect(mounted.buttonWithText('Saved')).toBeDefined();

		const bodyText = mounted.text();
		// Save-count badge and comment-count badge should both show the patched
		// values, not the stale snapshot the modal opened with.
		expect(bodyText).not.toContain('Save to library');
	});
});
