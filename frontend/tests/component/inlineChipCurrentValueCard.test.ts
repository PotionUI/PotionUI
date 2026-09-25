// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: PromptPickerBrowseModal } = await import('$lib/components/PromptPickerBrowseModal.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;

function mountModal(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PromptPickerBrowseModal as never, {
		target,
		props: {
			triggerChar: '#',
			title: 'Change value',
			contextMarker: '#lighting.mood',
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

describe('PromptPickerBrowseModal — current value is the selected row, not a summary card', () => {
	it('renders no separate current-value card', () => {
		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v1'
		});

		expect(document.querySelector('.pm-current')).toBeNull();
		expect(document.querySelector('.current-value-card')).toBeNull();
	});

	it('marks the current value both selected and with a signal "Current" badge', () => {
		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
				{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'blue hour lighting', sort_order: 1, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v1'
		});

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		const current = rows.find((r) => r.textContent?.includes('golden hour'));
		const other = rows.find((r) => r.textContent?.includes('blue hour'));

		expect(current?.className).toContain('sel');
		const badge = current?.querySelector('.picker-vbadge');
		expect(badge?.textContent).toContain('Current');
		expect(badge?.className).toContain('signal');
		expect(other?.querySelector('.picker-vbadge')).toBeNull();
	});

	it('marks the original current row with a neutral badge once a different row is picked', () => {
		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
				{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'blue hour lighting', sort_order: 1, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v1'
		});

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		rows.find((r) => r.textContent?.includes('blue hour'))!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		const refreshed = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		const current = refreshed.find((r) => r.textContent?.includes('golden hour'));
		const picked = refreshed.find((r) => r.textContent?.includes('blue hour'));

		expect(current?.className).not.toContain('sel');
		expect(current?.querySelector('.picker-vbadge')?.className).toContain('neutral');
		expect(picked?.className).toContain('sel');
		expect(picked?.querySelector('.picker-vcheck')).not.toBeNull();
	});

	it('scrolls the current row into view on open', () => {
		const scrollIntoView = vi.fn();
		Element.prototype.scrollIntoView = scrollIntoView;

		mountModal({
			values: [
				{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
				{ id: 'v2', category_id: 'c1', label: 'blue hour', value: 'blue hour lighting', sort_order: 1, created_at: '', updated_at: '' }
			],
			initialSelectedId: 'v2'
		});

		expect(scrollIntoView).toHaveBeenCalled();
	});

	it('marks the active category row in the tree', () => {
		mountModal({
			categories: [
				{ id: 'lighting', name: 'Lighting' },
				{ id: 'mood', name: 'Mood' }
			],
			activeCategoryId: 'lighting',
			values: [{ id: 'v1', category_id: 'c1', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' }],
			initialSelectedId: 'v1'
		});

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-tree-row'));
		const active = rows.find((r) => r.textContent?.includes('Lighting'));
		const other = rows.find((r) => r.textContent?.includes('Mood'));

		expect(active?.className).toContain('on');
		expect(other?.className).not.toContain('on');
	});
});
