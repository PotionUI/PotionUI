// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { ChipData } from '$lib/types/segments';

const { default: InlineChip } = await import('$lib/components/InlineChip.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function makeChip(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'lighting.mood',
		valueId: 'v1',
		label: 'golden hour',
		value: 'golden hour lighting, warm rim light',
		allValues: [
			{ id: 'v1', label: 'golden hour', value: 'golden hour lighting, warm rim light', preview_file_id: 'file-1' },
			{ id: 'v2', label: 'blue hour', value: 'blue hour, cool ambient light' }
		],
		shuffle: false,
		autoRegen: false,
		...overrides
	};
}

function mount(data: ChipData, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChip as never,
		target,
		props: { data, variant: 'segment-composer', ...props }
	});
	flushSync();
	return target;
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('phrasebook chip settings popover — current value card', () => {
	it('shows the current value text and its preview image when the settings popover opens', () => {
		mount(makeChip());
		target.querySelector<HTMLElement>('.chip-config')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();

		const card = document.querySelector('.current-value-card');
		expect(card).not.toBeNull();
		expect(card!.textContent).toContain('golden hour');
		expect(card!.textContent).toContain('golden hour lighting, warm rim light');
		expect(card!.textContent).toContain('lighting.mood');

		const img = card!.querySelector('img');
		expect(img).not.toBeNull();
		expect(img!.getAttribute('src')).toContain('file-1');
	});

	it('omits the thumbnail when the current value has no preview image', () => {
		mount(
			makeChip({
				valueId: 'v2',
				label: 'blue hour',
				value: 'blue hour, cool ambient light'
			})
		);
		target.querySelector<HTMLElement>('.chip-config')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();

		const card = document.querySelector('.current-value-card');
		expect(card).not.toBeNull();
		expect(card!.querySelector('img')).toBeNull();
	});
});

describe('phrasebook chip — clicking opens the switcher instead of the old modal', () => {
	it('calls onSwitch (not the value-chooser modal) when the chip body is clicked', () => {
		const onSwitch = vi.fn();
		mount(makeChip(), { onSwitch });
		target.querySelector<HTMLElement>('.chip-main')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();

		expect(onSwitch).toHaveBeenCalledTimes(1);
		expect(document.querySelector('[role="dialog"]')).toBeNull();
	});

	it('routes the settings popover\'s "Change value…" button through the same onSwitch, closing the popover', () => {
		const onSwitch = vi.fn();
		mount(makeChip(), { onSwitch });
		target.querySelector<HTMLElement>('.chip-config')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();
		expect(document.querySelector('.phrase-popover')).not.toBeNull();

		const changeButton = Array.from(document.querySelectorAll<HTMLElement>('.small-button')).find((b) =>
			b.textContent?.includes('Change value')
		);
		changeButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(onSwitch).toHaveBeenCalledTimes(1);
		expect(document.querySelector('.phrase-popover')).toBeNull();
	});

	it('does nothing when disabled', () => {
		const onSwitch = vi.fn();
		mount(makeChip(), { onSwitch, disabled: true });
		target.querySelector<HTMLElement>('.chip-main')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		flushSync();

		expect(onSwitch).not.toHaveBeenCalled();
	});
});
