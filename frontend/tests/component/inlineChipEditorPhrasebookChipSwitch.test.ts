// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { ChipData } from '$lib/types/segments';

vi.mock('$lib/services/api/index', () => ({
	api: {
		searchPhrasebook: vi.fn().mockResolvedValue({ success: true, data: { child_categories: [], values: [] } }),
		getFileURL: (fileId: string, size: string) => `/media/${size}/${fileId}`
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function chipData(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'lighting.mood',
		valueId: 'v1',
		label: 'golden hour',
		value: 'golden hour lighting',
		allValues: [
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		],
		shuffle: false,
		autoRegen: false,
		...overrides
	};
}

function mountEditor(value: string, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, variant: 'segment-composer', borderless: true, ...props }
	});
	return component;
}

function findButton(text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.trim() === text);
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('clicking a # phrasebook chip to change its value', () => {
	it('opens the browse modal with a Current card, the alternatives listed, current preselected, Replace as the action', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{ id: 'v1', category_path: 'lighting.mood', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
					{ id: 'v2', category_path: 'lighting.mood', label: 'blue hour', value: 'blue hour lighting', sort_order: 1, created_at: '', updated_at: '' }
				]
			}
		} as never);

		mountEditor('#lighting.mood', { chips: { 'chip-1': chipData() } });
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector<HTMLElement>('.phrase-chip .chip-main');
		expect(chip).not.toBeNull();
		chip!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();

		const current = document.querySelector('.pm-current');
		expect(current).not.toBeNull();
		expect(current!.textContent).toContain('golden hour');

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		expect(rows.length).toBe(2);
		const selected = rows.find((r) => r.className.includes('sel'));
		expect(selected?.textContent).toContain('golden hour');

		expect(findButton('Replace')).toBeTruthy();
		expect(findButton('Insert')).toBeFalsy();
	});

	it('Replace updates the chip value, leaving the marker text unchanged', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{ id: 'v1', category_path: 'lighting.mood', label: 'golden hour', value: 'golden hour lighting', sort_order: 0, created_at: '', updated_at: '' },
					{ id: 'v2', category_path: 'lighting.mood', label: 'blue hour', value: 'blue hour lighting', sort_order: 1, created_at: '', updated_at: '' }
				]
			}
		} as never);

		const onChange = vi.fn();
		mountEditor('#lighting.mood', { chips: { 'chip-1': chipData() } });
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.phrase-chip .chip-main')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		const other = rows.find((r) => r.textContent?.includes('blue hour'));
		other!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		findButton('Replace')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('#lighting.mood');
		expect(detail.chips['chip-1'].valueId).toBe('v2');
		expect(detail.chips['chip-1'].label).toBe('blue hour');

		const chipEl = target.querySelector('.phrase-chip');
		expect(chipEl?.textContent).toContain('blue hour');
	});

	it('Escape closes the modal without changing the chip', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: { child_categories: [], values: [] }
		} as never);

		const onChange = vi.fn();
		mountEditor('#lighting.mood', { chips: { 'chip-1': chipData() } });
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.phrase-chip .chip-main')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(onChange).not.toHaveBeenCalled();
		const chipEl = target.querySelector('.phrase-chip');
		expect(chipEl?.textContent).toContain('golden hour');
	});
});
