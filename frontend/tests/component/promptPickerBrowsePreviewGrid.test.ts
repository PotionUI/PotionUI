// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: PromptPickerBrowseModal } = await import('$lib/components/PromptPickerBrowseModal.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;

const valuesWithMixedPreviews = [
	{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'warm rim light', sort_order: 0, created_at: '', updated_at: '', preview_file_id: 'file-1' },
	{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'cool ambient light', sort_order: 1, created_at: '', updated_at: '' },
	{ id: 'v3', category_id: 'c1', label: 'hard noon', value: 'short shadows', sort_order: 2, created_at: '', updated_at: '', preview_file_id: 'file-3' }
];

function mountModal(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PromptPickerBrowseModal as never, {
		target,
		props: {
			triggerChar: '#',
			title: 'Insert from Phrasebook',
			contextMarker: '#lighting',
			layout: 'list',
			getImageUrl: (fileId: string) => `/media/small/${fileId}`,
			onInsertValue: vi.fn(),
			onClose: vi.fn(),
			...props
		}
	});
	flushSync();
	return target;
}

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('PromptPickerBrowseModal — phrasebook preview grid', () => {
	it('renders an image card for values with a preview and a glyph tile for values without one', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		const cards = Array.from(document.querySelectorAll<HTMLElement>('.picker-pgitem'));
		expect(cards.length).toBe(3);

		const golden = cards.find((c) => c.textContent?.includes('golden hour'));
		const blue = cards.find((c) => c.textContent?.includes('blue hour'));

		expect(golden?.querySelector('img')).not.toBeNull();
		expect(blue?.querySelector('img')).toBeNull();
		expect(blue?.querySelector('.picker-pgglyph')).not.toBeNull();
	});

	it('falls back to the list layout when no value in the category has a preview', () => {
		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'warm rim light', sort_order: 0, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v1'
		});

		expect(document.querySelector('.picker-pgrid')).toBeNull();
		expect(document.querySelector('.picker-vrow')).not.toBeNull();
	});

	it('opens the preview viewer by clicking a card\'s preview button', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		document.querySelector<HTMLElement>('button[aria-label="Preview golden hour"]')!.dispatchEvent(
			new MouseEvent('click', { bubbles: true })
		);
		flushSync();

		expect(document.querySelector('.picker-preview-pane')).not.toBeNull();
		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('golden hour');
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 3');
	});

	it('opens the preview viewer with Space when a card is selected', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v2' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();

		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('blue hour');
	});

	it('← / → move through the values as a carousel, updating position and stopping at the ends', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 3');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 3');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('2 / 3');
		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('blue hour');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('3 / 3');
	});

	it('"Use this value" sets the pending selection and closes the viewer, keeping the modal open', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();

		const useButton = Array.from(document.querySelectorAll<HTMLElement>('button')).find(
			(b) => b.textContent?.trim() === 'Use this value'
		);
		useButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('.picker-preview-pane')).toBeNull();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		const selected = document.querySelector<HTMLElement>('.picker-pgitem.sel');
		expect(selected?.textContent).toContain('blue hour');
	});

	it('Esc closes the viewer only, not the whole modal', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pane')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(document.querySelector('.picker-preview-pane')).toBeNull();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
	});
});
