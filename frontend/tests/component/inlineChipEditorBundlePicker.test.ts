// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	original.api.getModelById = vi.fn().mockResolvedValue({ success: true, data: { model: { name: 'Face Bundle' } } }) as never;
	original.api.getRefModMembers = vi
		.fn()
		.mockResolvedValue({ success: true, data: { members: [{ kind: 'image', name: null }, { kind: 'video', name: null }] } }) as never;
	return original;
});

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { resetRefModDirectory } = await import('$lib/stores/refModDirectory');

let target: HTMLDivElement;

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_mods', kind: 'image', label: 'RefMods', token: '<Picture @>', source: 'refmod' },
	{ field: 'reference_mods', kind: 'video', label: 'RefMods', token: '<Video @>', source: 'refmod' }
];

const values = {
	references: [{ relative_path: 'a.png', name: 'a.png' }],
	reference_mods: [
		{ model: 'model:01X', strength: 1, copies: 1 },
		{ model: 'model:01Z', strength: 0, copies: 1 }
	]
};

function clickRow(text: string) {
	const row = Array.from(document.querySelectorAll<HTMLElement>('.picker-row')).find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	resetRefModDirectory();
});

describe('InlineChipEditor @ picker with RefMods', () => {
	it('lists RefMods as one group, by name with a kind badge, and cites one as a named chip', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		const onChange = vi.fn();
		const component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, promptResources: specs, resourceFieldValues: values }
		});
		component.$on?.('change', onChange);
		await vi.waitFor(() => {
			flushSync();
			(component as unknown as { insertResourceTrigger: () => void }).insertResourceTrigger?.();
			flushSync();
			expect(document.querySelector('.resource-picker')).not.toBeNull();
		});

		const groups = document.querySelector('.resource-picker')!.textContent || '';
		expect(groups.match(/RefMods/g)).toHaveLength(1);
		expect(groups).toContain('1 available');

		clickRow('RefMods');
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('.resource-picker')!.textContent).toContain('Face Bundle');
		});
		const menu = document.querySelector('.resource-picker')!.textContent || '';
		expect(menu).toContain('Picture + Video');
		expect(menu).not.toContain('01Z');

		clickRow('Face Bundle');
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('@[reference_mods:model:01X]');
		await vi.waitFor(() => {
			flushSync();
			expect(target.querySelector('.resource-chip')?.textContent).toContain('Face Bundle');
		});
	});
});
