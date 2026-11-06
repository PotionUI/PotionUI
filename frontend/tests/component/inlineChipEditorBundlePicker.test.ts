// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	original.api.getModelById = vi.fn().mockResolvedValue({ success: true, data: { model: { name: 'Face Bundle' } } }) as never;
	original.api.getPromptResourceMembers = vi.fn().mockImplementation(async (source: string) => ({
		success: true,
		data: { members: source === 'kit' ? [{ kind: 'image', name: null }, { kind: 'video', name: null }] : [] }
	})) as never;
	return original;
});

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { resetBundleDirectories } = await import('$lib/stores/bundleDirectory');

let target: HTMLDivElement;

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_kits', kind: 'image', label: 'Kits', token: '<Picture @>', source: 'kit' },
	{ field: 'reference_kits', kind: 'video', label: 'Kits', token: '<Video @>', source: 'kit' }
];

const values = {
	references: [{ relative_path: 'a.png', name: 'a.png' }],
	reference_kits: [
		{ model: 'model:01X', strength: 1, copies: 1 },
		{ model: 'model:01Z', strength: 0, copies: 1 }
	]
};

async function openPicker(promptResources: PromptResourceSpec[], resourceFieldValues: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const onChange = vi.fn();
	const component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, promptResources, resourceFieldValues }
	});
	component.$on?.('change', onChange);
	await vi.waitFor(() => {
		flushSync();
		(component as unknown as { insertResourceTrigger: () => void }).insertResourceTrigger?.();
		flushSync();
		expect(document.querySelector('.resource-picker')).not.toBeNull();
	});
	return onChange;
}

function clickRow(text: string) {
	const row = Array.from(document.querySelectorAll<HTMLElement>('.picker-row')).find((r) => r.textContent?.includes(text));
	row?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	resetBundleDirectories();
});

describe('InlineChipEditor @ picker with Kits', () => {
	it('lists Kits as one group, by name with a kind badge, and cites one as a named chip', async () => {
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
		expect(groups.match(/Kits/g)).toHaveLength(1);
		expect(groups).toContain('1 available');

		clickRow('Kits');
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('.resource-picker')!.textContent).toContain('Face Bundle');
		});
		const menu = document.querySelector('.resource-picker')!.textContent || '';
		expect(menu).toContain('Picture + Video');
		expect(menu).not.toContain('01Z');

		clickRow('Face Bundle');
		const detail = onChange.mock.calls.at(-1)![0].detail;
		expect(detail.value).toBe('@[reference_kits:model:01X]');
		await vi.waitFor(() => {
			flushSync();
			expect(target.querySelector('.resource-chip')?.textContent).toContain('Face Bundle');
		});
	});

	it('lists a plugin source group with the badge and label the backend serves for it', async () => {
		const stackSpecs: PromptResourceSpec[] = [
			{ field: 'stacks', kind: 'image', label: 'Stacks', token: '<Picture @>', source: 'stack', source_label: 'Stack', source_badge: 'Stk' }
		];
		const onChange = await openPicker(stackSpecs, { stacks: [{ model: 'model:01S', strength: 1 }] });

		clickRow('Stacks');
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('.resource-picker')!.textContent).toContain('Face Bundle');
		});
		expect(document.querySelector('.resource-picker')!.textContent).toContain('Stk');

		clickRow('Face Bundle');
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe('@[stacks:model:01S]');
	});

	it('says a plugin source group has nothing selected yet in its own words', async () => {
		const stackSpecs: PromptResourceSpec[] = [
			{ field: 'stacks', kind: 'image', label: 'Stacks', token: '<Picture @>', source: 'stack', source_label: 'Stack', source_badge: 'Stk' }
		];
		await openPicker(stackSpecs, { stacks: [] });
		expect(document.querySelector('.resource-picker')!.textContent).toContain('No Stacks selected yet');
	});
});
