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

function rows() {
	return Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
}

function rowFor(label: string) {
	return rows().find((r) => r.textContent?.includes(label));
}

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('PromptPickerBrowseModal — phrasebook value list previews', () => {
	it('renders a thumbnail image for values with a preview and a plain glyph for values without one', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		expect(rows().length).toBe(3);

		const golden = rowFor('golden hour');
		const blue = rowFor('blue hour');

		expect(golden?.querySelector('.picker-vthumb img')).not.toBeNull();
		expect(blue?.querySelector('.picker-vthumb img')).toBeNull();
		expect(blue?.querySelector('.picker-vthumb')?.classList.contains('clickable')).toBe(false);
	});

	it('shows the Space / → preview hint in the list header only when a value has a preview', () => {
		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'warm rim light', sort_order: 0, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v1'
		});

		expect(document.querySelector('.picker-values-head')).toBeNull();
		expect(document.querySelector('.picker-vrow')).not.toBeNull();
	});

	it('shows the Space / → preview hint when at least one value has a preview', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		const hint = document.querySelector('.picker-values-head');
		expect(hint).not.toBeNull();
		expect(hint?.textContent).toContain('preview');
	});

	it("opens the preview viewer by clicking a row's thumbnail", () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		rowFor('golden hour')!.querySelector<HTMLElement>('.picker-vthumb')!.dispatchEvent(
			new MouseEvent('click', { bubbles: true })
		);
		flushSync();

		expect(document.querySelector('.picker-preview-pane')).not.toBeNull();
		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('golden hour');
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 2');
	});

	it('opens the preview viewer with Space when a row with a preview is selected', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();

		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('golden hour');
	});

	it('opens the preview viewer with → when a row with a preview is selected', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v3' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();

		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('hard noon');
	});

	it('Space/→ do nothing when the selected row has no preview', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v2' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pane')).toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pane')).toBeNull();
	});

	it('↑ / ↓ move the list selection', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		flushSync();
		expect(rowFor('blue hour')?.classList.contains('sel')).toBe(true);

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		flushSync();
		expect(rowFor('hard noon')?.classList.contains('sel')).toBe(true);

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
		flushSync();
		expect(rowFor('blue hour')?.classList.contains('sel')).toBe(true);
	});

	it('← / → move only through values with previews, updating position and stopping at the ends', () => {
		mountModal({ values: valuesWithMixedPreviews, initialSelectedId: 'v1' });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 2');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('1 / 2');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('2 / 2');
		expect(document.querySelector('.picker-preview-pane')?.textContent).toContain('hard noon');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.querySelector('.picker-preview-pos')?.textContent?.trim()).toBe('2 / 2');
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

		expect(rowFor('hard noon')?.classList.contains('sel')).toBe(true);
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
