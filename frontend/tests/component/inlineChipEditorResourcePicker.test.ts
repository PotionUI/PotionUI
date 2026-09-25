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
	{ field: 'reference_videos', kind: 'video', token: '<Video @>' }
];

interface EditorInstance {
	insertResourceTrigger: () => void;
}

function mountEditor(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, ...props }
	});
	return component as unknown as EditorInstance;
}

function clickRow(text: string) {
	const rows = Array.from(document.querySelectorAll<HTMLElement>('.picker-row'));
	const row = rows.find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('InlineChipEditor @ prompt-resource picker', () => {
	it('opens on @ and lists the mode\'s mapped resource groups with item counts', () => {
		const editor = mountEditor({
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }], reference_videos: [] }
		});
		editor.insertResourceTrigger();
		flushSync();

		expect(document.querySelector('.resource-picker')).not.toBeNull();
		const text = document.querySelector('.resource-picker')!.textContent || '';
		expect(text).toContain('Pictures');
		expect(text).toContain('2 available');
		expect(text).toContain('Videos');
		expect(text).toContain('No videos added yet');
	});

	it('does not navigate into an empty group', () => {
		const editor = mountEditor({
			promptResources: specs,
			resourceFieldValues: { references: [], reference_videos: [] }
		});
		editor.insertResourceTrigger();
		flushSync();

		clickRow('Pictures');
		expect(document.querySelector('.resource-picker')!.textContent).toContain('Pictures');
		expect(document.querySelector('.resource-picker')!.textContent).not.toContain('Picture 1');
	});

	it('lists the field\'s current items with resolved handle labels after picking a group', () => {
		const editor = mountEditor({
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png', name: 'a.png' }, { relative_path: 'b.png', name: 'b.png' }] }
		});
		editor.insertResourceTrigger();
		flushSync();
		clickRow('Pictures');

		const text = document.querySelector('.resource-picker')!.textContent || '';
		expect(text).toContain('Picture 1');
		expect(text).toContain('Picture 2');
	});

	it('inserting an item is a single insert that closes the picker and renders a resolved chip', () => {
		const editor = mountEditor({
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png', name: 'a.png', url: '/a.png' }] }
		});
		editor.insertResourceTrigger();
		flushSync();
		clickRow('Pictures');
		clickRow('Picture 1');

		expect(document.querySelector('.resource-picker')).toBeNull();
		const container = target.querySelector<HTMLElement>('.resource-chip-container');
		expect(container).not.toBeNull();
		expect(container?.contentEditable).toBe('false');

		const chip = target.querySelector('.resource-chip');
		expect(chip).not.toBeNull();
		expect(chip?.textContent).toContain('Picture 1');
	});

	it('dispatches change with the marker text and a resources map entry on insert', () => {
		const onChange = vi.fn();
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			props: {
				value: '',
				chips: {},
				variant: 'segment-composer',
				borderless: true,
				promptResources: specs,
				resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
			}
		});
		component.$on?.('change', onChange);

		(component as unknown as EditorInstance).insertResourceTrigger();
		flushSync();
		clickRow('Pictures');
		clickRow('Picture 1');

		expect(onChange).toHaveBeenCalled();
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('@[references:a.png]');
		expect(Object.values(detail.resources)).toEqual([{ field: 'references', item_key: 'a.png' }]);
	});
});

describe('InlineChipEditor resource chip dangling state', () => {
	it('renders a dangling chip, opening the browse modal to remove it (see inlineChipEditorResourceChipSwitch.test.ts)', async () => {
		mountEditor({
			value: '@[references:gone.png]',
			resources: { 'res-1': { field: 'references', item_key: 'gone.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [] },
			resourceFieldLabels: { references: 'References' }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector('.resource-chip');
		expect(chip).not.toBeNull();
		expect(chip?.className).toContain('text-danger');
		expect(target.querySelector('button[aria-label="Remove reference"]')).toBeNull();
	});

	it('renders a normal (non-dangling) chip when the item is present', async () => {
		mountEditor({
			value: '@[references:a.png]',
			resources: { 'res-1': { field: 'references', item_key: 'a.png' } },
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const chip = target.querySelector('.resource-chip');
		expect(chip).not.toBeNull();
		expect(chip?.className).not.toContain('text-danger');
		expect(chip?.textContent).toContain('Picture 1');
	});
});
