// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }
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
	return Array.from(document.querySelectorAll<HTMLElement>('button')).find((b) => b.textContent?.trim() === text);
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('clicking an @ resource chip to change its value', () => {
	it('opens the browse modal with a Current card and the field\'s items, current one marked, Replace as the action', async () => {
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
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector<HTMLElement>('.resource-chip');
		expect(chip).not.toBeNull();
		chip!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();

		const current = document.querySelector('.pm-current');
		expect(current).not.toBeNull();
		expect(current!.textContent).toContain('Picture 1');

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-gitem'));
		expect(rows.length).toBe(2);
		const selected = rows.find((r) => r.className.includes('sel'));
		expect(selected?.textContent).toContain('Picture 1');

		expect(findButton('Replace')).toBeTruthy();
		expect(findButton('Insert')).toBeFalsy();
	});

	it('Replace swaps the marker in place, byte-exact', async () => {
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

		findButton('Replace')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('a cat @[references:b.png] on a rug');

		const chipAfter = target.querySelector('.resource-chip');
		expect(chipAfter?.textContent).toContain('Picture 2');
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
