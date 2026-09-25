// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_videos', kind: 'video', label: 'Videos', token: '<Video @>' }
];

function mountEditor(value: string, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	return component;
}

function findButton(text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.includes(text));
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('clicking an @ resource chip to change its value', () => {
	it('opens the browse modal with the field\'s items, groups on the left, current one badged, Save as the action', async () => {
		mountEditor('@[references:a.png]', {
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: {
				references: [
					{ relative_path: 'a.png', name: 'a.png' },
					{ relative_path: 'b.png', name: 'b.png' }
				],
				reference_videos: []
			}
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector<HTMLElement>('.resource-chip');
		expect(chip).not.toBeNull();
		chip!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();
		expect(document.querySelector('.pm-current')).toBeNull();

		const groups = Array.from(document.querySelectorAll<HTMLElement>('.picker-tree-row'));
		expect(groups.some((g) => g.textContent?.includes('Pictures'))).toBe(true);
		expect(groups.some((g) => g.textContent?.includes('Videos'))).toBe(true);
		expect(groups.find((g) => g.textContent?.includes('Pictures'))?.className).toContain('on');

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-gitem'));
		expect(rows.length).toBe(2);
		const selected = rows.find((r) => r.className.includes('sel'));
		expect(selected?.textContent).toContain('Picture 1');

		expect(findButton('Save')).toBeTruthy();
		expect(findButton('Insert')).toBeFalsy();
	});

	it('shows Remove reference on the footer left', async () => {
		mountEditor('@[references:a.png]', {
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.resource-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(findButton('Remove reference')).toBeTruthy();
	});

	it('Save swaps the marker in place, byte-exact — pending until then', async () => {
		const onChange = vi.fn();
		mountEditor('a cat @[references:a.png] on a rug', {
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: {
				references: [
					{ relative_path: 'a.png', name: 'a.png' },
					{ relative_path: 'b.png', name: 'b.png' }
				]
			}
		});
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.resource-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-gitem'));
		const other = rows.find((r) => r.textContent?.includes('Picture 2'));
		other!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(onChange).not.toHaveBeenCalled();

		findButton('Save')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('a cat @[references:b.png] on a rug');

		const chipAfter = target.querySelector('.resource-chip');
		expect(chipAfter?.textContent).toContain('Picture 2');
	});

	it('Remove reference removes the marker immediately, without needing Save', async () => {
		const onChange = vi.fn();
		mountEditor('a cat @[references:a.png] on a rug', {
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.resource-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		findButton('Remove reference')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(target.querySelector('.resource-chip-container')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('a cat  on a rug');
	});

	it('Esc closes the modal without changing the marker', async () => {
		const onChange = vi.fn();
		mountEditor('@[references:a.png]', {
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: {
				references: [
					{ relative_path: 'a.png', name: 'a.png' },
					{ relative_path: 'b.png', name: 'b.png' }
				]
			}
		});
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.resource-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(onChange).not.toHaveBeenCalled();
		const container = target.querySelector<HTMLElement>('.resource-chip-container');
		expect(container?.dataset.resourceMarker).toBe('@[references:a.png]');
	});
});

describe('resource chip dangling state, without an inline remove button', () => {
	it('opens the editor straight to Remove reference', async () => {
		mountEditor('@[references:gone.png]', {
			resources: { 'res-1': { field: 'references', item_key: 'gone.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [] },
			resourceFieldLabels: { references: 'References' }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector<HTMLElement>('.resource-chip');
		expect(chip).not.toBeNull();
		expect(chip?.querySelector('.chip-config')).toBeNull();

		chip!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
		expect(findButton('Remove reference')).toBeTruthy();
	});

	it('removing a dangling chip from the modal drops its marker from the emitted value', async () => {
		const onChange = vi.fn();
		mountEditor('a cat @[references:gone.png] on a rug', {
			resources: { 'res-1': { field: 'references', item_key: 'gone.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [] }
		});
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		target.querySelector<HTMLElement>('.resource-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
		findButton('Remove reference')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(target.querySelector('.resource-chip-container')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('a cat  on a rug');
	});
});
