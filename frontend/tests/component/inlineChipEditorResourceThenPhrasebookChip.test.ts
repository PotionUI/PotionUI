// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { api } from '$lib/services/api/index';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }
];

const ITEM_KEY = '2026-09-19/01M0DDM0EAV03E51MVA03CBF8H/0.png';

interface EditorInstance {
	insertPhrasebookTrigger: () => void;
}

function mountEditor(value: string, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	return component as unknown as EditorInstance;
}

function typeAfterCaret(text: string) {
	const editorEl = target.querySelector('.inline-chip-editor')!;
	const textNode = document.createTextNode(text);
	editorEl.appendChild(textNode);
	const range = document.createRange();
	range.setStart(textNode, textNode.length);
	range.collapse(true);
	const selection = window.getSelection()!;
	selection.removeAllRanges();
	selection.addRange(range);
	editorEl.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	return textNode;
}

function clickRow(cssClass: string, text: string) {
	const rows = Array.from(document.querySelectorAll<HTMLElement>(`.picker-row${cssClass}`));
	const row = rows.find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('InlineChipEditor resource marker followed by a phrasebook chip', () => {
	it('inserting a phrasebook chip after a resource marker + space leaves the marker intact', async () => {
		vi.spyOn(api, 'searchPhrasebook').mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{
						id: 'v1',
						category_path: 'Look description',
						label: 'Look description',
						value: 'A slender woman in her early 20s wearing a black blazer.'
					}
				]
			}
		} as never);

		mountEditor(`@[references:${ITEM_KEY}]`, {
			resources: { 'res-1': { field: 'references', item_key: ITEM_KEY } },
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: ITEM_KEY }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		expect(target.querySelector('.resource-chip-container')).not.toBeNull();

		typeAfterCaret(' #Look description');
		expect(document.querySelector('.phrasebook-picker')).not.toBeNull();

		await new Promise((r) => setTimeout(r, 250));
		flushSync();

		clickRow('.phrase-value', 'Look description');

		const container = target.querySelector<HTMLElement>('.resource-chip-container');
		expect(container?.dataset.resourceMarker).toBe(`@[references:${ITEM_KEY}]`);

		const chip = target.querySelector('.phrase-chip');
		expect(chip).not.toBeNull();
	});

	it('extractContentFromDOM after the insert still reports the marker byte-for-byte', async () => {
		vi.spyOn(api, 'searchPhrasebook').mockResolvedValue({
			success: true,
			data: {
				child_categories: [],
				values: [
					{
						id: 'v1',
						category_path: 'Look description',
						label: 'Look description',
						value: 'A slender woman in her early 20s wearing a black blazer.'
					}
				]
			}
		} as never);

		const onChange = vi.fn();
		mountEditor(`@[references:${ITEM_KEY}]`, {
			resources: { 'res-1': { field: 'references', item_key: ITEM_KEY } },
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: ITEM_KEY }] }
		});
		component!.$on?.('change', onChange);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		typeAfterCaret(' #Look description');
		await new Promise((r) => setTimeout(r, 250));
		flushSync();

		clickRow('.phrase-value', 'Look description');

		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe(`@[references:${ITEM_KEY}] #[Look description]`);
	});
});
