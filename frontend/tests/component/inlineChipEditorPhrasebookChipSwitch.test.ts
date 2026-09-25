// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { ChipData } from '$lib/types/segments';

vi.mock('$lib/services/api/index', () => ({
	api: {
		searchPhrasebook: vi.fn().mockResolvedValue({ success: true, data: { child_categories: [], values: [] } }),
		getFileURL: (fileId: string, size: string) => `/media/${size}/${fileId}`,
		toggleValueActive: vi.fn().mockResolvedValue({ success: true })
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

async function openSwitcher(mockedValues: Array<{ id: string; label: string; value: string }>, overrides: Partial<ChipData> = {}) {
	vi.mocked(api.searchPhrasebook).mockResolvedValue({
		success: true,
		data: {
			child_categories: [],
			values: mockedValues.map((v, index) => ({
				id: v.id,
				category_path: 'lighting.mood',
				label: v.label,
				value: v.value,
				sort_order: index,
				created_at: '',
				updated_at: ''
			}))
		}
	} as never);

	mountEditor('#lighting.mood', { chips: { 'chip-1': chipData(overrides) } });
	await new Promise((r) => setTimeout(r, 0));
	flushSync();

	const chip = target.querySelector<HTMLElement>('.phrase-chip .chip-main');
	chip!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
	flushSync();
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('clicking a # phrasebook chip to change its value', () => {
	it('opens the browse modal with the current value preselected, badged, and Save as the action', async () => {
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);

		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();
		expect(document.querySelector('.pm-current')).toBeNull();

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		expect(rows.length).toBe(2);
		const current = rows.find((r) => r.textContent?.includes('golden hour'));
		expect(current?.className).toContain('sel');
		expect(current?.querySelector('.picker-vbadge')?.textContent).toContain('Current');

		expect(findButton('Save')).toBeTruthy();
		expect(findButton('Insert')).toBeFalsy();
		expect(findButton('Replace')).toBeFalsy();
	});

	it('shows the footer behavior controls: segmented value mode, Shuffle now, Exclude from shuffles, Remove chip', async () => {
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);

		expect(findButton('Fixed value')).toBeTruthy();
		expect(findButton('Auto-shuffle')).toBeTruthy();
		expect(findButton('Shuffle now')).toBeTruthy();
		expect(findButton('Exclude from shuffles')).toBeTruthy();
		expect(findButton('Remove chip')).toBeTruthy();
	});

	it('hides Exclude from shuffles when the chip has no alternatives', async () => {
		await openSwitcher([{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' }], {
			allValues: [{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' }]
		});

		expect(findButton('Exclude from shuffles')).toBeFalsy();
	});

	it('Save updates the chip value, leaving the marker text unchanged', async () => {
		const onChange = vi.fn();
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);
		component!.$on?.('change', onChange);

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		const other = rows.find((r) => r.textContent?.includes('blue hour'));
		other!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		findButton('Save')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('#lighting.mood');
		expect(detail.chips['chip-1'].valueId).toBe('v2');
		expect(detail.chips['chip-1'].label).toBe('blue hour');

		const chipEl = target.querySelector('.phrase-chip');
		expect(chipEl?.textContent).toContain('blue hour');
	});

	it('picking a different row is pending — the chip only changes after Save', async () => {
		const onChange = vi.fn();
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);
		component!.$on?.('change', onChange);

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-vrow'));
		const other = rows.find((r) => r.textContent?.includes('blue hour'));
		other!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(onChange).not.toHaveBeenCalled();
		expect(target.querySelector('.phrase-chip')?.textContent).toContain('golden hour');
	});

	it('toggling Auto-shuffle and Saving round-trips shuffle into the chip data', async () => {
		const onChange = vi.fn();
		await openSwitcher(
			[
				{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
				{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
			],
			{ shuffle: false }
		);
		component!.$on?.('change', onChange);

		findButton('Auto-shuffle')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		findButton('Save')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.chips['chip-1'].shuffle).toBe(true);
	});

	it('Exclude from shuffles removes the current value from the chip\'s alternatives immediately', async () => {
		vi.mocked(api.toggleValueActive).mockResolvedValue({ success: true } as never);
		const onChange = vi.fn();
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);
		component!.$on?.('change', onChange);

		findButton('Exclude from shuffles')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(api.toggleValueActive).toHaveBeenCalledWith('v1', false);

		const detail = onChange.mock.calls.at(-1)![0].detail;
		const updatedChip = detail.chips['chip-1'];
		expect(updatedChip.allValues.some((v: { id: string }) => v.id === 'v1')).toBe(false);
		expect(updatedChip.valueId).toBe('v2');
	});

	it('Remove chip removes it immediately, without needing Save', async () => {
		const onChange = vi.fn();
		await openSwitcher([
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour lighting' }
		]);
		component!.$on?.('change', onChange);

		findButton('Remove chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(target.querySelector('.phrase-chip')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.chips['chip-1']).toBeUndefined();
	});

	it('Escape closes the modal without changing the chip', async () => {
		await openSwitcher([]);
		const onChange = vi.fn();
		component!.$on?.('change', onChange);

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(onChange).not.toHaveBeenCalled();
		const chipEl = target.querySelector('.phrase-chip');
		expect(chipEl?.textContent).toContain('golden hour');
	});
});

describe('the phrasebook chip, without the gear', () => {
	it('has no settings button — the chip body opens the editor directly', async () => {
		mountEditor('#lighting.mood', { chips: { 'chip-1': chipData() } });
		flushSync();

		expect(target.querySelector('.chip-config')).toBeNull();
	});

	it('opens the editor on Enter when the chip is focused', async () => {
		vi.mocked(api.searchPhrasebook).mockResolvedValue({
			success: true,
			data: { child_categories: [], values: [] }
		} as never);
		mountEditor('#lighting.mood', { chips: { 'chip-1': chipData() } });
		flushSync();

		const chipMain = target.querySelector<HTMLElement>('.phrase-chip .chip-main')!;
		chipMain.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
	});
});
