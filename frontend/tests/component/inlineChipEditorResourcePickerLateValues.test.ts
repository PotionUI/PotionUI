// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { RESOURCE_NUMBERING_CONTEXT_KEY, type ResourceNumbering } from '$lib/utils/resourceNumbering';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

const items = [
	{ relative_path: 'a.png', name: 'a.png' },
	{ relative_path: '0-edit.png', name: '0-edit.png' }
];

interface EditorInstance {
	$set: (props: Record<string, unknown>) => void;
	insertResourceTrigger: () => void;
}

function mountEditor(props: Record<string, unknown>, numbering?: ResourceNumbering) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const context = numbering ? new Map([[RESOURCE_NUMBERING_CONTEXT_KEY, writable(numbering)]]) : undefined;
	return createClassComponent({
		component: InlineChipEditor as never,
		target,
		context,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, ...props }
	}) as unknown as EditorInstance;
}

function clickRow(text: string) {
	const row = Array.from(document.querySelectorAll<HTMLElement>('.picker-row')).find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
});

describe('InlineChipEditor @ picker with late field values', () => {
	it('shows the item count once the field values arrive after mount', () => {
		const editor = mountEditor({ promptResources: specs, resourceFieldValues: {} });
		editor.$set({ resourceFieldValues: { references: items } });
		flushSync();
		editor.insertResourceTrigger();
		flushSync();

		const text = document.querySelector('.resource-picker')!.textContent || '';
		expect(text).toContain('2 available');
		expect(text).not.toContain('added yet');
	});

	it('labels menu items by file name so no two items show the same number', () => {
		const numbering: ResourceNumbering = {
			positionFor: () => null,
			positionIfAdded: () => 1
		};
		const editor = mountEditor({ promptResources: specs, resourceFieldValues: { references: items } }, numbering);
		editor.insertResourceTrigger();
		flushSync();
		clickRow('Pictures');

		const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row')).map((r) => r.textContent || '');
		expect(rows.some((r) => r.includes('a.png'))).toBe(true);
		expect(rows.some((r) => r.includes('0-edit.png'))).toBe(true);
		expect(rows.some((r) => /Picture \d/.test(r))).toBe(false);
	});
});
