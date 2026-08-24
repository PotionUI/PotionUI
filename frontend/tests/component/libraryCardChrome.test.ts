// @vitest-environment jsdom
//
// The library card shares the generation card's width-bucketed chrome
// (generationCardChrome.ts) but shows an upload's own affordances. The wiring
// worth pinning down is what a given tile width actually renders - the pure
// bucket table can't tell you whether the card asks it at all - and that the
// name shown is never the on-disk uuid.
import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: LibraryCard } = await import(
	'../../src/routes/library/components/LibraryCard.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const ITEM = {
	id: 'item-1',
	filename: '0d3f8e2a-1111-2222-3333-444455556666.png',
	original_filename: 'sunset.png',
	media_type: 'image',
	url: '/api/media/uploads/0d3f8e2a-1111-2222-3333-444455556666.png',
	width: 1024,
	height: 512,
	size: 2048,
	created_at: '2026-08-13T10:00:00Z',
	tags: []
};

function mountCard(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LibraryCard as never,
		target,
		props: { item: ITEM, tile: { width: 320, height: 160 }, ...props }
	});
	return {
		target,
		component,
		labels: () =>
			Array.from(target.querySelectorAll('button')).map((b) => b.getAttribute('aria-label') ?? ''),
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('library card', () => {
	it('shows the original filename, never the on-disk uuid', () => {
		mounted = mountCard();

		expect(mounted.text()).toContain('sunset.png');
		expect(mounted.text()).not.toContain('0d3f8e2a');
	});

	it('renders the item resolution', () => {
		mounted = mountCard();

		expect(mounted.text()).toContain('1024×512');
	});

	it('offers view, download and delete on a wide tile', () => {
		mounted = mountCard({ tile: { width: 320, height: 160 } });

		const labels = mounted.labels();
		expect(labels).toContain('Open library item');
		expect(labels).toContain('Download');
		expect(labels).toContain('Delete library item');
	});

	// An upload has no favorite or rating state - the generation card's two
	// smallest buckets keep favorite, this one must not invent it.
	it('never offers a favorite action', () => {
		mounted = mountCard();

		expect(mounted.labels().join(' ')).not.toMatch(/favorit/i);
	});

	it('drops download before delete on a mid-width tile', () => {
		mounted = mountCard({ tile: { width: 150, height: 150 } });

		const labels = mounted.labels();
		expect(labels).toContain('Delete library item');
		expect(labels).toContain('Open library item');
		expect(labels).not.toContain('Download');
	});

	it('keeps delete on the narrowest tile', () => {
		mounted = mountCard({ tile: { width: 100, height: 100 } });

		const labels = mounted.labels();
		expect(labels).toContain('Delete library item');
		expect(labels).not.toContain('Download');
	});

	it('emits delete with the item rather than deleting anything itself', () => {
		mounted = mountCard();
		const onDelete = vi.fn();
		mounted.component.$on('delete', (e: CustomEvent) => onDelete(e.detail));

		const button = Array.from(mounted.target.querySelectorAll('button')).find(
			(b) => b.getAttribute('aria-label') === 'Delete library item'
		);
		button!.click();

		expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 'item-1' }));
	});

	it('hides the per-item actions in selection mode', () => {
		mounted = mountCard({ selectable: true });

		expect(mounted.labels()).not.toContain('Delete library item');
	});
});
