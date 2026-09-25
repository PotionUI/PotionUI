// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { RESOURCE_NUMBERING_CONTEXT_KEY, type ResourceNumbering } from '$lib/utils/resourceNumbering';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

const resourceFieldValues = {
	references: [
		{ relative_path: 'a.png', name: 'a.png' },
		{ relative_path: 'b.png', name: 'b.png' },
		{ relative_path: 'c.png', name: 'c.png' }
	]
};

function numberingFor(positions: Record<string, number>): ResourceNumbering {
	return { positionFor: (field, itemKey) => (field === 'references' ? positions[itemKey] ?? null : null) };
}

function mountEditor(props: Record<string, unknown>, numbering?: ResourceNumbering | null) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const context = numbering !== undefined ? new Map([[RESOURCE_NUMBERING_CONTEXT_KEY, writable(numbering)]]) : undefined;
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		context,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	return component;
}

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
});

describe('InlineChipEditor resource numbering context', () => {
	it('numbers chips per-shot, citing C ahead of the uncited B', async () => {
		mountEditor(
			{
				value: '@[references:c.png] and @[references:a.png]',
				resources: {
					'res-1': { field: 'references', item_key: 'c.png' },
					'res-2': { field: 'references', item_key: 'a.png' }
				},
				promptResources: specs,
				resourceFieldValues
			},
			numberingFor({ 'a.png': 1, 'c.png': 2 })
		);
		await settle();

		const chips = Array.from(target.querySelectorAll('.resource-chip'));
		expect(chips.map((c) => c.textContent?.trim())).toEqual(['Picture 2', 'Picture 1']);
	});

	it('renumbers live once a marker citing B appears', async () => {
		const store = writable<ResourceNumbering | null>(numberingFor({ 'a.png': 1, 'c.png': 2 }));
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			context: new Map([[RESOURCE_NUMBERING_CONTEXT_KEY, store]]),
			props: {
				value: '@[references:c.png] and @[references:a.png]',
				chips: {},
				resources: {
					'res-1': { field: 'references', item_key: 'c.png' },
					'res-2': { field: 'references', item_key: 'a.png' }
				},
				variant: 'segment-composer',
				borderless: true,
				promptResources: specs,
				resourceFieldValues
			}
		});
		await settle();
		expect(Array.from(target.querySelectorAll('.resource-chip')).map((c) => c.textContent?.trim())).toEqual([
			'Picture 2',
			'Picture 1'
		]);

		store.set(numberingFor({ 'a.png': 1, 'b.png': 2, 'c.png': 3 }));
		await settle();
		expect(Array.from(target.querySelectorAll('.resource-chip')).map((c) => c.textContent?.trim())).toEqual([
			'Picture 3',
			'Picture 1'
		]);
	});

	it('falls back to the form-wide array position with no numbering context (Generate page)', async () => {
		mountEditor({
			value: '@[references:c.png]',
			resources: { 'res-1': { field: 'references', item_key: 'c.png' } },
			promptResources: specs,
			resourceFieldValues
		});
		await settle();

		const chip = target.querySelector('.resource-chip');
		expect(chip?.textContent).toContain('Picture 3');
	});
});
