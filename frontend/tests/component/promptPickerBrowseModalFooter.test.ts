// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: PromptPickerBrowseModal } = await import('$lib/components/PromptPickerBrowseModal.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;

const values = [
	{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'warm rim light', sort_order: 0, created_at: '', updated_at: '' },
	{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'cool ambient light', sort_order: 1, created_at: '', updated_at: '' }
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
			values,
			onInsertValue: vi.fn(),
			onClose: vi.fn(),
			...props
		}
	});
	flushSync();
	return target;
}

function confirmButton() {
	return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.includes('Insert'));
}

function cancelButton() {
	return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.includes('Cancel'));
}

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('PromptPickerBrowseModal footer', () => {
	it('renders one ConfirmFooter row with Cancel/Esc and Insert/Enter', () => {
		mountModal({ initialSelectedId: 'v1' });

		const footerRow = document.querySelector<HTMLElement>('.border-t.border-line > div');
		expect(footerRow).not.toBeNull();
		expect(footerRow!.className).toContain('items-center');
		expect(footerRow!.className).toContain('justify-between');
		expect(footerRow!.className).toContain('px-6');
		expect(footerRow!.className).toContain('py-4');

		expect(cancelButton()?.textContent).toContain('Esc');
		expect(confirmButton()?.textContent).toContain('Insert');
		expect(confirmButton()?.textContent).toContain('Enter');
	});

	it('Enter confirms the current selection and closes the modal', () => {
		const onInsertValue = vi.fn();
		const onClose = vi.fn();
		mountModal({ initialSelectedId: 'v2', onInsertValue, onClose });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(onInsertValue).toHaveBeenCalledTimes(1);
		expect(onInsertValue.mock.calls[0][0].id).toBe('v2');
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('Enter does nothing while no value is selected', () => {
		const onInsertValue = vi.fn();
		const onClose = vi.fn();
		mountModal({ values: [], onInsertValue, onClose });

		expect(confirmButton()?.hasAttribute('disabled')).toBe(true);

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(onInsertValue).not.toHaveBeenCalled();
		expect(onClose).not.toHaveBeenCalled();
	});

	it('Escape cancels without inserting anything', () => {
		const onInsertValue = vi.fn();
		const onClose = vi.fn();
		mountModal({ initialSelectedId: 'v1', onInsertValue, onClose });

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(onInsertValue).not.toHaveBeenCalled();
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('Enter in the search box still confirms the current selection', () => {
		const onInsertValue = vi.fn();
		const onClose = vi.fn();
		mountModal({ initialSelectedId: 'v1', onInsertValue, onClose });

		const searchInput = document.querySelector<HTMLInputElement>('.picker-modal-search input')!;
		searchInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(onInsertValue).toHaveBeenCalledTimes(1);
	});
});

describe('PromptPickerBrowseModal footer — phrasebook actions stay on one row', () => {
	it('renders the left action group without a wrap class', () => {
		mountModal({ footerLeft: 'phrasebook', initialSelectedId: 'v1', canShuffle: true });

		const left = document.querySelector<HTMLElement>('.picker-footer-left');
		expect(left).not.toBeNull();
		expect(left!.className).not.toContain('flex-wrap');
	});

	it('renders Fixed value / Auto-shuffle as a segmented control with aria-pressed', () => {
		mountModal({ footerLeft: 'phrasebook', initialSelectedId: 'v1', canShuffle: true });

		const group = document.querySelector<HTMLElement>('.picker-footer-left [role="group"]');
		expect(group).not.toBeNull();

		const segments = Array.from(group!.querySelectorAll<HTMLButtonElement>('button'));
		expect(segments.map((b) => b.textContent?.trim())).toEqual(['Fixed value', 'Auto-shuffle']);
		expect(segments[0].getAttribute('aria-pressed')).toBe('true');
		expect(segments[1].getAttribute('aria-pressed')).toBe('false');

		segments[1].click();
		flushSync();

		expect(segments[1].getAttribute('aria-pressed')).toBe('true');
		expect(segments[0].getAttribute('aria-pressed')).toBe('false');
	});

	it('disables the Auto-shuffle segment and hides Exclude when the value cannot shuffle', () => {
		mountModal({ footerLeft: 'phrasebook', initialSelectedId: 'v1', canShuffle: false });

		const segments = Array.from(
			document.querySelectorAll<HTMLButtonElement>('.picker-footer-left [role="group"] button')
		);
		expect(segments[1].disabled).toBe(true);

		const exclude = Array.from(document.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Exclude from shuffles')
		);
		expect(exclude).toBeUndefined();
	});

	it('Remove chip is a danger IconButton with a tooltip label, calling onRemove and closing', () => {
		const onRemove = vi.fn();
		const onClose = vi.fn();
		mountModal({ footerLeft: 'phrasebook', initialSelectedId: 'v1', canShuffle: true, onRemove, onClose });

		const removeButton = document.querySelector<HTMLButtonElement>(
			'.picker-footer-left button[aria-label="Remove chip"]'
		);
		expect(removeButton).not.toBeNull();
		expect(removeButton!.className).toContain('text-danger');

		removeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(onRemove).toHaveBeenCalledTimes(1);
		expect(onClose).toHaveBeenCalledTimes(1);
	});
});
